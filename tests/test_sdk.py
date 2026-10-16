import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from neuro_desktop_sdk import Heartbeat, IntegrationClient, IntegrationManifest, ManifestError, Permission, SDKError, ViewerClient, load_manifest, validate_manifest
from neuro_desktop_sdk.client import MAX_RESPONSE_BYTES, validate_server

TOKEN = "fixture-integration-token-0123456789"


def manifest():
    return IntegrationManifest(
        id="private-minecraft", name="Minecraft bridge", version="1.0.0", kind="game",
        permissions=(Permission("discord.viewer", "Publish selected, viewer-safe activity", False),),
        game_ids=("minecraft",), neuro_api={"game_name": "Minecraft"},
    ).to_dict()


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_is_detached(self):
        original = manifest()
        result = validate_manifest(original)
        result["permissions"][0]["description"] = "changed"
        self.assertNotEqual(result, original)

    def test_malformed_manifests_and_ids(self):
        mutations = [
            ("id", "../admin"), ("id", "WrongCase"), ("id", "x" * 65),
            ("schema_version", True), ("schema_version", 2), ("kind", []),
            ("game_ids", ["minecraft", "minecraft"]), ("game_ids", ["../../etc"]),
            ("name", "a\nb"), ("name", " "), ("settings_schema", []),
            ("settings_schema", {"type": "string"}), ("neuro_api", {"game_name": ""}),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                body = manifest()
                body[key] = value
                with self.assertRaises(ManifestError):
                    validate_manifest(body)
        body = manifest()
        body["permissions"] *= 2
        with self.assertRaises(ManifestError):
            validate_manifest(body)
        body = manifest()
        body["permissions"][0]["required"] = "yes"
        with self.assertRaises(ManifestError):
            validate_manifest(body)

    def test_caps_nonfinite_and_duplicate_json(self):
        for schema in ({"type": "object", "description": "x" * 65536}, {"type": "object", "default": float("nan")}):
            body = manifest()
            body["settings_schema"] = schema
            with self.assertRaises(ManifestError):
                validate_manifest(body)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(manifest()))
            self.assertEqual(load_manifest(path)["id"], "private-minecraft")
            path.write_text('{"schema_version":1,"schema_version":2}')
            with self.assertRaises(ManifestError):
                load_manifest(path)
            path.write_text(" " * 65537)
            with self.assertRaises(ManifestError):
                load_manifest(path)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.reply = {"ok": True}
        self.code = 200
        self.truncated = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.handle_request()

            def do_GET(self):
                self.handle_request()

            def handle_request(self):
                size = int(self.headers.get("Content-Length", 0))
                content = self.rfile.read(size)
                owner.seen.append({"method": self.command, "path": self.path, "headers": self.headers, "body": json.loads(content) if content else None})
                if owner.truncated:
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b'20\r\n{"ok":')
                    self.close_connection = True
                    return
                self.send_response(owner.code)
                if owner.code == 302:
                    self.send_header("Location", owner.origin + "/redirected")
                self.end_headers()
                payload = owner.reply
                self.wfile.write(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.client = IntegrationClient("private-minecraft", TOKEN, server=self.origin)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_exact_scoped_requests_and_readonly_viewer(self):
        self.client.heartbeat("busy", "Loading world")
        self.client.activity(actor="evil", kind="discord.message", summary="Sent a curated public reply")
        self.client.hide()
        viewer = ViewerClient("private-minecraft", TOKEN, server=self.origin)
        viewer.snapshot()
        self.assertEqual([(x["method"], x["path"]) for x in self.seen], [
            ("POST", "/api/integrations/private-minecraft/heartbeat"),
            ("POST", "/api/integrations/private-minecraft/activity"),
            ("POST", "/api/integrations/private-minecraft/hide"),
            ("GET", "/api/viewer/private-minecraft"),
        ])
        self.assertEqual(self.seen[0]["body"], {"state": "busy", "message": "Loading world"})
        self.assertEqual(self.seen[1]["body"]["actor"], "evil")
        for request in self.seen[:3]:
            self.assertEqual(request["headers"]["X-ND-Integration-Token"], TOKEN)
            self.assertIsNone(request["headers"].get("X-ND-Token"))
            self.assertNotIn(TOKEN, request["path"])
        self.assertEqual(self.seen[-1]["headers"]["X-ND-Viewer-Token"], TOKEN)
        self.assertIsNone(self.seen[-1]["headers"].get("X-ND-Integration-Token"))
        self.assertFalse(hasattr(viewer, "activity"))
        viewer.close()

    def test_scoped_configuration_is_readonly_and_contains_no_operator_header(self):
        self.reply = {"ok": True, "id": "private-minecraft", "manifest": manifest(), "settings": {"publish_curated_activity": False}, "grants": {"discord.viewer": False}, "run_allowed": True}
        response = self.client.configuration()
        self.assertFalse(response["settings"]["publish_curated_activity"])
        self.assertFalse(response["grants"]["discord.viewer"])
        request = self.seen[-1]
        self.assertEqual((request["method"], request["path"]), ("GET", "/api/integrations/private-minecraft/config"))
        self.assertEqual(request["headers"]["X-ND-Integration-Token"], TOKEN)
        self.assertIsNone(request["headers"].get("X-ND-Operator-Token"))
        self.assertIsNone(request["body"])

    def test_invalid_calls_never_reach_server(self):
        for kwargs in [{"actor": "operator", "kind": "status", "summary": "safe"}, {"actor": "neuro", "kind": "discord.token", "summary": "safe"}, {"actor": "neuro", "kind": "status", "summary": "a\nb"}, {"actor": "neuro", "kind": "status", "summary": "a" * 501}]:
            with self.assertRaises(ValueError):
                self.client.activity(**kwargs)
        with self.assertRaises(ValueError):
            self.client.heartbeat("enabled")
        self.assertEqual(self.seen, [])

    def test_redirect_denied_and_errors_do_not_expose_token(self):
        self.code = 302
        with self.assertRaises(SDKError) as caught:
            self.client.heartbeat()
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(len(self.seen), 1)
        self.code = 403
        self.reply = {"error": TOKEN}
        with self.assertRaises(SDKError) as caught:
            self.client.heartbeat()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(caught.exception.status, 403)

    def test_bounded_invalid_response_and_closed_client(self):
        for reply in (b"not json", [], b" " * (MAX_RESPONSE_BYTES + 1)):
            self.reply = reply
            with self.assertRaises(SDKError):
                self.client.heartbeat()
        self.client.close()
        with self.assertRaises(SDKError):
            self.client.heartbeat()

    def test_truncated_chunked_response_is_retryable_without_exposing_credentials(self):
        self.truncated = True
        with self.assertRaises(SDKError) as caught:
            self.client.heartbeat()
        self.assertIn("incomplete", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        self.truncated = False
        self.assertTrue(self.client.heartbeat()["ok"])

    def test_proxy_environment_cannot_receive_scoped_header(self):
        with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "no_proxy": "", "NO_PROXY": ""}):
            client = IntegrationClient("fixture", TOKEN, server=self.origin)
            self.assertTrue(client.heartbeat()["ok"])
            client.close()

    def test_heartbeat_shutdown_hides_before_offline(self):
        heartbeat = Heartbeat(self.client).start()
        heartbeat.update("busy", "Loading")
        start = time.monotonic()
        heartbeat.close()
        self.assertLess(time.monotonic() - start, 2)
        self.assertFalse(heartbeat._thread.is_alive())
        self.assertEqual(self.seen[-2]["path"], "/api/integrations/private-minecraft/hide")
        self.assertEqual(self.seen[-1]["body"], {"state": "offline"})
        heartbeat.close()

    def test_background_heartbeat_recovers_without_overlapping_workers(self):
        heartbeat = Heartbeat(self.client, interval=1).start()
        try:
            self.code = 503
            deadline = time.monotonic() + 3
            while heartbeat.last_error is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsInstance(heartbeat.last_error, SDKError)
            self.code = 200
            self.reply = {"ok": True, "settings": {"publish_curated_activity": False}, "grants": {}}
            deadline = time.monotonic() + 3
            while heartbeat.last_error is not None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsNone(heartbeat.last_error)
            self.assertFalse(heartbeat.last_response["settings"]["publish_curated_activity"])
        finally:
            self.code = 200
            heartbeat.close()


class URLTests(unittest.TestCase):
    def test_urls_tokens_and_timeouts(self):
        self.assertEqual(validate_server("http://[::1]:8300/"), "http://[::1]:8300")
        self.assertEqual(validate_server("https://example.org", allow_remote=True), "https://example.org")
        for url in ("http://token@127.0.0.1", "http://127.0.0.1?a=b", "http://127.0.0.1#secret", "http://127.0.0.1/api", "http://127.0.0.1:0", "http://127.0.0.1:99999", "http://127.0.0.1\n", "https://example.org", "file:///tmp/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_server(url)
        with self.assertRaises(ValueError):
            validate_server("http://example.org", allow_remote=True)
        for token in ("a\nb", "short", TOKEN + "\r", "ü" * 20):
            with self.assertRaises(ValueError):
                IntegrationClient("fixture", token)
        with self.assertRaises(ValueError):
            IntegrationClient("../fixture", TOKEN)
        with self.assertRaises(ValueError):
            IntegrationClient("fixture", TOKEN, timeout=True)


if __name__ == "__main__":
    unittest.main()

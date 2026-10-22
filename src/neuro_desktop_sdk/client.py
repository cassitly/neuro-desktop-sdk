"""Typed, scoped HTTP helpers; no administrator token or game-API transport."""

from __future__ import annotations

import ipaddress
from http.client import HTTPException
import json
import re
import socket
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .manifest import clean_text, validate_id

Actor = Literal["neuro", "evil"]
State = Literal["ready", "busy", "offline"]
ActivityKind = Literal["status", "discord.channel", "discord.message", "discord.voice"]
ACTIVITY_KINDS = {"status", "discord.channel", "discord.message", "discord.voice"}
MAX_RESPONSE_BYTES = 256 * 1024
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~-]{16,256}\Z", re.ASCII)


class SDKError(RuntimeError):
    """A bounded request failed. Exception text never includes a token/body."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_server(server: str, *, allow_remote: bool = False) -> str:
    if not isinstance(server, str) or not server or any(ord(c) <= 32 for c in server):
        raise ValueError("server must be a clean HTTP(S) origin")
    try:
        parsed = urlsplit(server)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("server has an invalid host or port") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("server must be an HTTP(S) origin")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("server cannot contain credentials, paths, queries or fragments")
    if port == 0:
        raise ValueError("server port must be between 1 and 65535")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if not loopback and (not allow_remote or parsed.scheme != "https"):
        raise ValueError("remote servers require HTTPS and allow_remote=True")
    return f"{parsed.scheme}://{parsed.netloc}"


class _ScopedClient:
    def __init__(self, integration_id: str, token: str, *, server: str = "http://127.0.0.1:8300", timeout: float = 8, allow_remote: bool = False):
        self.integration_id = validate_id(integration_id)
        self.server = validate_server(server, allow_remote=allow_remote)
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("token must be a scoped token of 16–256 URL-safe ASCII characters")
        if type(timeout) not in (float, int) or not 0 < timeout <= 30:
            raise ValueError("timeout must be greater than zero and at most 30 seconds")
        self._token = token
        self.timeout = timeout
        # A loopback request must not leak a scoped token to an ambient proxy.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, path: str, header: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._token:
            raise SDKError("This scoped client has been closed")
        data = None if body is None else json.dumps(body, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if data is not None and len(data) > 64 * 1024:
            raise ValueError("request cannot exceed 64 KiB")
        request = Request(self.server + path, data=data, headers={header: self._token, "Accept": "application/json", "Content-Type": "application/json"}, method="GET" if body is None else "POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            exc.close()
            raise SDKError(f"Neurcro denied the request (HTTP {exc.code}); check this integration's enablement and grants", status=exc.code) from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise SDKError("Cannot reach Neurcro within the configured timeout") from None
        except HTTPException:
            # A truncated chunked response raises IncompleteRead here rather
            # than OSError. Keep it within the heartbeat worker's retry contract.
            raise SDKError("Neurcro returned an incomplete or invalid HTTP response") from None
        if len(content) > MAX_RESPONSE_BYTES:
            raise SDKError("Neurcro response exceeded 256 KiB")
        try:
            result = json.loads(content)
        except (ValueError, UnicodeError, RecursionError):
            raise SDKError("Neurcro returned invalid JSON") from None
        if not isinstance(result, dict):
            raise SDKError("Neurcro response must be an object")
        return result

    def close(self) -> None:
        """Forget the scoped token. Stop heartbeat helpers before closing."""
        self._token = ""


class IntegrationClient(_ScopedClient):
    """Publish lifecycle and already-curated viewer activity for one registration.

    This client has no import/install, token-minting, policy or self-grant API.
    Register the manifest and approve permissions in Neurcro first.
    """

    def configuration(self) -> dict[str, Any]:
        """Read your manifest, approved non-secret settings and effective grants."""
        return self._request(f"/api/integrations/{self.integration_id}/config", "X-ND-Integration-Token")

    def heartbeat(self, state: State = "ready", message: str | None = None) -> dict[str, Any]:
        if not isinstance(state, str) or state not in {"ready", "busy", "offline"}:
            raise ValueError("state must be ready, busy or offline")
        body: dict[str, Any] = {"state": state}
        if message is not None:
            body["message"] = clean_text(message, "message", 500)
        return self._request(f"/api/integrations/{self.integration_id}/heartbeat", "X-ND-Integration-Token", body)

    def activity(self, *, actor: Actor, kind: ActivityKind, summary: str) -> dict[str, Any]:
        if not isinstance(actor, str) or actor not in {"neuro", "evil"}:
            raise ValueError("actor must be neuro or evil")
        if not isinstance(kind, str) or kind not in ACTIVITY_KINDS:
            raise ValueError("unsupported activity kind")
        return self._request(f"/api/integrations/{self.integration_id}/activity", "X-ND-Integration-Token", {"actor": actor, "kind": kind, "summary": clean_text(summary, "summary", 500)})

    def hide(self) -> dict[str, Any]:
        """Turn off viewer visibility and clear history; cannot enable sharing."""
        return self._request(f"/api/integrations/{self.integration_id}/hide", "X-ND-Integration-Token", {})


class ViewerClient(_ScopedClient):
    """Read one curated overlay feed with a separate read-only viewer token."""

    def snapshot(self) -> dict[str, Any]:
        return self._request(f"/api/viewer/{self.integration_id}", "X-ND-Viewer-Token")

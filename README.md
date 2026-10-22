# Neurcro SDK

Add scoped lifecycle and curated viewer activity to an existing integration. Your existing connection to [VedalAI's official Neuro API](https://github.com/VedalAI/neuro-sdk/blob/main/API/SPECIFICATION.md) keeps handling game context, actions and results. This package supplies the local Neurcro manifest, Games-tab readiness and optional viewer feed. It has no runtime dependencies and supports Python 3.10+ on Windows, macOS and Linux.

This is a standalone repository. Its `.github/workflows/ci.yml` tests the package on Windows, macOS and Linux. Installation here does not imply a PyPI release exists.

## Install and describe your existing integration

```sh
python -m pip install .
python examples/existing_game_bridge.py minecraft > minecraft.manifest.json
python examples/existing_game_bridge.py osu > osu.manifest.json
```

The examples use the existing `minecraft` and `osu` game-profile IDs. Generating a manifest starts no game and reports no readiness. Call the example bridge's `on_existing_backend_ready()` only after your private integration is actually ready, and call `close()` when it stops.

```python
from neuro_desktop_sdk import IntegrationClient, Heartbeat

client = IntegrationClient("private-minecraft", scoped_token)
with Heartbeat(client):
    run_your_existing_integration()
client.close()
```

`scoped_token` comes from your local Neurcro registration. Keep it in your process's memory or your own secret storage; never commit it, put it in a URL or pass an administrator token to this SDK. `close()` forgets the client's token reference. Heartbeat helpers hide viewer history before sending `offline` on shutdown. If a process disappears, readiness expires after 45 seconds. Heartbeats run every 15 seconds, use one worker and never overlap.

## Register and approve it in Neurcro

Use your local manager's integration registration and permission controls. For an administrative tool you operate directly, the equivalent HTTP contract is:

| Request | Body | Credentials |
| --- | --- | --- |
| `PUT /api/integrations/private-minecraft` | `{"manifest": <your manifest object>}` | Your normal `X-ND-Token` session plus your explicitly re-entered `X-ND-Operator-Token` |
| `PUT /api/extensions/private-minecraft/permissions` | `{"manage_allowed":false,"run_allowed":true,"grants":{},"settings":{}}` | Both operator headers above |
| `POST /api/integrations/private-minecraft/token` | `{"kind":"integration"}` | Both operator headers above |
| `POST /api/integrations/private-minecraft/token` | `{"kind":"viewer"}` | Both operator headers above |

The token response is `{"ok":true,"id":"…","kind":"integration" or "viewer","token":"…"}`. Save its token once. Minting a replacement rotates that credential and hides the viewer feed. An integration token publishes only for its own ID; a viewer token only reads that ID's feed. The SDK deliberately contains no administrator client and cannot grant itself anything.

Approve required declared capabilities before connecting, and enable the global `extensions` permission. `run_allowed` enables ND's managed runtime lease. `manage_allowed` separately delegates management operations to Neuro; it is not needed for heartbeats. For an optional viewer feed, declare `discord.viewer` and grant it explicitly: `"grants":{"discord.viewer":true}`. Settings must match the declared settings schema. Per-item grants supplement Neurcro's global control/permission checks and do not sandbox an external executable's OS access.

For dedicated game controls, your existing backend still uses the official API. The official `game` name is recorded in `neuro_api.game_name`; this SDK never changes that connection or forwards its credentials. A live approved heartbeat links the adapter to matching `game_ids` in Neurcro's Games tab, allowing dedicated integration control to take precedence without pretending a stale process is ready.

Read `client.configuration()` to obtain only your own `manifest`, approved `settings`, effective `grants` and `run_allowed` through scoped `GET /api/integrations/{id}/config`. You cannot modify them. Heartbeat responses also carry current settings/grants; `heartbeat.last_response` holds the latest successful response so frequent operations can use a recent snapshot without another poll. Keep secrets outside settings. Your adapter implements its declared settings: the example checks the opt-in `publish_curated_activity` setting and `discord.viewer` grant before publishing, while the server independently enforces current permissions and visibility.

## Version-1 manifest

```json
{
  "schema_version": 1,
  "id": "private-minecraft",
  "name": "Your Minecraft integration",
  "version": "1.0.0",
  "kind": "game",
  "permissions": [],
  "settings_schema": {},
  "game_ids": ["minecraft"],
  "neuro_api": {"game_name": "Minecraft"}
}
```

All nine fields are required by this SDK. Kind is `integration`, `extension`, `game` or `discord`. IDs use lowercase `[a-z0-9][a-z0-9._-]{0,63}`. Permissions contain unique `id`, nonempty `description` and boolean `required` fields; the server's current capabilities are `input`, `game`, `vision`, `shell`, `filesystem`, `process`, `network`, `system`, `extensions` and `discord.viewer`. The server remains authoritative about supported capabilities and setting values.

Manifests are capped at 64 KiB, JSON depth 16, 32 permission declarations and 32 game IDs. Names and official game names are capped at 120 characters; versions at 64; permission descriptions at 500. Text cannot contain control characters. `settings_schema` is empty or describes an object. `neuro_api` is empty or contains `game_name` only: private API URLs and credentials do not belong in a public manifest. Use `IntegrationManifest(...).to_dict()`, `validate_manifest(...)`, or `load_manifest(path)` to validate. The packaged `manifest.schema.json` is useful for editor completion; the Python validator additionally checks duplicate IDs, finite JSON, control characters and total size.

## Curated activity and viewer visibility

An integration must already be ready, approved for `discord.viewer`, and currently visible for the same actor before the server accepts viewer events. Sharing starts off. Neuro or Evil chooses the `set_viewer_visibility` action with `{"integration_id":"private-discord","actor":"neuro","visible":true,"minutes":15}`; visibility expires automatically. Use `visible:false` to hide. Your backend identifies the actor and chooses the public text.

```python
client.activity(
    actor="neuro",  # or "evil", from your existing backend
    kind="discord.message",
    summary="Replied to the public community conversation.",
)
client.hide()  # may only turn sharing off; clears the feed
```

Kinds are `status`, `discord.channel`, `discord.message` and `discord.voice`. Summary length is 1–500 Unicode characters without control characters. Send already-curated public summaries. This SDK does not inspect Discord, scrape messages, read credentials, stream voice, identify the actor for you, or emit screenshots. The [private-backend example](examples/curated_discord.py) is the callback seam for your existing backend.

Runtime requests use `X-ND-Integration-Token` only:

| POST route under `/api/integrations/{id}/` | Body |
| --- | --- |
| `heartbeat` | `{"state":"ready" or "busy" or "offline","message":"optional bounded text"}` |
| `activity` | `{"actor":"neuro" or "evil","kind":"status" or "discord.channel" or "discord.message" or "discord.voice","summary":"curated text"}` |
| `hide` | `{}` |

`ViewerClient(id, viewer_token).snapshot()` sends only `X-ND-Viewer-Token` to `GET /api/viewer/{id}`. Its result has `visible`, optional `actor`, and `items` with `seq`, `time`, `actor`, `kind` and `summary`. When hidden or stale, `visible` is false and `items` is empty. For OBS, use Neurcro's local `/viewer/` overlay with the separately issued viewer token in its URL fragment, never a query string; the overlay keeps it only in memory. A viewer token is a read capability: keep the source URL out of public chat.

By default the client accepts HTTP(S) loopback origins only. Remote origins require both HTTPS and `allow_remote=True`. URLs containing paths, embedded credentials, queries or fragments are rejected. Redirects and ambient proxies are disabled to avoid forwarding scoped tokens; responses are capped at 256 KiB. An `SDKError` contains a generic message and optional HTTP `status`, never a token or response body. A 403 means to check your per-item approvals, visibility and global controls. A missing server is retried by the heartbeat worker; inspect `heartbeat.last_error` if you want to show diagnostics.

## Test and maintain

```sh
python -m pip install .
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --wheel-dir dist .
```

Tests use a local fake server, including real headers, redirect refusal, bounded responses, manifest validation and hide-before-offline shutdown. The package CI tests Python 3.10/3.14 on all three OSes and builds a wheel artifact. It does not publish to PyPI or claim to verify your private backend. Original SDK code is MIT licensed; the official Neuro SDK remains a separate project with its own authors and protocol.

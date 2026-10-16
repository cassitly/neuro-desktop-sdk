"""Lifecycle sidecar for your existing private Minecraft/osu integration.

This contains no game control or private backend implementation. Call its
methods from your existing integration's real ready/busy/shutdown callbacks.
Running this file only prints a manifest; it never claims a game is connected.
"""

from __future__ import annotations

import argparse
import json
import os

from neuro_desktop_sdk import Actor, Heartbeat, IntegrationClient, IntegrationManifest, Permission


def make_manifest(game_id: str) -> IntegrationManifest:
    game_name = {"minecraft": "Minecraft", "osu": "osu!"}[game_id]
    return IntegrationManifest(
        id=f"private-{game_id}", name=f"Your existing {game_name} integration",
        version="0.1.0", kind="game", game_ids=(game_id,),
        neuro_api={"game_name": game_name},
        permissions=(Permission("discord.viewer", "Publish curated activity to your optional viewer overlay", False),),
        settings_schema={
            "type": "object",
            "properties": {"publish_curated_activity": {"type": "boolean", "default": False}},
            "additionalProperties": False,
        },
    )


class ExistingGameBridge:
    def __init__(self, game_id: str):
        self.manifest = make_manifest(game_id)
        self.client = IntegrationClient(self.manifest.id, os.environ["ND_INTEGRATION_TOKEN"])
        self.heartbeat = Heartbeat(self.client)

    def on_existing_backend_ready(self) -> None:
        # Call only after your existing connection and game integration are ready.
        self.heartbeat.start()

    def on_existing_backend_busy(self, busy: bool) -> None:
        self.heartbeat.update("busy" if busy else "ready")

    def publish_curated_activity(self, actor: Actor, viewer_safe_summary: str) -> None:
        # Only call after your backend chooses a safe, public summary. Use the
        # actual actor from that backend, never infer it from the selected game.
        # ND also requires a discord.viewer grant and current visibility consent.
        configuration = self.client.configuration()
        if not configuration.get("settings", {}).get("publish_curated_activity", False):
            return
        if not configuration.get("grants", {}).get("discord.viewer", False):
            return
        self.client.activity(actor=actor, kind="status", summary=viewer_safe_summary)

    def close(self) -> None:
        self.heartbeat.close()
        self.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", choices=["minecraft", "osu"])
    args = parser.parse_args()
    print(json.dumps(make_manifest(args.game).to_dict(), indent=2))

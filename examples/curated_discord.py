"""Connect this publisher to your existing private Discord backend callbacks.

The SDK does not read Discord, retrieve tokens or decide what is safe to stream.
Your existing backend remains responsible for identifying Neuro/Evil and for
selecting a public summary before invoking publish().
"""

from neuro_desktop_sdk import ActivityKind, Actor, Heartbeat, IntegrationClient


class CuratedDiscordPublisher:
    def __init__(self, scoped_integration_token: str):
        self.client = IntegrationClient("private-discord", scoped_integration_token)
        self.heartbeat = Heartbeat(self.client)

    def start(self) -> None:
        self.heartbeat.start()

    def publish(self, *, actor: Actor, kind: ActivityKind, summary: str) -> None:
        # Accept only summaries already selected by the private backend for
        # streaming. No raw message objects or credentials enter this method.
        self.client.activity(actor=actor, kind=kind, summary=summary)

    def hide(self) -> None:
        self.client.hide()

    def close(self) -> None:
        self.heartbeat.close()
        self.client.close()

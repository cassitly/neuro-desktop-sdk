"""Neuro Desktop lifecycle SDK, separate from the official Neuro game API."""

from .client import ActivityKind, Actor, IntegrationClient, SDKError, State, ViewerClient
from .heartbeat import Heartbeat
from .manifest import IntegrationManifest, ManifestError, Permission, load_manifest, validate_manifest

__all__ = [
    "ActivityKind", "Actor", "Heartbeat", "IntegrationClient", "IntegrationManifest",
    "ManifestError", "Permission", "SDKError", "State", "ViewerClient",
    "load_manifest", "validate_manifest",
]
__version__ = "0.1.0"

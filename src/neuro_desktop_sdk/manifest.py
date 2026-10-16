"""Bounded version-1 manifest validation. No installation or permission grants."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping

MAX_MANIFEST_BYTES = 64 * 1024
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z", re.ASCII)
Kind = Literal["integration", "extension", "game", "discord"]
KINDS = {"integration", "extension", "game", "discord"}


class ManifestError(ValueError):
    """An integration manifest is invalid or exceeds its limits."""


def validate_id(value: object, field_name: str = "id") -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ManifestError(f"{field_name} must match [a-z0-9][a-z0-9._-]{{0,63}}")
    return value


def clean_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ManifestError(f"{field_name} must be nonempty text of at most {maximum} characters")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ManifestError(f"{field_name} cannot contain control characters")
    return value


def _bounded_json(value: object, depth: int = 0) -> None:
    if depth > 16:
        raise ManifestError("JSON nesting cannot exceed 16 levels")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ManifestError("JSON object keys must be strings")
            _bounded_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _bounded_json(child, depth + 1)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ManifestError("Manifest values must be JSON data")


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached JSON manifest or raise ManifestError.

    ``settings_schema`` describes settings; the SDK does not change them or grant
    its own permissions. ``neuro_api`` documents the existing official API link.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("Manifest must be a JSON object")
    _bounded_json(manifest)
    try:
        encoded = json.dumps(manifest, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ManifestError("Manifest must contain finite, valid JSON values") from exc
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest cannot exceed 64 KiB")
    required = {"schema_version", "id", "name", "version", "kind", "permissions", "settings_schema", "game_ids", "neuro_api"}
    if set(manifest) != required:
        raise ManifestError("Manifest must contain exactly the version-1 fields")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ManifestError("schema_version must be 1")
    validate_id(manifest["id"])
    clean_text(manifest["name"], "name", 120)
    clean_text(manifest["version"], "version", 64)
    if not isinstance(manifest["kind"], str) or manifest["kind"] not in KINDS:
        raise ManifestError("kind must be integration, extension, game or discord")
    permissions = manifest["permissions"]
    if not isinstance(permissions, list) or len(permissions) > 32:
        raise ManifestError("permissions must be a list of at most 32 entries")
    seen = set()
    for permission in permissions:
        if not isinstance(permission, dict) or set(permission) != {"id", "description", "required"}:
            raise ManifestError("Each permission needs id, description and required")
        permission_id = validate_id(permission["id"], "permission id")
        if permission_id in seen:
            raise ManifestError("Permission IDs must be unique")
        seen.add(permission_id)
        clean_text(permission["description"], "permission description", 500)
        if type(permission["required"]) is not bool:
            raise ManifestError("permission required must be a boolean")
    if not isinstance(manifest["settings_schema"], dict):
        raise ManifestError("settings_schema must be an object")
    schema = manifest["settings_schema"]
    if schema and schema.get("type") != "object":
        raise ManifestError("settings_schema must describe an object")
    game_ids = manifest["game_ids"]
    if not isinstance(game_ids, list) or len(game_ids) > 32:
        raise ManifestError("game_ids must be a list of at most 32 IDs")
    for game_id in game_ids:
        validate_id(game_id, "game id")
    if len(set(game_ids)) != len(game_ids):
        raise ManifestError("game_ids must be unique")
    neuro_api = manifest["neuro_api"]
    if not isinstance(neuro_api, dict):
        raise ManifestError("neuro_api must be an object")
    if neuro_api:
        if set(neuro_api) != {"game_name"}:
            raise ManifestError("neuro_api contains only the existing official API game_name")
        clean_text(neuro_api.get("game_name"), "neuro_api.game_name", 120)
    return json.loads(encoded)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Read at most 64 KiB, reject duplicate JSON keys, then validate."""
    with Path(path).open("rb") as source:
        content = source.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest cannot exceed 64 KiB")
    try:
        manifest = json.loads(content, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ManifestError("Manifest must be valid UTF-8 JSON without duplicate keys") from exc
    return validate_manifest(manifest)


@dataclass(frozen=True)
class Permission:
    id: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class IntegrationManifest:
    id: str
    name: str
    version: str
    kind: Kind = "integration"
    permissions: tuple[Permission, ...] = ()
    settings_schema: dict[str, Any] = field(default_factory=dict)
    game_ids: tuple[str, ...] = ()
    neuro_api: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return validate_manifest({
            "schema_version": 1, "id": self.id, "name": self.name,
            "version": self.version, "kind": self.kind,
            "permissions": [{"id": p.id, "description": p.description, "required": p.required} for p in self.permissions],
            "settings_schema": self.settings_schema, "game_ids": list(self.game_ids),
            "neuro_api": self.neuro_api,
        })

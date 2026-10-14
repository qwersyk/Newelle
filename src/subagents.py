"""Named subagent definitions and enablement resolution.

Subagent definitions created by the user are global application data.  Their
enablement is intentionally stored in a separate setting so it can participate
in Newelle profiles.  Extensions are merged into the live registry and are
never copied into the user's definition store.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
import uuid


SUBAGENTS_KEY = "subagents"
SUBAGENTS_SETTINGS_KEY = "subagents-settings"
USER_ID_PREFIX = "user:"
EXTENSION_ID_PREFIX = "extension:"

CANONICAL_FIELDS = (
    "name",
    "description",
    "system_prompt",
    "include_agents_md",
    "tools",
    "skills",
    "provider",
    "model",
    "use_secondary_model",
    "default_on",
)

_ID_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SubagentError(ValueError):
    """Base class for subagent definition errors."""


class InvalidSubagentError(SubagentError):
    """Raised when a subagent definition or identifier is invalid."""


class SubagentNotFoundError(SubagentError):
    """Raised when a requested subagent does not exist in the live registry."""


class ReadOnlySubagentError(SubagentError):
    """Raised when an extension-owned subagent is mutated."""


def validate_subagent_id_component(value: object, label: str = "subagent id") -> str:
    """Validate an extension/local identifier used inside a stable ID."""
    if not isinstance(value, str) or not _ID_COMPONENT_RE.fullmatch(value):
        raise InvalidSubagentError(
            f"{label} must contain only letters, numbers, '.', '_' or '-'"
        )
    return value


def _normalize_string(value: object, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidSubagentError(f"'{field}' must be a string")
    value = value.strip() if field == "name" else value
    if required and not value:
        raise InvalidSubagentError(f"'{field}' cannot be empty")
    return value


def _normalize_optional_string(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidSubagentError(f"'{field}' must be a string or null")
    value = value.strip()
    return value or None


def _normalize_name_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise InvalidSubagentError(f"'{field}' must be a list of names")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidSubagentError(
                f"every entry in '{field}' must be a non-empty string"
            )
        item = item.strip()
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_subagent_definition(definition: object) -> dict:
    """Return a validated definition containing only canonical fields.

    Unknown keys are ignored.  This permits extension contribution dictionaries
    to carry their required local ``id`` without leaking it into persisted user
    definitions.
    """
    if not isinstance(definition, dict):
        raise InvalidSubagentError("A subagent definition must be an object")

    default_on = definition.get("default_on", True)
    if not isinstance(default_on, bool):
        raise InvalidSubagentError("'default_on' must be a boolean")
    include_agents_md = definition.get("include_agents_md", False)
    if not isinstance(include_agents_md, bool):
        raise InvalidSubagentError("'include_agents_md' must be a boolean")
    use_secondary_model = definition.get("use_secondary_model", False)
    if not isinstance(use_secondary_model, bool):
        raise InvalidSubagentError("'use_secondary_model' must be a boolean")
    provider = _normalize_optional_string(
        definition.get("provider"), "provider"
    )
    if use_secondary_model:
        provider = None

    return {
        "name": _normalize_string(
            definition.get("name", ""), "name", required=True
        ),
        "description": _normalize_string(
            definition.get("description", ""), "description"
        ),
        "system_prompt": _normalize_string(
            definition.get("system_prompt", ""), "system_prompt"
        ),
        "include_agents_md": include_agents_md,
        "tools": _normalize_name_list(definition.get("tools", []), "tools"),
        "skills": _normalize_name_list(definition.get("skills", []), "skills"),
        "provider": provider,
        "model": _normalize_optional_string(definition.get("model"), "model"),
        "use_secondary_model": use_secondary_model,
        "default_on": default_on,
    }


class SubagentManager:
    """Manage user and extension named subagents.

    Args:
        settings: A ``Gio.Settings``-compatible object.
        extension_loader: Optional :class:`ExtensionLoader` used to build the
            dynamic extension contribution view.
        mode_manager: Optional :class:`ModeManager` used for mode-aware
            enablement.  When omitted the current serialized mode is read from
            ``settings`` directly, which keeps the manager useful in headless
            contexts.
    """

    def __init__(self, settings, extension_loader=None, mode_manager=None):
        self.settings = settings
        self.extension_loader = extension_loader
        self.mode_manager = mode_manager
        self._user_subagents: dict[str, dict] = {}
        self._extension_subagents: dict[str, dict] = {}
        self._load_user_subagents()
        self.reload_extensions()

    # ------------------------------------------------------------------ #
    # Loading and persistence
    # ------------------------------------------------------------------ #
    def _read_json_object(self, key: str) -> dict:
        try:
            value = json.loads(self.settings.get_string(key))
        except (AttributeError, KeyError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _load_user_subagents(self):
        raw = self._read_json_object(SUBAGENTS_KEY)
        normalized = {}
        for identifier, definition in raw.items():
            if not isinstance(identifier, str) or not identifier.startswith(
                USER_ID_PREFIX
            ):
                continue
            try:
                validate_subagent_id_component(
                    identifier[len(USER_ID_PREFIX) :], "user subagent id"
                )
                normalized[identifier] = normalize_subagent_definition(definition)
            except InvalidSubagentError:
                # One malformed record must not make all configured subagents
                # unavailable.  Mutations are validated strictly below.
                continue
        self._user_subagents = normalized
        if normalized != raw:
            self._save_user_subagents()

    def _save_user_subagents(self):
        self.settings.set_string(
            SUBAGENTS_KEY,
            json.dumps(self._user_subagents, ensure_ascii=False),
        )

    def _get_enablement_map(self) -> dict:
        raw = self._read_json_object(SUBAGENTS_SETTINGS_KEY)
        result = {}
        for identifier, config in raw.items():
            # Accept an early boolean-only representation when reading, while
            # always writing the extensible object representation.
            if isinstance(config, bool):
                result[identifier] = {"enabled": config}
            elif isinstance(config, dict) and isinstance(
                config.get("enabled"), bool
            ):
                result[identifier] = {"enabled": config["enabled"]}
        return result

    def _save_enablement_map(self, enablement: dict):
        self.settings.set_string(
            SUBAGENTS_SETTINGS_KEY,
            json.dumps(enablement, ensure_ascii=False),
        )

    def reload_extensions(self, extension_loader=None) -> dict:
        """Rebuild and return the dynamic extension contribution mapping."""
        if extension_loader is not None:
            self.extension_loader = extension_loader

        contributions = {}
        loader = self.extension_loader
        if loader is not None:
            try:
                contributions = loader.get_subagent_contributions() or {}
            except Exception as error:
                print(f"Error reading extension subagents: {error}")
                contributions = {}

        normalized = {}
        if isinstance(contributions, dict):
            for identifier, definition in contributions.items():
                if (
                    not isinstance(identifier, str)
                    or not identifier.startswith(EXTENSION_ID_PREFIX)
                    or not isinstance(definition, dict)
                ):
                    continue
                try:
                    canonical = normalize_subagent_definition(definition)
                except InvalidSubagentError:
                    continue
                extension_id = definition.get("extension_id")
                if not isinstance(extension_id, str):
                    parts = identifier.split(":", 2)
                    extension_id = parts[1] if len(parts) == 3 else ""
                canonical.update(
                    {
                        "id": identifier,
                        "source": "extension",
                        "read_only": True,
                        "extension_id": extension_id,
                        "source_extension": extension_id,
                    }
                )
                normalized[identifier] = canonical
        self._extension_subagents = normalized
        return deepcopy(normalized)

    # ------------------------------------------------------------------ #
    # Read accessors and resolution
    # ------------------------------------------------------------------ #
    def _decorate_user(self, identifier: str, definition: dict) -> dict:
        result = deepcopy(definition)
        result.update(
            {
                "id": identifier,
                "source": "user",
                "read_only": False,
            }
        )
        return result

    def _all_subagents(self) -> dict[str, dict]:
        users = {
            identifier: self._decorate_user(identifier, definition)
            for identifier, definition in self._user_subagents.items()
        }
        # Prefixes make collisions impossible, but user data deliberately wins
        # should a future ID format ever make one possible.
        return {**self._extension_subagents, **users}

    def get_subagents(self, enabled_only: bool = False) -> dict[str, dict]:
        """Return live definitions keyed by stable ID.

        Returned definitions include ownership metadata plus ``base_enabled``,
        effective ``enabled``, and the active ``mode_override`` for settings UI
        consumers.  These derived fields are never persisted.
        """
        result = {}
        for identifier, definition in self._all_subagents().items():
            enabled = self.is_enabled(identifier)
            if enabled_only and not enabled:
                continue
            item = deepcopy(definition)
            item["base_enabled"] = self.is_enabled(
                identifier, mode_aware=False
            )
            item["enabled"] = enabled
            item["mode_override"] = self._get_mode_override(identifier)
            result[identifier] = item
        return result

    def get_subagent(self, identifier: str) -> dict | None:
        """Return one live definition, or ``None`` when it is unavailable."""
        definition = deepcopy(self._all_subagents().get(identifier))
        if definition is None:
            return None
        definition["base_enabled"] = self.is_enabled(
            identifier, mode_aware=False
        )
        definition["enabled"] = self.is_enabled(identifier)
        definition["mode_override"] = self._get_mode_override(identifier)
        return definition

    def _get_mode_override(self, identifier: str) -> str:
        if self.mode_manager is not None:
            getter = getattr(self.mode_manager, "get_subagent_override", None)
            if callable(getter):
                return getter(identifier)

        modes = self._read_json_object("modes")
        try:
            active_name = self.settings.get_string("current-mode")
        except (AttributeError, KeyError, TypeError, ValueError):
            return "no_change"
        mode = modes.get(active_name, {})
        if not isinstance(mode, dict):
            return "no_change"
        state_map = mode.get("subagents", {})
        if not isinstance(state_map, dict):
            return "no_change"
        state = state_map.get(identifier, "no_change")
        return state if state in ("enable", "remove") else "no_change"

    def is_enabled(self, identifier: str, mode_aware: bool = True) -> bool:
        """Resolve profile base enablement and, optionally, the active mode."""
        definition = self._all_subagents().get(identifier)
        if definition is None:
            return False
        config = self._get_enablement_map().get(identifier, {})
        base_enabled = config.get("enabled", definition["default_on"])
        if not mode_aware:
            return base_enabled

        if self.mode_manager is not None:
            resolver = getattr(
                self.mode_manager, "resolve_subagent_enabled", None
            )
            if callable(resolver):
                return resolver(identifier, base_enabled)

        override = self._get_mode_override(identifier)
        if override == "enable":
            return True
        if override == "remove":
            return False
        return base_enabled

    # ------------------------------------------------------------------ #
    # Mutators
    # ------------------------------------------------------------------ #
    def create(self, definition: dict | None = None, **fields) -> str:
        """Create a user-owned subagent and return its immutable stable ID."""
        if definition is None:
            definition = {}
        if not isinstance(definition, dict):
            raise InvalidSubagentError("A subagent definition must be an object")
        candidate = {**definition, **fields}
        normalized = normalize_subagent_definition(candidate)
        identifier = f"{USER_ID_PREFIX}{uuid.uuid4().hex}"
        self._user_subagents[identifier] = normalized
        self._save_user_subagents()
        return identifier

    def update(
        self, identifier: str, updates: dict | None = None, **fields
    ) -> dict:
        """Replace selected canonical fields of a user-owned subagent."""
        if identifier in self._extension_subagents:
            raise ReadOnlySubagentError(
                f"Subagent '{identifier}' is owned by an extension"
            )
        current = self._user_subagents.get(identifier)
        if current is None:
            raise SubagentNotFoundError(f"Subagent '{identifier}' not found")
        if updates is None:
            updates = {}
        if not isinstance(updates, dict):
            raise InvalidSubagentError("Subagent updates must be an object")
        requested = {**updates, **fields}
        merged = dict(current)
        merged.update(
            {key: value for key, value in requested.items() if key in CANONICAL_FIELDS}
        )
        normalized = normalize_subagent_definition(merged)
        self._user_subagents[identifier] = normalized
        self._save_user_subagents()
        return self.get_subagent(identifier)

    def delete(self, identifier: str) -> bool:
        """Delete a user definition and its toggle/mode references."""
        if identifier in self._extension_subagents:
            raise ReadOnlySubagentError(
                f"Subagent '{identifier}' is owned by an extension"
            )
        if identifier not in self._user_subagents:
            return False

        del self._user_subagents[identifier]
        self._save_user_subagents()

        # Work from the raw mapping so even an old/malformed entry for this ID
        # is removed rather than being hidden by read-time normalization.
        enablement = self._read_json_object(SUBAGENTS_SETTINGS_KEY)
        if identifier in enablement:
            del enablement[identifier]
            self._save_enablement_map(enablement)
        self._remove_profile_enablement_references(identifier)
        self._remove_mode_references(identifier)
        return True

    def set_enabled(self, identifier: str, enabled: bool) -> bool:
        """Set profile base enablement and return the effective state."""
        if identifier not in self._all_subagents():
            raise SubagentNotFoundError(f"Subagent '{identifier}' not found")
        if not isinstance(enabled, bool):
            raise InvalidSubagentError("'enabled' must be a boolean")
        enablement = self._get_enablement_map()
        enablement[identifier] = {"enabled": enabled}
        self._save_enablement_map(enablement)
        return self.is_enabled(identifier)

    def _remove_mode_references(self, identifier: str):
        if self.mode_manager is not None:
            remover = getattr(
                self.mode_manager, "remove_subagent_reference", None
            )
            if callable(remover):
                remover(identifier)
                return

        modes = self._read_json_object("modes")
        changed = False
        for mode in modes.values():
            if not isinstance(mode, dict):
                continue
            state_map = mode.get("subagents")
            if isinstance(state_map, dict) and identifier in state_map:
                del state_map[identifier]
                changed = True
        if changed:
            self.settings.set_string("modes", json.dumps(modes, ensure_ascii=False))

    def _remove_profile_enablement_references(self, identifier: str):
        """Remove an orphaned toggle from every saved profile snapshot."""
        profiles = self._read_json_object("profiles")
        changed = False
        for profile in profiles.values():
            if not isinstance(profile, dict):
                continue
            saved_settings = profile.get("settings")
            if not isinstance(saved_settings, dict):
                continue
            raw = saved_settings.get(SUBAGENTS_SETTINGS_KEY)
            was_string = isinstance(raw, str)
            if was_string:
                try:
                    enablement = json.loads(raw)
                except (TypeError, ValueError):
                    continue
            else:
                enablement = raw
            if not isinstance(enablement, dict) or identifier not in enablement:
                continue
            del enablement[identifier]
            saved_settings[SUBAGENTS_SETTINGS_KEY] = (
                json.dumps(enablement, ensure_ascii=False)
                if was_string
                else enablement
            )
            changed = True
        if changed:
            self.settings.set_string(
                "profiles", json.dumps(profiles, ensure_ascii=False)
            )

    # Explicit aliases make call sites self-documenting without duplicating
    # implementation or forcing extensions/UI code into one naming convention.
    create_subagent = create
    update_subagent = update
    delete_subagent = delete

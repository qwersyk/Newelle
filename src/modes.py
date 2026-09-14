"""Modes infrastructure.

A **Mode** is a named overlay that customizes the assistant's behavior without
touching the active profile. It is composed of:

- ``description``  : short one-line summary shown in the mode switcher popover.
- ``icon``         : a GTK symbolic icon name (see ``DEFAULT_MODE_ICON``).
- ``tools``        : mapping ``tool_name -> state`` describing how each tool is
  affected relative to the current profile.
- ``skills``       : mapping ``skill_name -> state`` describing how each skill is
  affected relative to the current profile.
- ``prompts``      : mapping ``prompt_key -> {state, override}`` describing whether
  each prompt is enabled and, optionally, which text replaces its profile value.

Each tool, skill, or prompt ``state`` is one of three values:

- ``"enable"``    : force the tool/skill on, regardless of profile settings.
- ``"remove"``    : force the tool/skill off, regardless of profile settings.
- ``"no_change"`` : leave the tool/skill as configured in the current profile.

Only the infrastructure is provided here; the UI is added separately.
"""

import json
import gettext

_ = gettext.gettext

# Valid three-state values for tools and skills inside a mode.
ENABLE = "enable"
REMOVE = "remove"
NO_CHANGE = "no_change"
VALID_STATES = (ENABLE, REMOVE, NO_CHANGE)

# Built-in modes that every installation ships with. They are merged into the
# stored ``modes`` setting on load if missing and cannot be renamed or deleted.
PLAN_ASSISTANT_OVERRIDE = """## Plan Mode

You are operating in **Plan Mode**. In this mode you must NOT make any changes
to the system: do not execute commands, do not create, edit, or delete files,
and do not invoke any tool that has a side effect on the user's machine.

Your goal is to:
1. Explore and understand the request and the relevant code/context.
2. Design a concrete, step-by-step implementation plan.
3. Present the plan to the user for approval before any action is taken.

If a piece of information is missing, ask the user. Reason about trade-offs
explicitly. Once the plan is approved, the user will switch out of Plan Mode
and you will be allowed to execute it."""

DEFAULT_MODES = {
    "Normal": {
        "description": _("Standard assistant behavior"),
        "icon": "chat-bubbles-text-symbolic",
        "tools": {},
        "skills": {},
        "prompts": {},
    },
    "Plan": {
        "description": _("Plan first, then act — no side effects"),
        "icon": "document-edit-symbolic",
        # The command execution tool is removed so the assistant cannot mutate
        # the user's machine while planning.
        "tools": {
            "execute_command": REMOVE,
        },
        "skills": {},
        # Plan Mode uses the same generic prompt override system available to
        # every other mode; there is no dedicated mode-only prompt anymore.
        "prompts": {
            "assistant": {
                "state": ENABLE,
                "override": PLAN_ASSISTANT_OVERRIDE,
            },
        },
    },
    "No Tools": {
        "description": _("Disable tools"),
        "icon": "system-run-symbolic",
        "tools": {},
        "skills": {},
        "prompts": {
            "tools": {
                "state": REMOVE,
            },
            "todolist": {
                "state": REMOVE,
            },
        },
    },
}

# Name of the default fallback mode.
DEFAULT_MODE_NAME = "Normal"

# Shipped modes remain editable, but their names are part of the public mode
# switcher contract and therefore cannot be renamed or deleted.
BUILT_IN_MODE_NAMES = frozenset(DEFAULT_MODES)

# Fallback icon for modes that do not declare one.
DEFAULT_MODE_ICON = "applications-system-symbolic"

# Curated symbolic icons shared by the desktop and WebUI editors.  The desktop
# filters these through the current icon theme; the WebUI exposes the names as
# portable identifiers even when it cannot render the native theme itself.
MODE_ICON_CHOICES = (
    "user-available-symbolic",
    "user-idle-symbolic",
    "chat-bubbles-text-symbolic",
    "chat-symbolic",
    "mail-unread-symbolic",
    "emblem-favorite-symbolic",
    "face-smile-symbolic",
    "document-edit-symbolic",
    "document-open-symbolic",
    "text-x-generic-symbolic",
    "edit-symbolic",
    "brain-augemnted-symbolic",
    "emoji-objects-symbolic",
    "lightbulb-symbolic",
    "magic-wand-symbolic",
    "starred-symbolic",
    "bookmark-symbolic",
    "system-search-symbolic",
    "applications-science-symbolic",
    "skills-symbolic",
    "preferences-system-symbolic",
    "system-run-symbolic",
    "utilities-terminal-symbolic",
    "code-symbolic",
    "media-playback-start-symbolic",
    "audio-x-generic-symbolic",
    "video-x-generic-symbolic",
    "image-x-generic-symbolic",
    "camera-photo-symbolic",
    "help-browser-symbolic",
    DEFAULT_MODE_ICON,
)


class ModeError(ValueError):
    """Base class for mode mutation errors."""


class InvalidModeNameError(ModeError):
    """Raised when a mode name is empty or otherwise invalid."""


class ModeAlreadyExistsError(ModeError):
    """Raised when creating or renaming to an existing name."""


class ModeNotFoundError(ModeError):
    """Raised when updating a mode that does not exist."""


class ProtectedModeError(ModeError):
    """Raised when attempting to rename a shipped mode."""


class ModeManager:
    """Load, persist, and resolve Modes backed by a ``Gio.Settings`` object.

    The stored shape is::

        {
            "<mode_name>": {
                "description":  "<str>",
                "icon":         "<str>",
                "tools":        {"<tool_name>": "<state>", ...},
                "skills":       {"<skill_name>": "<state>", ...},
                "prompts":      {
                    "<prompt_key>": {
                        "state": "<state>",
                        "override": "<str>",
                    },
                    ...
                },
            },
            ...
        }
    """

    def __init__(self, settings):
        self.settings = settings
        self._load_modes()
        self._load_active_mode()

    # ------------------------------------------------------------------ #
    # Loading / persistence
    # ------------------------------------------------------------------ #
    def _load_modes(self):
        """Load modes from settings, ensuring built-ins are always present."""
        try:
            modes = json.loads(self.settings.get_string("modes"))
        except (json.JSONDecodeError, TypeError):
            modes = {}

        if not isinstance(modes, dict):
            modes = {}

        # Normalize while merging so legacy modes containing the old singular
        # ``prompt`` field are migrated and persisted immediately.
        merged = {
            name: self._normalize_mode(data)
            for name, data in DEFAULT_MODES.items()
        }
        for name, data in modes.items():
            if isinstance(name, str) and isinstance(data, dict):
                merged[name] = self._normalize_mode(data)
        self.modes = merged

        # Persist the merged view so the schema always reflects reality.
        self._save_modes()

    def _save_modes(self):
        self.settings.set_string("modes", json.dumps(self.modes))

    def add_modes(self, modes: dict):
        """Merge extension-provided modes into the registry.

        Extension modes are added only when no mode with that name already
        exists (so user-created modes and built-ins are never clobbered). The
        merged result is persisted. Each value must be a mode dict (it is
        normalized via :meth:`_normalize_mode`).

        Args:
            modes: ``{name: mode_dict}`` mapping from extensions.
        """
        if not isinstance(modes, dict):
            return
        changed = False
        for name, data in modes.items():
            if name in self.modes:
                continue
            self.modes[name] = self._normalize_mode(data)
            changed = True
        if changed:
            self._save_modes()
        # Ensure the active mode is still valid after a merge.
        if self.active_mode not in self.modes:
            self.set_active_mode(DEFAULT_MODE_NAME)

    def _load_active_mode(self):
        active = self.settings.get_string("current-mode")
        if active not in self.modes:
            active = DEFAULT_MODE_NAME
            self.settings.set_string("current-mode", active)
        self.active_mode = active

    # ------------------------------------------------------------------ #
    # Read accessors
    # ------------------------------------------------------------------ #
    def get_modes(self) -> dict:
        """Return the full ``{name: mode_dict}`` mapping (a copy)."""
        return {name: self._normalize_mode(data) for name, data in self.modes.items()}

    def get_mode(self, name: str) -> dict | None:
        """Return a normalized copy of a single mode, or ``None`` if unknown."""
        data = self.modes.get(name)
        if data is None:
            return None
        return self._normalize_mode(data)

    def get_active_mode_name(self) -> str:
        return self.active_mode

    def get_active_mode(self) -> dict:
        """Return the active mode (falls back to Normal if missing)."""
        data = self.modes.get(self.active_mode) or self.modes[DEFAULT_MODE_NAME]
        return self._normalize_mode(data)

    def _get_resolution_mode(self, mode_name: str | None = None) -> dict:
        """Return the active mode or a named request-local mode."""
        if mode_name is None:
            return self.get_active_mode()
        mode = self.get_mode(mode_name)
        if mode is None:
            raise ValueError(f"Mode '{mode_name}' not found")
        return mode

    def get_tool_override(self, tool_name: str, mode_name: str | None = None) -> str:
        """Return a mode's state for a tool (defaults to NO_CHANGE)."""
        tools = self._get_resolution_mode(mode_name).get("tools", {})
        return tools.get(tool_name, NO_CHANGE)

    def get_skill_override(self, skill_name: str, mode_name: str | None = None) -> str:
        """Return a mode's state for a skill (defaults to NO_CHANGE)."""
        skills = self._get_resolution_mode(mode_name).get("skills", {})
        return skills.get(skill_name, NO_CHANGE)

    def get_prompt_override(self, prompt_key: str, mode_name: str | None = None) -> dict:
        """Return a mode's normalized settings for one prompt."""
        prompts = self._get_resolution_mode(mode_name).get("prompts", {})
        return dict(prompts.get(prompt_key, {"state": NO_CHANGE}))

    # ------------------------------------------------------------------ #
    # Resolution helpers (apply the 3-state to a base boolean)
    # ------------------------------------------------------------------ #
    def resolve_tool_enabled(self, tool_name: str, base_enabled: bool, mode_name: str | None = None) -> bool:
        """Apply a mode's tool state to a profile-derived boolean."""
        override = self.get_tool_override(tool_name, mode_name)
        if override == ENABLE:
            return True
        if override == REMOVE:
            return False
        return base_enabled

    def resolve_skill_enabled(self, skill_name: str, base_enabled: bool, mode_name: str | None = None) -> bool:
        """Apply a mode's skill state to a profile-derived boolean."""
        override = self.get_skill_override(skill_name, mode_name)
        if override == ENABLE:
            return True
        if override == REMOVE:
            return False
        return base_enabled

    def resolve_prompt_enabled(self, prompt_key: str, base_enabled: bool, mode_name: str | None = None) -> bool:
        """Apply a mode's state to a profile-derived prompt setting."""
        override = self.get_prompt_override(prompt_key, mode_name).get("state", NO_CHANGE)
        if override == ENABLE:
            return True
        if override == REMOVE:
            return False
        return base_enabled

    def resolve_prompt_text(self, prompt_key: str, base_text: str, mode_name: str | None = None) -> str:
        """Return a mode's text override or the profile-derived prompt text."""
        override = self.get_prompt_override(prompt_key, mode_name)
        if "override" in override:
            return override["override"]
        return base_text

    # ------------------------------------------------------------------ #
    # Mutators
    # ------------------------------------------------------------------ #
    def set_active_mode(self, name: str):
        """Switch the active mode. Raises ``ValueError`` if unknown."""
        if name not in self.modes:
            raise ValueError(f"Mode '{name}' not found")
        self.active_mode = name
        self.settings.set_string("current-mode", name)

    def cycle_mode(self) -> str:
        """Advance to the next mode in insertion order and return its name.

        Wraps around after the last mode. No-op (returns the current name) when
        fewer than two modes exist.
        """
        names = list(self.modes.keys())
        if len(names) < 2:
            return self.active_mode
        try:
            idx = names.index(self.active_mode)
        except ValueError:
            idx = -1
        next_name = names[(idx + 1) % len(names)]
        self.set_active_mode(next_name)
        return next_name

    def create_mode(self, name: str, description: str = "", icon: str = DEFAULT_MODE_ICON, tools: dict | None = None, skills: dict | None = None, prompts: dict | None = None):
        """Create a mode with a trimmed, unique name.

        Raises :class:`InvalidModeNameError` for a blank name and
        :class:`ModeAlreadyExistsError` when the normalized name is in use.
        """
        name = self._validate_name(name)
        if name in self.modes:
            raise ModeAlreadyExistsError(f"Mode '{name}' already exists")
        self.modes[name] = self._build_mode(
            description, icon, tools, skills, prompts
        )
        self._save_modes()
        return name

    def update_mode(self, name: str, description: str | None = None, icon: str | None = None, tools: dict | None = None, skills: dict | None = None, prompts: dict | None = None, new_name: str | None = None):
        """Update and optionally atomically rename an existing mode.

        ``None`` arguments leave the corresponding field untouched.  A rename
        preserves insertion order and updates ``current-mode`` when the renamed
        mode is active.  Shipped modes can be edited but cannot be renamed.
        """
        if name not in self.modes:
            raise ModeNotFoundError(f"Mode '{name}' not found")

        target_name = name if new_name is None else self._validate_name(new_name)
        if name in BUILT_IN_MODE_NAMES and target_name != name:
            raise ProtectedModeError(f"Built-in mode '{name}' cannot be renamed")
        if target_name != name and target_name in self.modes:
            raise ModeAlreadyExistsError(
                f"Mode '{target_name}' already exists"
            )

        mode = self._normalize_mode(self.modes[name])
        if description is not None:
            mode["description"] = description
        if icon is not None:
            mode["icon"] = icon
        if tools is not None:
            mode["tools"] = self._clean_state_map(tools)
        if skills is not None:
            mode["skills"] = self._clean_state_map(skills)
        if prompts is not None:
            mode["prompts"] = self._clean_prompt_map(prompts)
        mode = self._normalize_mode(mode)

        # Rebuild the mapping once so a rename cannot expose an intermediate
        # create/delete state and retains the original switcher position.
        if target_name == name:
            self.modes[name] = mode
        else:
            self.modes = {
                (target_name if current_name == name else current_name): (
                    mode if current_name == name else current_mode
                )
                for current_name, current_mode in self.modes.items()
            }
            if self.active_mode == name:
                self.active_mode = target_name
                self.settings.set_string("current-mode", target_name)
        self._save_modes()
        return target_name

    def rename_mode(self, name: str, new_name: str) -> str:
        """Rename ``name`` without changing its configuration."""
        return self.update_mode(name, new_name=new_name)

    def delete_mode(self, name: str) -> bool:
        """Delete a custom mode. Shipped modes cannot be deleted.

        Returns ``True`` if deleted, ``False`` if it was protected or unknown.
        """
        if name in BUILT_IN_MODE_NAMES:
            return False
        if name not in self.modes:
            return False
        del self.modes[name]
        self._save_modes()
        # If the active mode was removed, fall back to Normal.
        if self.active_mode == name:
            self.set_active_mode(DEFAULT_MODE_NAME)
        return True

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise InvalidModeNameError("Mode name cannot be blank")
        return name.strip()

    @staticmethod
    def _build_mode(description, icon, tools, skills, prompts) -> dict:
        return {
            "description": description or "",
            "icon": icon or DEFAULT_MODE_ICON,
            "tools": ModeManager._clean_state_map(tools or {}),
            "skills": ModeManager._clean_state_map(skills or {}),
            "prompts": ModeManager._clean_prompt_map(prompts or {}),
        }

    @staticmethod
    def _normalize_mode(data: dict) -> dict:
        """Return a complete, validated mode dict from possibly partial data.

        Older stored modes (without ``description``/``icon``) are migrated
        transparently by filling sensible defaults.
        """
        prompts = data.get("prompts", {})
        # Migrate the retired singular mode prompt to a generic override. The
        # assistant prompt is the closest equivalent and keeps existing custom
        # modes useful without retaining a hidden dedicated-prompt pathway.
        legacy_prompt = data.get("prompt", "") or ""
        if legacy_prompt and not prompts:
            prompts = {
                "assistant": {
                    "state": ENABLE,
                    "override": legacy_prompt,
                }
            }

        return {
            "description": data.get("description", "") or "",
            "icon": data.get("icon", "") or DEFAULT_MODE_ICON,
            "tools": ModeManager._clean_state_map(data.get("tools", {})),
            "skills": ModeManager._clean_state_map(data.get("skills", {})),
            "prompts": ModeManager._clean_prompt_map(prompts),
        }

    @staticmethod
    def _clean_state_map(state_map) -> dict:
        """Keep actionable valid states; neutral entries carry no information."""
        if not isinstance(state_map, dict):
            return {}
        return {
            name: state
            for name, state in state_map.items()
            if state in (ENABLE, REMOVE)
        }

    @staticmethod
    def _clean_prompt_map(prompt_map) -> dict:
        """Validate prompt states and optional string text overrides."""
        if not isinstance(prompt_map, dict):
            return {}
        cleaned = {}
        for key, config in prompt_map.items():
            if not isinstance(key, str) or not isinstance(config, dict):
                continue
            state = config.get("state", NO_CHANGE)
            if state not in VALID_STATES:
                state = NO_CHANGE
            normalized = {"state": state}
            if isinstance(config.get("override"), str):
                normalized["override"] = config["override"]
            if state != NO_CHANGE or "override" in normalized:
                cleaned[key] = normalized
        return cleaned

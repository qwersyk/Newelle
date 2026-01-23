from gi.repository import GLib


def get_settings_dict(settings, blacklisted_keys: list = []):
    """
    Return a dictionary containing all settings from a Gio.Settings object.
    """
    settings_dict = {}
    for key in settings.list_keys():
        if key in blacklisted_keys:
            continue
        value = settings.get_value(key)
        settings_dict[key] = value.unpack()
    return settings_dict


def restore_settings_from_dict(settings, settings_dict):
    """
    Restore settings from a dictionary into a Gio.Settings object.
    """
    for key, value in settings_dict.items():
        current_value = settings.get_value(key)
        variant = GLib.Variant(current_value.get_type_string(), value)
        settings.set_value(key, variant)


def get_settings_dict_by_groups(settings, groups: list, settings_groups: dict, blacklisted_keys: list = []):
    """
    Return a dictionary containing settings from specified groups from a Gio.Settings object.

    Args:
        settings: Gio.Settings object
        groups: List of group names to include (e.g. ["LLM", "TTS"])
        settings_groups: Dictionary mapping group names to their settings
        blacklisted_keys: List of keys to exclude
    """
    if len(groups) == 0:
        groups = list(settings_groups.keys())
    # Get all settings keys for the specified groups
    allowed_keys = set()
    for group in groups:
        if group in settings_groups:
            allowed_keys.update(settings_groups[group]["settings"])

    settings_dict = {}
    for key in settings.list_keys():
        if key in blacklisted_keys or key not in allowed_keys:
            continue
        value = settings.get_value(key)
        settings_dict[key] = value.unpack()
    return settings_dict


def restore_settings_from_dict_by_groups(settings, settings_dict, groups: list, settings_groups: dict):
    """
    Restore settings from a dictionary into a Gio.Settings object, but only for specified groups.

    Args:
        settings: Gio.Settings object
        settings_dict: Dictionary of settings to restore
        groups: List of group names to include (e.g. ["LLM", "TTS"])
        settings_groups: Dictionary mapping group names to their settings
    """
    # Get all settings keys for the specified groups
    if len(groups) == 0:
        groups = list(settings_groups.keys())
    allowed_keys = set()
    for group in groups:
        if group in settings_groups:
            allowed_keys.update(settings_groups[group]["settings"])

    for key, value in settings_dict.items():
        if key not in allowed_keys:
            continue
        current_value = settings.get_value(key)
        variant = GLib.Variant(current_value.get_type_string(), value)
        settings.set_value(key, variant)


class ProfileSettingsProxy:
    """
    Lightweight per-profile settings proxy.
    It keeps an in-memory cache of profile-scoped keys while delegating other keys to the base Gio.Settings.
    Handlers use this proxy so background windows stay on their own profile even if another window changes GSettings.
    """

    def __init__(self, base_settings, settings_groups: dict[str, dict]):
        self.base = base_settings
        self.settings_groups = settings_groups
        self.profile_name = None
        self.profile_store: dict[str, dict] | None = None
        self.cache: dict[str, object] = {}
        self.allowed_keys: set[str] = set()
        self.active = False
        # Track external changes on Gio.Settings to keep cache in sync for the active profile
        try:
            self._handler_id = self.base.connect("changed", self._on_changed)
        except Exception:
            self._handler_id = None

    def _compute_allowed_keys(self, groups: list[str]):
        if len(groups) == 0:
            groups = list(self.settings_groups.keys())
        keys = set()
        for g in groups:
            if g in self.settings_groups:
                keys.update(self.settings_groups[g]["settings"])
        self.allowed_keys = keys

    def set_profile(self, profile_name: str, profile_store: dict[str, dict]):
        self.profile_name = profile_name
        self.profile_store = profile_store
        profile = profile_store.get(profile_name, {"settings": {}, "settings_groups": []})
        self._compute_allowed_keys(profile.get("settings_groups", []))
        self.cache = profile.get("settings", {}).copy()

    def set_active(self, active: bool):
        self.active = active

    def _update_store(self, key, value):
        if self.profile_store is None or self.profile_name not in self.profile_store:
            return
        if "settings" not in self.profile_store[self.profile_name]:
            self.profile_store[self.profile_name]["settings"] = {}
        self.profile_store[self.profile_name]["settings"][key] = value

    def _on_changed(self, _settings, key):
        # Only sync when this proxy is active; otherwise another profile is applied in Gio.Settings
        if not self.active or key not in self.allowed_keys:
            return
        try:
            # Read fresh value and update cache/store
            value = self.base.get_value(key).unpack()
            self.cache[key] = value
            self._update_store(key, value)
        except Exception:
            pass

    def get_cached_snapshot(self, groups: list[str]) -> dict:
        """Return cached values limited to provided groups."""
        allowed = set()
        if len(groups) == 0:
            groups = list(self.settings_groups.keys())
        for g in groups:
            if g in self.settings_groups:
                allowed.update(self.settings_groups[g]["settings"])
        return {k: v for k, v in self.cache.items() if k in allowed}

    # Basic getters
    def _get(self, key, getter):
        if key in self.allowed_keys:
            if self.active:
                value = getter(key)
                self.cache[key] = value
                self._update_store(key, value)
                return value
            return self.cache.get(key, getter(key))
        return getter(key)

    def get_string(self, key):
        return self._get(key, self.base.get_string)

    def get_boolean(self, key):
        return self._get(key, self.base.get_boolean)

    def get_int(self, key):
        return self._get(key, self.base.get_int)

    def get_double(self, key):
        return self._get(key, self.base.get_double)

    def get_value(self, key):
        return self._get(key, self.base.get_value)

    # Basic setters (update cache and base for UI coherence)
    def _set(self, key, value, setter):
        if key in self.allowed_keys:
            self.cache[key] = value
            self._update_store(key, value)
            if self.active:
                setter(key, value)
            return
        setter(key, value)

    def set_string(self, key, value):
        self._set(key, value, self.base.set_string)

    def set_boolean(self, key, value):
        self._set(key, value, self.base.set_boolean)

    def set_int(self, key, value):
        self._set(key, value, self.base.set_int)

    def set_double(self, key, value):
        self._set(key, value, self.base.set_double)

    def set_value(self, key, value):
        self._set(key, value, self.base.set_value)

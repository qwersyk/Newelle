from gi.repository import GLib

def get_settings_dict(settings, blacklisted_keys:list = []):
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
    
    # Path and active mode belong to the workspace, even in older profile snapshots.
    settings_dict = {}
    for key in settings.list_keys():
        if key in blacklisted_keys or key in ("path", "current-mode") or key not in allowed_keys:
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
        if key in ("path", "current-mode") or key not in allowed_keys:
            continue
        current_value = settings.get_value(key)
        variant = GLib.Variant(current_value.get_type_string(), value)
        settings.set_value(key, variant)


# Profile inheritance
#
# Every profile except the root has a parent ("Based on").  A profile owns the
# settings groups listed in ``settings_groups`` and inherits every other group
# from the closest ancestor that owns it.  The root owns all groups, so every
# group always resolves to exactly one owner, and the live value of a group is
# always saved back into that owner.

ROOT_PROFILE = "Assistant"
# Path and active mode belong to the workspace, even in older profile snapshots.
WORKSPACE_KEYS = ("path", "current-mode")


def new_profile_record(picture=None, settings=None, settings_groups=None, parent=ROOT_PROFILE):
    return {
        "picture": picture,
        "settings": dict(settings or {}),
        "settings_groups": list(settings_groups or []),
        "parent": parent,
    }


def get_profile_parent(profiles: dict, name: str, root: str = ROOT_PROFILE):
    """Return the parent of a profile, or None when it acts as the root."""
    if name == root:
        return None
    profile = profiles.get(name) or {}
    parent = profile.get("parent") or root
    if parent == name or parent not in profiles:
        parent = root
    if parent == name or parent not in profiles:
        return None
    return parent


def get_profile_chain(profiles: dict, name: str, root: str = ROOT_PROFILE) -> list:
    """Return ``[name, parent, grandparent, ...]``, stopping on cycles."""
    chain = []
    current = name
    while current is not None and current not in chain:
        chain.append(current)
        current = get_profile_parent(profiles, current, root)
    return chain


def get_profile_descendants(profiles: dict, name: str, root: str = ROOT_PROFILE) -> set:
    """Return every profile whose parent chain passes through ``name``."""
    return {
        other for other in profiles
        if other != name and name in get_profile_chain(profiles, other, root)
    }


def get_profile_groups(profiles: dict, name: str, settings_groups: dict, root: str = ROOT_PROFILE) -> list:
    """Return the settings groups a profile overrides."""
    if get_profile_parent(profiles, name, root) is None:
        return list(settings_groups.keys())
    profile = profiles.get(name) or {}
    groups = profile.get("settings_groups") or []
    # Profiles saved before inheritance existed have no parent, and an empty
    # group list used to mean "every group".
    if "parent" not in profile and len(groups) == 0:
        return list(settings_groups.keys())
    return [group for group in settings_groups if group in groups]


def get_group_owners(profiles: dict, name: str, settings_groups: dict, root: str = ROOT_PROFILE) -> dict:
    """Map each settings group to the profile that provides its values."""
    chain = get_profile_chain(profiles, name, root)
    owned = {profile: set(get_profile_groups(profiles, profile, settings_groups, root)) for profile in chain}
    owners = {}
    for group in settings_groups:
        owners[group] = next((profile for profile in chain if group in owned[profile]), chain[-1])
    return owners


def get_group_keys(group: str, settings_groups: dict) -> list:
    return [key for key in settings_groups[group]["settings"] if key not in WORKSPACE_KEYS]


def resolve_profile_settings(profiles: dict, name: str, settings_groups: dict, root: str = ROOT_PROFILE) -> dict:
    """Return the saved values a profile sees, including inherited groups."""
    values = {}
    for group, owner in get_group_owners(profiles, name, settings_groups, root).items():
        saved = (profiles.get(owner) or {}).get("settings") or {}
        for key in get_group_keys(group, settings_groups):
            if key in saved:
                values[key] = saved[key]
    return values


def save_live_settings_to_profiles(settings, profiles: dict, name: str, settings_groups: dict, root: str = ROOT_PROFILE):
    """Store the live value of every group in the profile that provides it."""
    available = set(settings.list_keys())
    for group, owner in get_group_owners(profiles, name, settings_groups, root).items():
        record = profiles.setdefault(owner, new_profile_record())
        saved = record.get("settings")
        if not isinstance(saved, dict):
            saved = {}
            record["settings"] = saved
        for key in get_group_keys(group, settings_groups):
            if key in available:
                saved[key] = settings.get_value(key).unpack()
    return profiles


def apply_profile_settings(settings, profiles: dict, name: str, settings_groups: dict, root: str = ROOT_PROFILE):
    """Write a profile's resolved values into the live settings."""
    available = set(settings.list_keys())
    for key, value in resolve_profile_settings(profiles, name, settings_groups, root).items():
        if key not in available:
            continue
        current_value = settings.get_value(key)
        if current_value.unpack() == value:
            continue
        settings.set_value(key, GLib.Variant(current_value.get_type_string(), value))

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

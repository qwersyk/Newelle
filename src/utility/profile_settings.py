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

import os
import re
import subprocess
import sys

def is_wayland() -> bool:
    """
    Check if we are in a Wayland environment

    Returns:
        bool: True if we are in a Wayland environment
    """
    if os.getenv("WAYLAND_DISPLAY"):
        return True
    return False

def is_flatpak() -> bool:
    """
    Check if we are in a flatpak

    Returns:
        bool: True if we are in a flatpak
    """
    if os.getenv("container"):
        return True
    return False

def primary_accel(key: str) -> str:
    """Build a macOS primary accelerator string."""
    return f"<Meta>{key}"

def has_primary_modifier(state) -> bool:
    """Return True when the macOS primary shortcut modifier is pressed."""
    from gi.repository import Gdk

    meta_mask = getattr(Gdk.ModifierType, "META_MASK", 0)
    super_mask = getattr(Gdk.ModifierType, "SUPER_MASK", 0)
    return bool(state & (meta_mask | super_mask))


def _is_alt_pressed(state) -> bool:
    from gi.repository import Gdk

    alt_mask = getattr(Gdk.ModifierType, "ALT_MASK", 0)
    mod1_mask = getattr(Gdk.ModifierType, "MOD1_MASK", 0)
    return bool(state & (alt_mask | mod1_mask))


def _copy_editable(widget) -> bool:
    from gi.repository import Gdk

    bounds = widget.get_selection_bounds()
    if not bounds:
        return False
    start, end = bounds
    text = widget.get_chars(start, end)
    display = Gdk.Display.get_default()
    if display is None:
        return False
    display.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
    return True


def _cut_editable(widget) -> bool:
    bounds = widget.get_selection_bounds()
    if not bounds:
        return False
    start, end = bounds
    if not _copy_editable(widget):
        return False
    widget.delete_text(start, end)
    widget.set_position(start)
    return True


def _paste_editable(widget) -> bool:
    clipboard = widget.get_clipboard()
    if clipboard is None:
        return False

    def on_read_text(clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except Exception:
            return
        if text is None:
            return
        bounds = widget.get_selection_bounds()
        if bounds:
            start, end = bounds
            widget.delete_text(start, end)
            position = start
        else:
            position = widget.get_position()
        widget.insert_text(text, position)
        widget.set_position(position + len(text))

    clipboard.read_text_async(None, on_read_text)
    return True


def _select_all_editable(widget) -> bool:
    widget.select_region(0, -1)
    return True


def _delete_previous_word_editable(widget) -> bool:
    bounds = widget.get_selection_bounds()
    if bounds:
        start, end = bounds
        widget.delete_text(start, end)
        widget.set_position(start)
        return True

    text = widget.get_text()
    position = widget.get_position()
    if position <= 0:
        return False

    start = position
    while start > 0 and text[start - 1].isspace():
        start -= 1
    while start > 0 and not text[start - 1].isspace():
        start -= 1

    if start == position:
        return False

    widget.delete_text(start, position)
    widget.set_position(start)
    return True


def _delete_previous_word_text_view(widget) -> bool:
    buffer = widget.get_buffer()
    bounds = buffer.get_selection_bounds()
    if bounds:
        start_iter, end_iter = bounds
        buffer.delete(start_iter, end_iter)
        return True

    end_iter = buffer.get_iter_at_mark(buffer.get_insert())
    start_iter = end_iter.copy()
    probe = start_iter.copy()

    while probe.backward_char():
        char = probe.get_char()
        if not char.isspace():
            start_iter = probe.copy()
            break
        start_iter = probe.copy()
    else:
        buffer.delete(start_iter, end_iter)
        return True

    if start_iter.inside_word() or start_iter.starts_word():
        start_iter.backward_word_start()

    buffer.delete(start_iter, end_iter)
    return True


def _delete_to_line_start_editable(widget) -> bool:
    bounds = widget.get_selection_bounds()
    if bounds:
        start, end = bounds
        widget.delete_text(start, end)
        widget.set_position(start)
        return True

    position = widget.get_position()
    if position <= 0:
        return False

    widget.delete_text(0, position)
    widget.set_position(0)
    return True


def _delete_to_line_start_text_view(widget) -> bool:
    buffer = widget.get_buffer()
    bounds = buffer.get_selection_bounds()
    if bounds:
        start_iter, end_iter = bounds
        buffer.delete(start_iter, end_iter)
        return True

    end_iter = buffer.get_iter_at_mark(buffer.get_insert())
    if end_iter.is_start():
        return False

    start_iter = end_iter.copy()
    start_iter.set_line_offset(0)

    if start_iter.equal(end_iter) and start_iter.backward_char():
        buffer.delete(start_iter, end_iter)
        return True

    buffer.delete(start_iter, end_iter)
    return True


def handle_text_input_key_pressed(widget, keyval, state) -> bool:
    from gi.repository import Gdk, Gtk

    lowered = Gdk.keyval_to_lower(keyval)
    focus = widget
    primary_pressed = has_primary_modifier(state)

    if primary_pressed:
        if lowered == Gdk.KEY_c:
            if isinstance(focus, Gtk.Editable):
                return _copy_editable(focus)
            return bool(focus.activate_action("clipboard.copy", None))
        if lowered == Gdk.KEY_x:
            if isinstance(focus, Gtk.Editable):
                return _cut_editable(focus)
            return bool(focus.activate_action("clipboard.cut", None))
        if lowered == Gdk.KEY_v:
            if isinstance(focus, Gtk.Editable):
                return _paste_editable(focus)
            return bool(focus.activate_action("clipboard.paste", None))
        if lowered == Gdk.KEY_a:
            if isinstance(focus, Gtk.Editable):
                return _select_all_editable(focus)
            return bool(focus.activate_action("selection.select-all", None))
        if lowered == Gdk.KEY_z:
            if state & Gdk.ModifierType.SHIFT_MASK:
                return bool(focus.activate_action("text.redo", None))
            return bool(focus.activate_action("text.undo", None))
        if keyval == Gdk.KEY_BackSpace:
            if isinstance(focus, Gtk.Editable):
                return _delete_to_line_start_editable(focus)
            if isinstance(focus, Gtk.TextView):
                return _delete_to_line_start_text_view(focus)

    if _is_alt_pressed(state) and keyval == Gdk.KEY_BackSpace:
        if isinstance(focus, Gtk.Editable):
            return _delete_previous_word_editable(focus)
        if isinstance(focus, Gtk.TextView):
            return _delete_previous_word_text_view(focus)

    return False


def install_window_text_input_handlers(window) -> None:
    from gi.repository import Gtk

    controller = Gtk.EventControllerKey.new()

    def on_key_pressed(_controller, keyval, keycode, state):
        focus = window.get_focus()
        if focus is None:
            return False
        return handle_text_input_key_pressed(focus, keyval, state)

    controller.connect("key-pressed", on_key_pressed)
    window.add_controller(controller)

def can_escape_sandbox() -> bool:
    """
    Check if we can escape the sandbox 

    Returns:
        bool: True if we can escape the sandbox
    """
    if not is_flatpak():
        return True
    try:
        r = subprocess.check_output(["flatpak-spawn", "--host", "echo", "test"])
    except subprocess.CalledProcessError as _:
        return False
    return True

def get_spawn_command() -> list:
    """
    Get the spawn command to run commands on the user system

    Returns:
        list: space diveded command  
    """
    if is_flatpak():
        return ["flatpak-spawn", "--host"]
    else:
        return []

def get_open_command(target: str) -> list[str]:
    """Return the platform-specific command used to open a path or URL."""
    if sys.platform == "darwin":
        return ["open", target]
    if os.name == "nt":
        return ["cmd", "/c", "start", "", target]
    return get_spawn_command() + ["xdg-open", target]

def open_target(target: str):
    """Open a path or URL using the native platform opener."""
    subprocess.Popen(get_open_command(target))

def open_website(website):
    """Open a website using the native platform opener.

    Args:
        website (): url of the website 
    """
    open_target(website)

def open_folder(folder):
    """Open a folder using the native platform opener.

    Args:
        folder (): location of the folder 
    """
    open_target(folder)

def has_backend(backend: str, spawn: bool = True) -> bool:
    """Check if a GPU/compute backend is available on the system.

    Args:
        backend: One of "cuda", "rocm", "vulkan", "openvino"
        spawn: If True, use get_spawn_command() prefix for subprocess calls

    Returns:
        bool: True if the backend appears to be available
    """
    cmd_prefix = get_spawn_command() if spawn else []

    def _run_check(cmd):
        try:
            result = subprocess.run(
                cmd_prefix + cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _path_check(path):
        return os.path.exists(path)

    if backend == "cuda":
        if _run_check(["nvidia-smi"]):
            return True
        return _path_check("/proc/driver/nvidia/version")

    elif backend == "rocm":
        if _run_check(["rocminfo"]):
            return True
        return _path_check("/opt/rocm")

    elif backend == "vulkan":
        if _run_check(["vulkaninfo"]):
            return True
        icd_dir = "/usr/share/vulkan/icd.d"
        if os.path.isdir(icd_dir):
            return any(f.endswith(".json") for f in os.listdir(icd_dir))
        return False

    elif backend == "openvino":
        return _run_check(["python3", "-c", "import openvino"])

    return False


def detect_cuda_version() -> float | None:
    """Detect the installed CUDA runtime version.

    Tries nvcc first, then falls back to nvidia-smi output.

    Returns:
        The major.minor CUDA version as a float (e.g. 12.8, 13.2, 11.7),
        or None if CUDA is not found.
    """
    cmd_prefix = get_spawn_command()

    try:
        result = subprocess.run(
            cmd_prefix + ["nvcc", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"release\s+(\d+)\.(\d+)", result.stdout)
            if match:
                return float(f"{match.group(1)}.{match.group(2)}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            cmd_prefix + ["nvidia-smi"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            match = re.search(r"CUDA Version:\s+(\d+)\.(\d+)", result.stdout)
            if match:
                return float(f"{match.group(1)}.{match.group(2)}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None

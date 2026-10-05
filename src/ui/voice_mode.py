from __future__ import annotations

import ctypes
import ctypes.util
import math
import os
import struct
import tempfile
import threading
import time
import warnings
import wave
from collections import deque
from enum import Enum

import gi
import pyaudio

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..controller import NewelleController
from ..ui_controller import HeadlessController
from ..utility.strings import clean_message_tts, remove_thinking_blocks
from ..utility.system import (
    get_flatpak_x11_override_command,
    has_flatpak_x11_permission,
    is_flatpak,
)
from ..utility.vad import VoiceActivityDetector

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
except (ImportError, ValueError):
    Gtk4LayerShell = None

try:
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11
except (ImportError, ValueError):
    GdkX11 = None


VOICE_CSS = """
.voice-mode-root {
    padding: 6px;
}
.voice-mode-window {
    background-color: transparent;
}
.voice-pill-shell {
    min-width: 240px;
    min-height: 56px;
    padding: 0 10px;
    border-radius: 999px;
    color: @window_fg_color;
    background-color: alpha(@window_bg_color, 0.96);
    border: 1px solid alpha(@window_fg_color, 0.13);
    box-shadow: 0 10px 30px alpha(black, 0.24);
}
.voice-wave-bar {
    min-width: 3px;
    border-radius: 999px;
    background-color: @accent_bg_color;
}
.voice-pill-status {
    font-size: 0.9em;
    font-weight: 600;
}
.voice-pill-status-icon {
    opacity: 0.9;
}
.voice-interaction-card {
    min-width: 390px;
    max-width: 520px;
    border-radius: 22px;
    padding: 14px;
    margin-bottom: 8px;
    overflow: hidden;
    color: @window_fg_color;
    background-color: alpha(@window_bg_color, 0.98);
    border: 1px solid alpha(@window_fg_color, 0.13);
    box-shadow: 0 12px 36px alpha(black, 0.28);
}
.voice-interaction-card.voice-interaction-below {
    margin-top: 8px;
    margin-bottom: 0;
}
.voice-interaction-title {
    font-weight: 700;
}
.voice-feedback-message {
    font-size: 0.95em;
    font-weight: 500;
    line-height: 1.45;
    opacity: 0.94;
}
.voice-feedback-widget {
    margin-top: 2px;
}
.voice-tool-placeholder {
    padding: 6px 2px;
    opacity: 0.88;
}
.voice-mode-error .voice-wave-bar {
    background-color: @error_bg_color;
}
.voice-mode-waiting .voice-wave-bar {
    background-color: @warning_bg_color;
}
"""


class VoiceSessionState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    RUNNING = "running"
    WAITING = "waiting"
    SPEAKING = "speaking"
    ERROR = "error"
    CLOSING = "closing"


class VoiceFeedbackMode(Enum):
    REQUIRED = "required"
    TOOLS = "tools"
    FULL = "full"


class VoiceCaptureDecision(Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    NO_SPEECH = "no-speech"


class VoiceEndpointController:
    """Pure timing state used around the shared VAD implementation."""

    def __init__(self, started_at, start_timeout, endpoint_debounce):
        self.speech_started = False
        self.start_deadline = started_at + start_timeout
        self.endpoint_debounce = endpoint_debounce
        self.finalize_deadline = None

    def observe(self, is_speech, started, ended, now):
        if started:
            self.speech_started = True
            self.finalize_deadline = None
        if ended and self.speech_started:
            self.finalize_deadline = now + self.endpoint_debounce
        if (
            self.finalize_deadline is not None
            and not is_speech
            and now >= self.finalize_deadline
        ):
            return VoiceCaptureDecision.COMPLETE
        if not self.speech_started and now >= self.start_deadline:
            return VoiceCaptureDecision.NO_SPEECH
        return VoiceCaptureDecision.CONTINUE


class VoiceSessionController:
    """Own cancellation and interactive results independently of the window."""

    def __init__(self):
        self.state = VoiceSessionState.IDLE
        self.cancel_event = threading.Event()
        self.pending_results = set()

    def transition(self, state):
        if self.cancel_event.is_set() and state not in {
            VoiceSessionState.ERROR,
            VoiceSessionState.CLOSING,
        }:
            return False
        self.state = state
        return True

    def track_interaction(self, result):
        if self.cancel_event.is_set():
            result.cancel()
            return False
        self.pending_results.add(result)
        return True

    def resolve_interaction(self, result):
        self.pending_results.discard(result)

    def fail(self):
        self.state = VoiceSessionState.ERROR
        self.cancel_event.set()

    def complete(self):
        if self.cancel_event.is_set():
            return False
        self.state = VoiceSessionState.IDLE
        return True

    def cancel(self):
        self.state = VoiceSessionState.CLOSING
        self.cancel_event.set()
        for result in tuple(self.pending_results):
            result.cancel()


class MeanWaveform(Gtk.Box):
    """One five-bar wave showing the mean envelope for input and output."""

    def __init__(self, animations_enabled: bool = True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self._animations_enabled = animations_enabled
        self._bars = []
        for _ in range(5):
            bar = Gtk.Box(css_classes=["voice-wave-bar"], valign=Gtk.Align.CENTER)
            bar.set_size_request(3, 5)
            self._bars.append(bar)
            self.append(bar)
        self._smoothed_level = 0.0
        self._phase = 0.0
        self._animation_source = None
        self.set_idle()

    def set_input_level(self, level: float):
        self.stop_animation()
        level = max(0.0, min(1.0, float(level)))
        self._smoothed_level = self._smoothed_level * 0.68 + level * 0.32
        self._render(self._smoothed_level)
        return GLib.SOURCE_REMOVE

    def start_output_animation(self):
        if not self._animations_enabled:
            self._render(0.52)
            return
        if self._animation_source is None:
            self._phase = 0.0
            self._animation_source = GLib.timeout_add(55, self._animate_output)

    def _animate_output(self):
        try:
            if self.get_parent() is None:
                self._animation_source = None
                return GLib.SOURCE_REMOVE
        except Exception:
            self._animation_source = None
            return GLib.SOURCE_REMOVE
        self._phase += 0.34
        level = 0.43 + 0.28 * math.sin(self._phase) + 0.12 * math.sin(self._phase * 2.3)
        self._render(max(0.12, min(1.0, level)), animated=True)
        return GLib.SOURCE_CONTINUE

    def _render(self, level: float, animated: bool = False):
        shape = (0.45, 0.72, 1.0, 0.72, 0.45)
        for index, bar in enumerate(self._bars):
            variation = 1.0
            if animated:
                left = 0.78 + 0.22 * math.sin(self._phase + index * 0.8)
                right = 0.78 + 0.22 * math.sin(
                    self._phase + index * 0.8 + 0.45
                )
                variation = (left + right) / 2
            height = 4 + int(18 * level * shape[index] * variation)
            bar.set_size_request(3, max(4, height))

    def set_idle(self):
        self.stop_animation()
        self._smoothed_level = 0.0
        self._render(0.08)

    def stop_animation(self):
        if self._animation_source is not None:
            try:
                GLib.source_remove(self._animation_source)
            except Exception:
                pass
            self._animation_source = None


class XSizeHints(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_long),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("min_width", ctypes.c_int),
        ("min_height", ctypes.c_int),
        ("max_width", ctypes.c_int),
        ("max_height", ctypes.c_int),
        ("width_inc", ctypes.c_int),
        ("height_inc", ctypes.c_int),
        ("min_aspect_x", ctypes.c_int),
        ("min_aspect_y", ctypes.c_int),
        ("max_aspect_x", ctypes.c_int),
        ("max_aspect_y", ctypes.c_int),
        ("base_width", ctypes.c_int),
        ("base_height", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
    ]


class XClientMessageData(ctypes.Union):
    _fields_ = [("l", ctypes.c_long * 5)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", XClientMessageData),
    ]


class VoicePillX11Helper:
    """Helper for managing window position and hints via X11 / XWayland."""

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.available = False
        self.libx11 = None
        self.xdisplay = None
        self.root = None
        self._error_handler_ref = None
        self._init_x11()

    def _init_x11(self):
        if GdkX11 is None:
            return
        disp_name = os.getenv("DISPLAY")
        if not disp_name:
            return
        libname = ctypes.util.find_library("X11") or "libX11.so.6"
        try:
            self.libx11 = ctypes.cdll.LoadLibrary(libname)
        except Exception as exc:
            print(f"Voice Mode: Failed to load libX11: {exc}")
            return

        self.libx11.XOpenDisplay.restype = ctypes.c_void_p
        self.libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.libx11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.libx11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.libx11.XInternAtom.restype = ctypes.c_ulong
        self.libx11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.libx11.XChangeProperty.restype = ctypes.c_int
        self.libx11.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int
        ]
        self.libx11.XGetWindowProperty.restype = ctypes.c_int
        self.libx11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p)
        ]
        self.libx11.XFree.restype = ctypes.c_int
        self.libx11.XFree.argtypes = [ctypes.c_void_p]
        self.libx11.XGetGeometry.restype = ctypes.c_int
        self.libx11.XGetGeometry.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)
        ]
        self.libx11.XMoveWindow.restype = ctypes.c_int
        self.libx11.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        self.libx11.XSendEvent.restype = ctypes.c_int
        self.libx11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
        self.libx11.XFlush.restype = ctypes.c_int
        self.libx11.XFlush.argtypes = [ctypes.c_void_p]
        self.libx11.XSetWMNormalHints.restype = None
        self.libx11.XSetWMNormalHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XSizeHints)]

        error_handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        self._error_handler_ref = error_handler_type(lambda disp, err: 0)
        self.libx11.XSetErrorHandler.argtypes = [error_handler_type]
        self.libx11.XSetErrorHandler.restype = error_handler_type
        try:
            self.libx11.XSetErrorHandler(self._error_handler_ref)
        except Exception:
            pass

        try:
            self.xdisplay = self.libx11.XOpenDisplay(disp_name.encode())
            if not self.xdisplay:
                return
            self.root = self.libx11.XDefaultRootWindow(self.xdisplay)
        except Exception as exc:
            print(f"Voice Mode: Failed to open X11 display: {exc}")
            return

        self.atom_type = self._intern(b"_NET_WM_WINDOW_TYPE")
        self.atom_utility = self._intern(b"_NET_WM_WINDOW_TYPE_UTILITY")
        self.atom_state = self._intern(b"_NET_WM_STATE")
        self.atom_above = self._intern(b"_NET_WM_STATE_ABOVE")
        self.atom_sticky = self._intern(b"_NET_WM_STATE_STICKY")
        self.atom_skip_taskbar = self._intern(b"_NET_WM_STATE_SKIP_TASKBAR")
        self.atom_skip_pager = self._intern(b"_NET_WM_STATE_SKIP_PAGER")
        self.atom_desktop = self._intern(b"_NET_WM_DESKTOP")
        self.atom_workarea = self._intern(b"_NET_WORKAREA")
        self.atom_atom = self._intern(b"ATOM")
        self.atom_cardinal = self._intern(b"CARDINAL")

        self.available = True

    def _intern(self, name: bytes) -> int:
        return self.libx11.XInternAtom(self.xdisplay, name, False)

    @staticmethod
    def get_x11_gdk_display():
        if GdkX11 is None:
            return None
        dm = Gdk.DisplayManager.get()
        for d in dm.list_displays():
            if isinstance(d, GdkX11.X11Display) and not d.is_closed():
                return d
        disp_name = os.getenv("DISPLAY")
        if disp_name:
            try:
                return Gdk.Display.open(disp_name)
            except Exception:
                return None
        return None

    def setup_window(self, xid: int, initial_x: int, initial_y: int):
        if not self.available or not xid:
            return
        try:
            type_arr = (ctypes.c_ulong * 1)(self.atom_utility)
            self.libx11.XChangeProperty(
                self.xdisplay, xid, self.atom_type, self.atom_atom,
                32, 0, ctypes.cast(type_arr, ctypes.c_void_p), 1
            )
            state_arr = (ctypes.c_ulong * 4)(
                self.atom_above, self.atom_sticky,
                self.atom_skip_taskbar, self.atom_skip_pager
            )
            self.libx11.XChangeProperty(
                self.xdisplay, xid, self.atom_state, self.atom_atom,
                32, 0, ctypes.cast(state_arr, ctypes.c_void_p), 4
            )
            desktop_arr = (ctypes.c_ulong * 1)(0xFFFFFFFF)
            self.libx11.XChangeProperty(
                self.xdisplay, xid, self.atom_desktop, self.atom_cardinal,
                32, 0, ctypes.cast(desktop_arr, ctypes.c_void_p), 1
            )
            hints = XSizeHints()
            hints.flags = (1 << 0) | (1 << 1)
            hints.x = initial_x
            hints.y = initial_y
            self.libx11.XSetWMNormalHints(self.xdisplay, xid, ctypes.byref(hints))

            self.libx11.XMoveWindow(self.xdisplay, xid, initial_x, initial_y)
            self.libx11.XFlush(self.xdisplay)
        except Exception as exc:
            print(f"Voice Mode: X11 setup_window error: {exc}")

    def enforce_state_on_map(self, xid: int):
        if not self.available or not xid:
            return
        try:
            mask = (1 << 20) | (1 << 19)
            for prop in (self.atom_above, self.atom_sticky):
                ev = XClientMessageEvent()
                ev.type = 33
                ev.window = xid
                ev.message_type = self.atom_state
                ev.format = 32
                ev.data.l[0] = 1
                ev.data.l[1] = prop
                ev.data.l[2] = 0
                ev.data.l[3] = 1
                ev.data.l[4] = 0
                self.libx11.XSendEvent(self.xdisplay, self.root, False, mask, ctypes.byref(ev))
            self.libx11.XFlush(self.xdisplay)
        except Exception as exc:
            print(f"Voice Mode: X11 enforce_state error: {exc}")

    def move_window(self, xid: int, x: int, y: int):
        if not self.available or not xid:
            return
        try:
            self.libx11.XMoveWindow(self.xdisplay, xid, x, y)
            self.libx11.XFlush(self.xdisplay)
        except Exception as exc:
            print(f"Voice Mode: X11 move_window error: {exc}")

    def get_window_size(self, xid: int):
        if not self.available or not xid:
            return None
        try:
            root_ret = ctypes.c_ulong()
            x_ret, y_ret = ctypes.c_int(), ctypes.c_int()
            w_ret, h_ret = ctypes.c_uint(), ctypes.c_uint()
            bw_ret, d_ret = ctypes.c_uint(), ctypes.c_uint()
            status = self.libx11.XGetGeometry(
                self.xdisplay, xid,
                ctypes.byref(root_ret),
                ctypes.byref(x_ret), ctypes.byref(y_ret),
                ctypes.byref(w_ret), ctypes.byref(h_ret),
                ctypes.byref(bw_ret), ctypes.byref(d_ret),
            )
            if status != 0 and w_ret.value > 1 and h_ret.value > 1:
                return int(w_ret.value), int(h_ret.value)
        except Exception:
            pass
        return None

    def get_workarea(self):
        if not self.available:
            return None
        try:
            actual_type = ctypes.c_ulong()
            actual_format = ctypes.c_int()
            nitems = ctypes.c_ulong()
            bytes_after = ctypes.c_ulong()
            prop = ctypes.c_void_p()
            status = self.libx11.XGetWindowProperty(
                self.xdisplay, self.root, self.atom_workarea,
                0, 32, False, 0,
                ctypes.byref(actual_type), ctypes.byref(actual_format),
                ctypes.byref(nitems), ctypes.byref(bytes_after),
                ctypes.byref(prop)
            )
            if status == 0 and prop and nitems.value >= 4:
                try:
                    data = ctypes.cast(prop, ctypes.POINTER(ctypes.c_long * nitems.value)).contents
                    return (int(data[0]), int(data[1]), int(data[2]), int(data[3]))
                finally:
                    self.libx11.XFree(prop)
        except Exception:
            pass
        return None


class VoiceWorkspaceUIController(HeadlessController):
    """Keep voice tools bound to their workspace while displaying requested UI."""

    def __init__(self, controller, display_ui):
        super().__init__(controller)
        self.display_ui = display_ui
        self.chat_id = None

    def add_tab(self, child, focus=True):
        return self.display_ui.add_tab(child, focus)

    def new_browser_tab(self, url=None, new=True):
        return self.display_ui.new_browser_tab(url, new)

    def open_link(self, url, new=False, use_integrated_browser=True):
        return self.display_ui.open_link(url, new, use_integrated_browser)

    def new_explorer_tab(self, path, new=True):
        return self.display_ui.new_explorer_tab(path, new)

    def new_editor_tab(self, file):
        return self.display_ui.new_editor_tab(file)

    def get_current_chat_id(self):
        context = self.controller._request_context()
        if context is not None:
            return context["chat_id"]
        return self.chat_id if self.chat_id is not None else super().get_current_chat_id()

    def get_tool_result_by_id(self, tool_uuid):
        prefix = "[Tool: "
        for entry in self.controller.get_chat_by_id(self.get_current_chat_id()):
            message = entry.get("Message", "")
            if entry.get("User") == "Console" and message.startswith(prefix):
                header, separator, output = message.partition("\n")
                if header.endswith(f", ID: {tool_uuid}]"):
                    return output if separator else ""
        return None


class VoiceModeWindow(Gtk.Window):
    """Desktop-anchored speech → tools → TTS conversation surface."""

    SAMPLE_RATE = 16000
    CHUNK_SIZE = 512
    START_TIMEOUT_SECONDS = 15.0
    ENDPOINT_DEBOUNCE_SECONDS = 0.5

    def __init__(self, application, main_window, on_closed=None, **kwargs):
        # Choose the display before registering with Gtk.Application. Its
        # native backend is tied to the default display, not each window's.
        super().__init__(**kwargs)
        self._application = application
        self._application_held = False
        self.main_window = main_window
        self.controller = main_window.controller
        self._workspace_controller = None
        self._workspace_cleanup_source = None
        self.settings = self.controller.settings
        self.on_closed = on_closed
        self.session = VoiceSessionController()
        self.chat_id = None
        self._chat_mode = self.settings.get_string("voice-mode-chat")
        self._selected_chat_id = self.controller.newelle_settings.chat_id
        self._cancel_event = self.session.cancel_event
        self._recording_thread = None
        self._capture_thread = None
        self._processing_thread = None
        self._pending_results = self.session.pending_results
        self._tts_tokens = []
        self._tts_handler = None
        self._tts_playback_generation = 0
        self._owns_tts = False
        self._resume_wakeword = False
        self._wakeword_windows = []
        self._destroying = False
        self._layer_shell_active = False
        self._x11_active = False
        self._x11_surface = None
        self._x11_xid = None
        self._last_x11_pos = None
        self._surface_width_handler = None
        self._surface_height_handler = None
        self._monitors_changed_handler = None
        self._css_provider = None
        gtk_settings = Gtk.Settings.get_default()
        self._animations_enabled = gtk_settings is None or bool(
            gtk_settings.get_property("gtk-enable-animations")
        )
        self._transition_ms = 200 if self._animations_enabled else 0
        self._open_transition_ms = 280 if self._animations_enabled else 0
        self._close_transition_ms = 180 if self._animations_enabled else 0
        self._content_transition_ms = 170 if self._animations_enabled else 0
        self._settings_changed_handler = None
        self._interaction_hide_source = None
        self._feedback_text_source = None
        self._content_reveal_source = None
        self._close_animation_source = None
        self._close_wait_source = None
        self._wakeword_release_source = None
        self._capture_restart_source = None
        self._tts_finish_source = None
        self._widget_timeouts = set()
        self._open_animation_started = False
        self._close_animation_started = False
        self._closing = False
        self._teardown_started = False
        self._closed_notified = False
        self._feedback_mode = VoiceFeedbackMode.REQUIRED
        self._feedback_message = ""
        self._feedback_shown_text = ""
        self._feedback_pending_text = None
        self._feedback_widgets = []
        self._feedback_placeholders = []
        self._feedback_interactive = False
        self._feedback_title = _("Action required")

        self.set_title(_("Newelle Voice Mode"))
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_focusable(False)
        self.set_default_size(240, -1)
        self.add_css_class("voice-mode-window")
        self._build_ui()
        self._configure_position()
        self._attach_application()
        self._maybe_prompt_x11_override()
        self._apply_css()
        self._settings_changed_handler = self.settings.connect(
            "changed", self._on_setting_changed
        )
        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

    def _attach_application(self):
        application = self._application
        if self.get_display() == Gdk.Display.get_default():
            self.set_application(application)
            return

        # A Wayland Gtk.Application must not own an X11 fallback window:
        # window removal otherwise passes its X11 surface to Wayland cleanup
        # and can segfault inside libwayland-client. Keep the application
        # alive explicitly and expose actions without registering the window.
        application.hold()
        self._application_held = True
        self.insert_action_group("app", application)
        shortcuts = Gtk.ShortcutController()
        shortcuts.set_scope(Gtk.ShortcutScope.GLOBAL)
        for accelerator in application.get_accels_for_action("app.voice_mode"):
            trigger = Gtk.ShortcutTrigger.parse_string(accelerator)
            if trigger is not None:
                shortcuts.add_shortcut(Gtk.Shortcut(
                    trigger=trigger,
                    action=Gtk.NamedAction.new("app.voice_mode"),
                ))
        self.add_controller(shortcuts)

    @property
    def state(self):
        return self.session.state

    @state.setter
    def state(self, value):
        self.session.state = value

    def _build_ui(self):
        self.root_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            css_classes=["voice-mode-root"],
        )

        self.interaction_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=self._transition_ms,
            reveal_child=False,
        )
        # A vertically sliding revealer still contributes its child's width
        # while collapsed. Hide it entirely until feedback is shown so the
        # 390px card cannot stretch the normal 240px pill.
        self.interaction_revealer.set_visible(False)
        self.interaction_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            css_classes=["voice-interaction-card"],
        )
        self.interaction_title = Gtk.Label(
            label=_("Action required"),
            xalign=0,
            css_classes=["voice-interaction-title"],
        )
        self.interaction_title.set_wrap(True)
        self.interaction_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.interaction_card.append(self.interaction_title)
        self.feedback_text_revealer = Gtk.Revealer(
            transition_type=self._content_slide_transition(),
            transition_duration=self._content_transition_ms,
            reveal_child=False,
        )
        self.feedback_message_label = Gtk.Label(
            xalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            css_classes=["voice-feedback-message"],
        )
        self.feedback_message_label.set_max_width_chars(48)
        self.feedback_text_revealer.set_child(self.feedback_message_label)
        self.interaction_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            max_content_height=360,
            propagate_natural_height=True,
        )
        self.interaction_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self.interaction_box.append(self.feedback_text_revealer)
        self.interaction_scroll.set_child(self.interaction_box)
        self.interaction_card.append(self.interaction_scroll)
        self.interaction_revealer.set_child(self.interaction_card)
        self.root_box.append(self.interaction_revealer)
        self._sync_feedback_mode()

        self.pill = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            css_classes=["voice-pill-shell"],
        )
        self.waveform = MeanWaveform(self._animations_enabled)
        self.status_icon = Gtk.Image(
            icon_name="audio-input-microphone-symbolic",
            css_classes=["voice-pill-status-icon"],
        )
        self.status_icon.set_pixel_size(16)
        self.status_spinner = Gtk.Spinner(
            css_classes=["voice-pill-status-icon"],
        )
        self.status_spinner.set_size_request(16, 16)
        self.status_indicator = Gtk.Stack(
            hhomogeneous=True,
            vhomogeneous=True,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        self.status_indicator.set_size_request(16, 16)
        self.status_indicator.add_named(self.status_icon, "icon")
        self.status_indicator.add_named(self.status_spinner, "spinner")
        self.status_indicator.set_visible_child_name("icon")
        self.status_label = Gtk.Label(
            css_classes=["voice-pill-status"],
            ellipsize=Pango.EllipsizeMode.END,
            width_chars=22,
            max_width_chars=22,
            single_line_mode=True,
            xalign=0.5,
        )
        status_slot = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
        )
        status_slot.set_size_request(28, -1)
        status_slot.append(self.status_indicator)
        waveform_slot = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
        )
        waveform_slot.set_size_request(28, -1)
        waveform_slot.append(self.waveform)
        self.pill_content = Gtk.CenterBox(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self.pill_content.set_start_widget(status_slot)
        self.pill_content.set_center_widget(self.status_label)
        self.pill_content.set_end_widget(waveform_slot)
        self.pill_content_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=self._content_transition_ms,
            reveal_child=not self._animations_enabled,
            hexpand=True,
        )
        self.pill_content_revealer.set_child(self.pill_content)
        self.pill.append(self.pill_content_revealer)
        self._set_status(_("Ready"))
        self.root_box.append(self.pill)

        self.motion_revealer = Gtk.Revealer(
            transition_type=self._motion_transition(),
            transition_duration=self._open_transition_ms,
            reveal_child=not self._animations_enabled,
        )
        self.motion_revealer.set_child(self.root_box)
        self._sync_interaction_layout()
        self.set_child(self.motion_revealer)

    def _motion_transition(self):
        """Choose motion that makes the pill emerge from its desktop edge."""
        position = self.settings.get_string("voice-mode-position") or "bottom-center"
        vertical, _, horizontal = position.partition("-")
        transitions = Gtk.RevealerTransitionType
        if vertical == "top":
            return getattr(
                transitions, "FADE_SLIDE_DOWN", transitions.SLIDE_DOWN
            )
        if vertical == "bottom":
            return getattr(
                transitions, "FADE_SLIDE_UP", transitions.SLIDE_UP
            )
        if horizontal == "left":
            return getattr(
                transitions, "FADE_SLIDE_RIGHT", transitions.SLIDE_RIGHT
            )
        if horizontal == "right":
            return getattr(
                transitions, "FADE_SLIDE_LEFT", transitions.SLIDE_LEFT
            )
        return transitions.CROSSFADE

    def _sync_interaction_layout(self):
        position = self.settings.get_string("voice-mode-position")
        interaction_below = position.startswith("top-")
        if hasattr(self, "motion_revealer"):
            self.motion_revealer.set_transition_type(self._motion_transition())
        self.interaction_card.remove_css_class("voice-interaction-below")
        if interaction_below:
            self.interaction_card.add_css_class("voice-interaction-below")
            self.interaction_revealer.set_transition_type(
                Gtk.RevealerTransitionType.SLIDE_DOWN
            )
            self.root_box.reorder_child_after(self.pill, None)
        else:
            self.interaction_revealer.set_transition_type(
                Gtk.RevealerTransitionType.SLIDE_UP
            )
            self.root_box.reorder_child_after(self.interaction_revealer, None)
        if hasattr(self, "feedback_text_revealer"):
            self.feedback_text_revealer.set_transition_type(
                self._content_slide_transition()
            )

    def _sync_feedback_mode(self):
        value = self.settings.get_string("voice-mode-feedback") or "required"
        try:
            self._feedback_mode = VoiceFeedbackMode(value)
        except ValueError:
            self._feedback_mode = VoiceFeedbackMode.REQUIRED

    def _content_slide_transition(self):
        position = self.settings.get_string("voice-mode-position") or "bottom-center"
        transitions = Gtk.RevealerTransitionType
        if position.startswith("top-"):
            return getattr(transitions, "FADE_SLIDE_DOWN", transitions.SLIDE_DOWN)
        return getattr(transitions, "FADE_SLIDE_UP", transitions.SLIDE_UP)

    def _on_setting_changed(self, _settings, key):
        if self._closing or self._destroying:
            return
        if key in {"voice-mode-position", "voice-mode-margin"}:
            self._sync_interaction_layout()
            if self._layer_shell_active:
                self._apply_layer_anchors()
            elif self._x11_active:
                self._apply_x11_position()
        elif key == "voice-mode-feedback":
            self._sync_feedback_mode()
            self._refresh_feedback_card()
        elif key in {
            "voice-pill-theme",
            "voice-pill-background",
            "voice-pill-foreground",
            "voice-pill-accent",
            "voice-pill-opacity",
        }:
            self._apply_css()

    def _apply_css(self):
        self._remove_css_provider()
        theme = self.settings.get_string("voice-pill-theme")
        opacity = max(0.55, min(1.0, self.settings.get_double("voice-pill-opacity")))
        if theme == "light":
            background, foreground, accent = "#ffffff", "#202124", "#3584e4"
        elif theme == "dark":
            background, foreground, accent = "#202124", "#f7f7f8", "#78aeed"
        elif theme == "custom":
            background = self._valid_color(
                self.settings.get_string("voice-pill-background"), "#202124"
            )
            foreground = self._valid_color(
                self.settings.get_string("voice-pill-foreground"), "#f7f7f8"
            )
            accent = self._valid_color(
                self.settings.get_string("voice-pill-accent"), "#78aeed"
            )
        else:
            background = foreground = accent = None

        css = VOICE_CSS
        if background is not None:
            css += f"""
            .voice-pill-shell, .voice-interaction-card {{
                color: {foreground};
                background-color: alpha({background}, {opacity});
                border-color: alpha({foreground}, 0.14);
            }}
            .voice-wave-bar {{ background-color: {accent}; }}
            """
        elif opacity != 0.96:
            css += f"""
            .voice-pill-shell, .voice-interaction-card {{
                background-color: alpha(@window_bg_color, {opacity});
            }}
            """

        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(css.encode())
        display = self.get_display() or Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )
            app = self._application
            if (
                display != Gdk.Display.get_default()
                and app is not None
                and getattr(app, "style_provider", None) is not None
            ):
                try:
                    Gtk.StyleContext.add_provider_for_display(
                        display,
                        app.style_provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
                except Exception:
                    pass

    def _remove_css_provider(self):
        display = self.get_display() or Gdk.Display.get_default()
        if display is not None and self._css_provider is not None:
            try:
                Gtk.StyleContext.remove_provider_for_display(display, self._css_provider)
            except Exception:
                pass
        self._css_provider = None

    @staticmethod
    def _valid_color(value: str, fallback: str) -> str:
        rgba = Gdk.RGBA()
        return value if value and rgba.parse(value) else fallback

    def _configure_position(self):
        if Gtk4LayerShell is not None and Gtk4LayerShell.is_supported():
            try:
                Gtk4LayerShell.init_for_window(self)
                Gtk4LayerShell.set_namespace(self, "newelle-voice-mode")
                Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)
                Gtk4LayerShell.set_exclusive_zone(self, 0)
                Gtk4LayerShell.set_keyboard_mode(
                    self, Gtk4LayerShell.KeyboardMode.NONE
                )
                self._layer_shell_active = True
                self._apply_layer_anchors()
                return
            except Exception as exc:
                self._layer_shell_active = False
                print(f"Voice Mode: Layer Shell unavailable: {exc}")

        self._setup_x11_positioning()

    def _maybe_prompt_x11_override(self):
        if self._layer_shell_active or self._x11_active:
            return
        if not is_flatpak() or has_flatpak_x11_permission():
            return
        if self.settings.get_boolean("voice-mode-x11-override-dont-show"):
            return
        GLib.idle_add(self._show_x11_override_dialog)

    def _show_x11_override_dialog(self):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        command = get_flatpak_x11_override_command()
        dialog = Adw.AlertDialog(
            heading=_("X11 access needed"),
            body=_(
                "To position Voice Mode correctly, we need X11 access. "
                "Run this command, then restart Newelle."
            ),
        )
        dialog.add_response("dont-show", _("Don't show again"))
        dialog.add_response("ok", _("OK"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")

        command_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            hexpand=True,
        )
        command_label = Gtk.Label(
            label=command,
            xalign=0,
            hexpand=True,
            selectable=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            css_classes=["monospace"],
        )
        copy_button = Gtk.Button(
            icon_name="edit-copy-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Copy command"),
        )

        def copy_command(_button):
            display = self.get_display() or Gdk.Display.get_default()
            if display is None:
                return
            display.get_clipboard().set(command)

        copy_button.connect("clicked", copy_command)
        command_row.append(command_label)
        command_row.append(copy_button)
        dialog.set_extra_child(command_row)
        dialog.connect("response", self._on_x11_override_response)
        parent = self
        if self.main_window is not None and self.main_window.get_mapped():
            parent = self.main_window
        dialog.present(parent)
        return GLib.SOURCE_REMOVE

    def _on_x11_override_response(self, _dialog, response):
        if response == "dont-show":
            self.settings.set_boolean("voice-mode-x11-override-dont-show", True)

    def _setup_x11_positioning(self):
        helper = VoicePillX11Helper.get()
        if not helper.available:
            return
        x11_d = helper.get_x11_gdk_display()
        if x11_d is None:
            return
        if self.get_display() != x11_d:
            self.set_display(x11_d)
        self._x11_active = True
        self.connect("realize", self._on_x11_realize)
        self.connect("map", self._on_x11_map)
        try:
            monitors = x11_d.get_monitors()
            self._monitors_changed_handler = monitors.connect(
                "items-changed", lambda *a: self._apply_x11_position()
            )
        except Exception:
            pass

    def _on_x11_realize(self, _widget):
        s = self.get_surface()
        self._x11_surface = s
        if s is None:
            return
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                self._x11_xid = s.get_xid()
            except Exception:
                self._x11_xid = None
            try:
                s.set_skip_taskbar_hint(True)
                s.set_skip_pager_hint(True)
                s.set_user_time(0)
            except Exception:
                pass

        if self._x11_xid is not None:
            self._surface_width_handler = s.connect(
                "notify::width", lambda *a: self._apply_x11_position()
            )
            self._surface_height_handler = s.connect(
                "notify::height", lambda *a: self._apply_x11_position()
            )
            helper = VoicePillX11Helper.get()
            init_x, init_y = self._calculate_x11_coordinates()
            helper.setup_window(self._x11_xid, init_x, init_y)
            self._last_x11_pos = (init_x, init_y)

    def _on_x11_map(self, _widget):
        if self._x11_xid is not None:
            helper = VoicePillX11Helper.get()
            helper.enforce_state_on_map(self._x11_xid)
            self._apply_x11_position()

    def _get_target_x11_monitor(self):
        display = self.get_display()
        if display is None:
            return None
        monitors = display.get_monitors()
        if monitors.get_n_items() == 0:
            return None

        target_connector = None
        target_geom = None
        if self.main_window is not None:
            try:
                main_surface = self.main_window.get_surface()
                if main_surface is not None:
                    main_disp = self.main_window.get_display()
                    main_mon = main_disp.get_monitor_at_surface(main_surface)
                    if main_mon is not None:
                        target_connector = main_mon.get_connector()
                        target_geom = main_mon.get_geometry()
            except Exception:
                pass

        if target_connector or target_geom:
            for i in range(monitors.get_n_items()):
                mon = monitors.get_item(i)
                if target_connector and mon.get_connector() == target_connector:
                    return mon
                mg = mon.get_geometry()
                if (
                    target_geom
                    and mg.x == target_geom.x
                    and mg.y == target_geom.y
                    and mg.width == target_geom.width
                    and mg.height == target_geom.height
                ):
                    return mon

        if hasattr(display, "get_primary_monitor"):
            primary = display.get_primary_monitor()
            if primary is not None:
                return primary

        return monitors.get_item(0)

    def _calculate_x11_coordinates(self):
        monitor = self._get_target_x11_monitor()
        if monitor is None:
            return 0, 0
        geom = monitor.get_geometry()
        helper = VoicePillX11Helper.get()
        wa = helper.get_workarea()
        if wa is not None:
            wa_x, wa_y, wa_w, wa_h = wa
            x_start = max(geom.x, wa_x)
            y_start = max(geom.y, wa_y)
            x_end = min(geom.x + geom.width, wa_x + wa_w)
            y_end = min(geom.y + geom.height, wa_y + wa_h)
            if x_end > x_start and y_end > y_start:
                eff_x = x_start
                eff_y = y_start
                eff_w = x_end - x_start
                eff_h = y_end - y_start
            else:
                eff_x, eff_y, eff_w, eff_h = geom.x, geom.y, geom.width, geom.height
        else:
            eff_x, eff_y, eff_w, eff_h = geom.x, geom.y, geom.width, geom.height

        win_w = 0
        win_h = 0
        if self._x11_xid is not None:
            size = helper.get_window_size(self._x11_xid)
            if size is not None:
                win_w, win_h = size
        if win_w <= 1 or win_h <= 1:
            win_w = self.get_width()
            win_h = self.get_height()
        if win_w <= 1 or win_h <= 1:
            try:
                min_s, nat_s = self.get_preferred_size()
                win_w = (
                    nat_s.width
                    if nat_s.width > 1
                    else (min_s.width if min_s.width > 1 else 240)
                )
                win_h = (
                    nat_s.height
                    if nat_s.height > 1
                    else (min_s.height if min_s.height > 1 else 56)
                )
            except Exception:
                win_w, win_h = 240, 56

        position = self.settings.get_string("voice-mode-position") or "bottom-center"
        valid_positions = {
            f"{vertical}-{horizontal}"
            for vertical in ("top", "center", "bottom")
            for horizontal in ("left", "center", "right")
        }
        if position not in valid_positions:
            position = "bottom-center"
        vertical, horizontal = position.split("-", 1)
        margin = max(0, self.settings.get_int("voice-mode-margin"))

        if horizontal == "left":
            x = eff_x + margin
        elif horizontal == "right":
            x = eff_x + eff_w - win_w - margin
        else:
            x = eff_x + (eff_w - win_w) // 2

        if vertical == "top":
            y = eff_y + margin
        elif vertical == "bottom":
            y = eff_y + eff_h - win_h - margin
        else:
            y = eff_y + (eff_h - win_h) // 2

        if eff_w >= win_w:
            x = max(eff_x, min(x, eff_x + eff_w - win_w))
        if eff_h >= win_h:
            y = max(eff_y, min(y, eff_y + eff_h - win_h))

        return int(x), int(y)

    def _apply_x11_position(self):
        if self._closing or self._destroying or not self._x11_active or self._x11_xid is None:
            return
        target_x, target_y = self._calculate_x11_coordinates()
        if self._last_x11_pos == (target_x, target_y):
            return
        self._last_x11_pos = (target_x, target_y)
        helper = VoicePillX11Helper.get()
        helper.move_window(self._x11_xid, target_x, target_y)

    def _apply_layer_anchors(self):
        if not self._layer_shell_active:
            return
        position = self.settings.get_string("voice-mode-position") or "bottom-center"
        valid_positions = {
            f"{vertical}-{horizontal}"
            for vertical in ("top", "center", "bottom")
            for horizontal in ("left", "center", "right")
        }
        if position not in valid_positions:
            position = "bottom-center"
        vertical, horizontal = position.split("-", 1)
        edges = {
            "top": Gtk4LayerShell.Edge.TOP,
            "bottom": Gtk4LayerShell.Edge.BOTTOM,
            "left": Gtk4LayerShell.Edge.LEFT,
            "right": Gtk4LayerShell.Edge.RIGHT,
        }
        for edge in edges.values():
            Gtk4LayerShell.set_anchor(self, edge, False)
            Gtk4LayerShell.set_margin(self, edge, 0)
        margin = max(0, self.settings.get_int("voice-mode-margin"))
        if vertical in edges:
            Gtk4LayerShell.set_anchor(self, edges[vertical], True)
            Gtk4LayerShell.set_margin(self, edges[vertical], margin)
        if horizontal in edges:
            Gtk4LayerShell.set_anchor(self, edges[horizontal], True)
            Gtk4LayerShell.set_margin(self, edges[horizontal], margin)

    def start(self):
        if self._closing or self._destroying or self.state is not VoiceSessionState.IDLE:
            return
        self._animate_open()
        try:
            self._select_workspace()
        except Exception as error:  # noqa: BLE001 - Provider setup may raise third-party exceptions.
            self._show_error(str(error))
            return
        if self._microphone_busy():
            self._show_error(_("Microphone in use"))
            return

        tts = getattr(self.controller.handlers, "tts", None)
        if tts is not None:
            tts.stop()

        self._wakeword_windows = [
            window for window in self._application.main_windows
            if getattr(window, "wakeword_listening", False)
        ]
        self._resume_wakeword = bool(self._wakeword_windows)
        if self._resume_wakeword:
            for window in self._wakeword_windows:
                window.stop_wakeword_detection()
            self._wakeword_release_deadline = time.monotonic() + 3.0
            self._wakeword_release_source = GLib.timeout_add(
                25, self._start_after_wakeword_release
            )
        else:
            self._start_capture()

    def _select_workspace(self):
        """Resolve the voice workspace once, keeping the visible window unchanged."""
        source = self.main_window.controller
        workspace_id = self.settings.get_string("voice-mode-workspace")
        if workspace_id in ("", "current") or workspace_id not in source.workspaces:
            workspace_id = source.active_workspace_id
        if workspace_id == source.active_workspace_id:
            self.controller = source
        else:
            window = self._application.workspace_window(workspace_id)
            if window is not None and getattr(window, "ui_built", False):
                self.controller = window.controller
            else:
                controller = NewelleController(
                    source.python_path, shared_controller=source,
                    workspace_id=workspace_id, window_settings=True,
                )
                self._workspace_controller = controller
                # Saving a background voice chat must preserve the visible
                # window's workspace selection for the next application start.
                controller.history_selection_controller = source
                controller.ui_init()
                controller.set_ui_controller(VoiceWorkspaceUIController(controller, source.ui_controller))
                self.controller = controller
        self._selected_chat_id = self.controller.newelle_settings.chat_id

    def _animate_open(self):
        if (
            self._open_animation_started
            or self._close_animation_started
            or self._closing
            or self._destroying
        ):
            return
        self._open_animation_started = True
        self.motion_revealer.set_transition_duration(self._open_transition_ms)
        self.motion_revealer.set_reveal_child(True)
        if not self._animations_enabled:
            self.pill_content_revealer.set_reveal_child(True)
            return
        # Let the shell establish its silhouette before fading in the live
        # waveform and status. The slight stagger keeps the entrance calm.
        self._content_reveal_source = GLib.timeout_add(
            65, self._reveal_pill_content
        )

    def _reveal_pill_content(self):
        self._content_reveal_source = None
        if (
            not self._close_animation_started
            and not self._closing
            and not self._destroying
        ):
            self.pill_content_revealer.set_reveal_child(True)
        return GLib.SOURCE_REMOVE

    def _start_after_wakeword_release(self):
        if self._cancel_event.is_set() or self._closing or self._destroying:
            self._wakeword_release_source = None
            return GLib.SOURCE_REMOVE
        detectors = [getattr(window, "wakeword_detector", None) for window in self._wakeword_windows]
        if any(detector is not None and not detector.is_stopped() for detector in detectors):
            if time.monotonic() < self._wakeword_release_deadline:
                return GLib.SOURCE_CONTINUE
            self._show_error(_("Microphone is still busy"))
            self._wakeword_release_source = None
            return GLib.SOURCE_REMOVE
        self._wakeword_release_source = None
        self._start_capture()
        return GLib.SOURCE_REMOVE

    def _microphone_busy(self) -> bool:
        for window in self._application.main_windows:
            if getattr(window, "recording", False) or getattr(window, "_recording_stopping", False):
                return True
            tab_view = getattr(window, "canvas_tabs", None)
            if tab_view is not None:
                for index in range(tab_view.get_n_pages()):
                    child = tab_view.get_nth_page(index).get_child()
                    if getattr(child, "call_active", False):
                        return True
        return False

    def _start_capture(self):
        if self._cancel_event.is_set() or self._closing or self._destroying:
            return
        self._reset_feedback()
        self._set_state(VoiceSessionState.LISTENING)
        thread = threading.Thread(
            target=self._capture_one_utterance,
            name="newelle-voice-capture",
            daemon=True,
        )
        self._recording_thread = thread
        self._capture_thread = thread
        thread.start()

    def _capture_one_utterance(self):
        audio = None
        stream = None
        vad = VoiceActivityDetector(self.SAMPLE_RATE)
        prebuffer = deque(maxlen=int(self.SAMPLE_RATE / self.CHUNK_SIZE) + 1)
        frames = []
        endpoint = VoiceEndpointController(
            time.monotonic(),
            self.START_TIMEOUT_SECONDS,
            self.ENDPOINT_DEBOUNCE_SECONDS,
        )
        try:
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.SAMPLE_RATE,
                input=True,
                frames_per_buffer=self.CHUNK_SIZE,
            )
            while not self._cancel_event.is_set():
                data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                self._queue_input_level(data)
                prebuffer.append(data)
                is_speech, started, ended = vad.process_chunk(data)
                had_speech = endpoint.speech_started
                decision = endpoint.observe(
                    is_speech, started, ended, time.monotonic()
                )

                if endpoint.speech_started and not had_speech:
                    frames = list(prebuffer)
                elif endpoint.speech_started:
                    frames.append(data)

                if decision is VoiceCaptureDecision.COMPLETE:
                    break
                if decision is VoiceCaptureDecision.NO_SPEECH:
                    GLib.idle_add(self._show_error, _("No speech detected"))
                    return
        except Exception as exc:
            print(f"Voice Mode capture error: {exc}")
            GLib.idle_add(self._show_error, _("Microphone unavailable"))
            return
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                try:
                    audio.terminate()
                except Exception:
                    pass
            self._recording_thread = None

        if self._cancel_event.is_set() or self._closing or self._destroying:
            return
        if not frames:
            GLib.idle_add(self._show_error, _("No speech detected"))
            return
        self._processing_thread = threading.Thread(
            target=self._process_capture,
            args=(b"".join(frames),),
            name="newelle-voice-request",
            daemon=True,
        )
        self._processing_thread.start()

    def _queue_input_level(self, data: bytes):
        if self._closing or self._destroying:
            return
        try:
            count = len(data) // 2
            if count == 0:
                return
            samples = struct.unpack("<" + str(count) + "h", data)
            rms = math.sqrt(sum(sample * sample for sample in samples) / count)
            GLib.idle_add(self._apply_input_level, min(1.0, rms / 9000.0))
        except Exception:
            pass

    def _apply_input_level(self, level: float):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        try:
            self.waveform.set_input_level(level)
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _process_capture(self, audio_data: bytes):
        audio_path = None
        cancel_event = self._cancel_event
        try:
            if self._cancel_event.is_set() or self._closing or self._destroying:
                return
            GLib.idle_add(self._set_state, VoiceSessionState.TRANSCRIBING)
            stt = getattr(self.controller.handlers, "stt", None)
            direct_audio = self.controller.settings.get_boolean("direct-audio-input")
            if not direct_audio and (stt is None or not stt.is_installed()):
                GLib.idle_add(self._show_error, _("Speech recognition unavailable"))
                return

            with tempfile.NamedTemporaryFile(
                dir=self.controller.cache_dir,
                prefix="voice_mode_",
                suffix=".wav",
                delete=False,
            ) as temporary_file:
                audio_path = temporary_file.name
            with wave.open(audio_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.SAMPLE_RATE)
                wav_file.writeframes(audio_data)

            if direct_audio:
                text = self.controller.audio_input.prepare_audio_input(
                    audio_path, lambda: not cancel_event.is_set() and not self._closing and not self._destroying)
            else:
                text = stt.recognize_file(audio_path)
            if self._cancel_event.is_set():
                return
            if not text or not text.strip():
                GLib.idle_add(self._show_error, _("I didn't catch that"))
                return

            llm = getattr(self.controller.handlers, "llm", None)
            if llm is None or not llm.is_installed():
                GLib.idle_add(self._show_error, _("Language model unavailable"))
                return

            GLib.idle_add(self._set_state, VoiceSessionState.RUNNING)
            # Keep prior turns when capture restarts after each response.
            if self.chat_id not in self.controller.chats:
                self.chat_id = self.controller.get_voice_chat(
                    self._chat_mode, self._selected_chat_id
                )
            if isinstance(self.controller.ui_controller, VoiceWorkspaceUIController):
                self.controller.ui_controller.chat_id = self.chat_id
            configured_mode = self.settings.get_string("voice-mode-mode")
            mode_name = None if configured_mode in ("", "current") else configured_mode
            if (
                mode_name is not None
                and self.controller.mode_manager.get_mode(mode_name) is None
            ):
                mode_name = None

            def on_tool_result(tool_name, result):
                GLib.idle_add(self._on_tool_result, tool_name, result)

            def on_tool_start(tool_name):
                status_ready = threading.Event()
                GLib.idle_add(self._on_tool_start, tool_name, status_ready)
                status_ready.wait(0.25)
                if self._animations_enabled:
                    time.sleep(0.02)

            def on_message(text):
                GLib.idle_add(self._on_stream_message, text)

            def on_intermediate_message(text):
                self._play_response(
                    clean_message_tts(str(text or "")).strip(),
                    final=False,
                )

            previous_call_request = self.controller.is_call_request
            self.controller.is_call_request = True
            try:
                response = self.controller.run_llm_with_tools(
                    message=text.strip(),
                    is_current=lambda: not cancel_event.is_set() and not self._closing and not self._destroying,
                    chat_id=self.chat_id,
                    on_message_callback=on_message,
                    on_tool_result_callback=on_tool_result,
                    on_tool_start_callback=on_tool_start,
                    on_intermediate_message_callback=on_intermediate_message,
                    save_chat=True,
                    force_tools_on_main_thread=True,
                    mode_name=mode_name,
                )
            finally:
                self.controller.is_call_request = previous_call_request

            if self._cancel_event.is_set():
                return
            spoken_response = clean_message_tts(response or "")
            if not spoken_response:
                GLib.idle_add(self._complete_without_tts)
                return
            self._play_response(spoken_response, final=True)
        except Exception as exc:
            import traceback

            print(f"Voice Mode request error: {exc}")
            print(traceback.format_exc())
            GLib.idle_add(self._show_error, str(exc))
        finally:
            self._processing_thread = None
            if audio_path is not None:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def _play_response(self, response: str, final: bool):
        if not response or self._cancel_event.is_set() or self._closing or self._destroying:
            return
        tts = getattr(self.controller.handlers, "tts", None)
        if tts is None or not tts.is_installed():
            if final:
                GLib.idle_add(self._show_error, _("Speech synthesis unavailable"))
            return
        self._disconnect_tts()
        self._tts_playback_generation += 1
        playback_generation = self._tts_playback_generation
        self._tts_tokens = [
            tts.connect(
                "start",
                lambda: self._on_tts_start(response, playback_generation),
            ),
            tts.connect(
                "stop",
                lambda: self._on_tts_stop(final, playback_generation),
            ),
        ]
        self._tts_handler = tts
        self._owns_tts = True
        try:
            tts.play(response)
        except Exception as exc:
            print(f"Voice Mode TTS error: {exc}")
            if final:
                GLib.idle_add(self._show_error, _("Speech playback failed"))
            else:
                GLib.idle_add(self._set_state, VoiceSessionState.RUNNING)
        finally:
            self._owns_tts = False

    def _on_tts_start(self, response, playback_generation):
        GLib.idle_add(
            self._set_speaking_status, response, playback_generation
        )

    def _set_speaking_status(self, response, playback_generation):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        if playback_generation != self._tts_playback_generation:
            return GLib.SOURCE_REMOVE
        self._set_state(VoiceSessionState.SPEAKING)
        self._set_status(response)
        if self._feedback_mode is VoiceFeedbackMode.FULL and not self._feedback_message:
            display = self._display_message_text(response)
            if display:
                self._queue_feedback_message(display)
        return GLib.SOURCE_REMOVE

    def _on_tts_stop(self, final, playback_generation):
        # Playback signals can fire on a worker. Marshal the delayed
        # completion so GLib sources are only touched on the main loop.
        GLib.idle_add(
            self._schedule_finish_after_speech,
            final,
            playback_generation,
        )

    def _schedule_finish_after_speech(self, final, playback_generation):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        self._clear_source("_tts_finish_source")
        self._tts_finish_source = GLib.timeout_add(
            50,
            self._finish_after_speech,
            final,
            playback_generation,
        )
        return GLib.SOURCE_REMOVE

    def _finish_after_speech(self, final, playback_generation):
        self._tts_finish_source = None
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        if playback_generation != self._tts_playback_generation:
            return GLib.SOURCE_REMOVE
        if not self._cancel_event.is_set():
            if final:
                self._finish_voice_interaction()
            elif self.state is VoiceSessionState.SPEAKING:
                self._set_state(VoiceSessionState.RUNNING)
        return GLib.SOURCE_REMOVE

    def _complete_without_tts(self):
        if self._cancel_event.is_set():
            return GLib.SOURCE_REMOVE
        self._finish_voice_interaction()
        return GLib.SOURCE_REMOVE

    def _finish_voice_interaction(self):
        """Finish the current request and arm capture for the next one."""
        if self._closing or self._destroying or not self.session.complete():
            return
        # The TTS handler is shared with the rest of the application. Once its
        # response is finished, detach this interaction's listeners before
        # listening again so unrelated playback cannot re-trigger completion.
        self._tts_playback_generation += 1
        self._disconnect_tts()
        self._set_status(_("Ready"), "audio-input-microphone-symbolic")
        self.waveform.set_idle()
        if self._capture_restart_source is None:
            self._capture_restart_source = GLib.timeout_add(
                25, self._restart_capture_when_ready
            )

    def _restart_capture_when_ready(self):
        """Restart only after the previous capture/request fully released."""
        if self._closing or self._destroying or self._cancel_event.is_set():
            self._capture_restart_source = None
            return GLib.SOURCE_REMOVE
        if (
            not self._capture_released()
            or self._processing_thread is not None
            or self._pending_results
        ):
            return GLib.SOURCE_CONTINUE
        self._capture_restart_source = None
        self._start_capture()
        return GLib.SOURCE_REMOVE

    def _on_stream_message(self, text):
        if self._cancel_event.is_set() or self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        display = self._display_message_text(text)
        if not display:
            return GLib.SOURCE_REMOVE
        preview = clean_message_tts(display).strip() or display
        self._set_status(preview, "brain-augemnted-symbolic")
        if self._feedback_mode is VoiceFeedbackMode.FULL:
            self._queue_feedback_message(display)
        return GLib.SOURCE_REMOVE

    def _on_tool_result(self, tool_name, result):
        if self._cancel_event.is_set() or self._closing or self._destroying:
            result.cancel()
            return GLib.SOURCE_REMOVE
        tool_title, tool_icon = self._tool_presentation(tool_name)
        display_text = getattr(result, "display_text", None)
        if display_text:
            self._set_status(display_text, tool_icon)

        if result.requires_interaction:
            if not self.session.track_interaction(result):
                return GLib.SOURCE_REMOVE
            self._set_state(VoiceSessionState.WAITING)
            self._set_status(
                _("{tool} needs input").format(tool=tool_title),
                tool_icon,
            )
            self._feedback_title = _("{tool} needs your input").format(tool=tool_title)
            self._feedback_interactive = True
            widget = self._widget_for_result(result)
            if widget is not None:
                self._add_feedback_widget(widget)
            threading.Thread(
                target=self._wait_for_interaction,
                args=(result,),
                daemon=True,
            ).start()
        elif (
            self._feedback_mode is not VoiceFeedbackMode.REQUIRED
            and result.widget is not None
        ):
            if display_text:
                self._feedback_title = display_text
            else:
                self._feedback_title = tool_title
            self._add_feedback_widget(result.widget)
        else:
            self._remove_tool_placeholder(tool_name)
            return GLib.SOURCE_REMOVE

        self._refresh_feedback_card()
        return GLib.SOURCE_REMOVE

    def _on_tool_start(self, tool_name, status_ready=None):
        if self._cancel_event.is_set() or self._closing or self._destroying:
            if status_ready is not None:
                status_ready.set()
            return GLib.SOURCE_REMOVE
        tool_title, tool_icon = self._tool_presentation(tool_name)
        self._set_state(VoiceSessionState.RUNNING)
        self._set_status(
            _("Running {tool}…").format(
                tool=tool_title
            ),
            tool_icon,
        )
        if self._feedback_mode is not VoiceFeedbackMode.REQUIRED:
            self._feedback_title = _("Running {tool}…").format(tool=tool_title)
            self._ensure_tool_placeholder(tool_name, tool_title, tool_icon)
            self._refresh_feedback_card()
        if status_ready is not None:
            status_ready.set()
        return GLib.SOURCE_REMOVE

    def _tool_presentation(self, tool_name):
        tool = self.controller.tools.get_tool(tool_name)
        if tool is None:
            return tool_name.replace("_", " ").title(), "system-run-symbolic"
        return tool.title, tool.icon_name or "system-run-symbolic"

    def _wait_for_interaction(self, result):
        result.get_output()
        GLib.idle_add(self._finish_interaction, result)

    def _finish_interaction(self, result):
        self.session.resolve_interaction(result)
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        if not self._pending_results:
            self._feedback_interactive = False
            self._release_feedback_focus()
            if self._feedback_mode is VoiceFeedbackMode.REQUIRED:
                self._hide_feedback_card()
            else:
                self._refresh_feedback_card()
            if not self._cancel_event.is_set():
                self._set_state(VoiceSessionState.RUNNING)
        return GLib.SOURCE_REMOVE

    def _display_message_text(self, text):
        cleaned = remove_thinking_blocks(str(text or "")).strip()
        if not cleaned:
            return ""
        tts = clean_message_tts(cleaned).strip()
        return tts or cleaned

    def _set_feedback_message(self, text):
        incoming = str(text or "").strip()
        if not incoming:
            return False
        shown = self._feedback_shown_text
        if not shown:
            self._feedback_message = incoming
        elif incoming.startswith(shown) or shown.startswith(incoming):
            self._feedback_message = incoming if len(incoming) >= len(shown) else shown
        elif incoming in shown:
            return False
        elif shown in incoming:
            self._feedback_message = incoming
        else:
            self._feedback_message = incoming
        changed = self._feedback_message != shown
        self._feedback_shown_text = self._feedback_message
        return changed

    def _queue_feedback_message(self, text):
        if not self._set_feedback_message(text):
            return
        if not self._animations_enabled:
            self._refresh_feedback_card()
            return
        self._feedback_pending_text = self._feedback_message
        if self._feedback_text_source is None:
            self._feedback_text_source = GLib.timeout_add(
                55, self._flush_feedback_message
            )

    def _flush_feedback_message(self):
        self._feedback_text_source = None
        self._feedback_pending_text = None
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        self._refresh_feedback_card()
        return GLib.SOURCE_REMOVE

    def _widget_for_result(self, result):
        widget = result.widget
        if widget is not None:
            return widget
        if not result.interaction_options:
            return None
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for option in result.interaction_options:
            button = Gtk.Button(label=option.title)
            button.connect("clicked", lambda _button, cb=option.callback: cb())
            button_box.append(button)
        return button_box

    def _adopt_widget(self, widget):
        parent = widget.get_parent()
        if parent is not None and hasattr(parent, "remove"):
            parent.remove(widget)
        widget.set_hexpand(True)
        if not widget.has_css_class("voice-feedback-widget"):
            widget.add_css_class("voice-feedback-widget")
        return widget

    def _card_is_open(self):
        if self._closing or self._destroying:
            return False
        try:
            return self.interaction_revealer.get_visible() and (
                self.interaction_revealer.get_reveal_child()
                or self._interaction_hide_source is not None
            )
        except Exception:
            return False

    def _wrap_feedback_widget(self, widget):
        widget = self._adopt_widget(widget)
        already_open = self._card_is_open()
        revealer = Gtk.Revealer(
            transition_type=self._content_slide_transition(),
            transition_duration=self._content_transition_ms if already_open else 0,
            reveal_child=not self._animations_enabled or not already_open,
        )
        revealer.set_child(widget)
        return revealer

    def _add_feedback_widget(self, widget):
        if widget is None or self._closing or self._destroying:
            return
        for placeholder in list(self._feedback_placeholders):
            placeholder.set_reveal_child(False)
            if placeholder.get_parent() is self.interaction_box:
                if self._content_transition_ms:
                    self._schedule_remove_feedback_child(placeholder)
                else:
                    self._remove_feedback_child(placeholder)
            self._feedback_placeholders.remove(placeholder)
        for existing in self._feedback_widgets:
            if existing.get_child() is widget:
                return
        revealer = self._wrap_feedback_widget(widget)
        self._feedback_widgets.append(revealer)
        self.interaction_box.append(revealer)
        if self._animations_enabled and self._card_is_open():
            GLib.idle_add(self._reveal_feedback_child, revealer)

    def _ensure_tool_placeholder(self, tool_name, tool_title, tool_icon):
        if self._closing or self._destroying:
            return
        for placeholder in self._feedback_placeholders:
            child = placeholder.get_child()
            if getattr(child, "_tool_name", None) == tool_name:
                return
        if self._feedback_widgets:
            return
        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            css_classes=["voice-tool-placeholder"],
            valign=Gtk.Align.CENTER,
        )
        row._tool_name = tool_name
        icon = Gtk.Image(icon_name=tool_icon)
        icon.set_pixel_size(16)
        spinner = Gtk.Spinner()
        spinner.start()
        label = Gtk.Label(
            label=tool_title,
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        row.append(icon)
        row.append(label)
        row.append(spinner)
        revealer = self._wrap_feedback_widget(row)
        self._feedback_placeholders.append(revealer)
        self.interaction_box.append(revealer)
        if self._animations_enabled and self._card_is_open():
            GLib.idle_add(self._reveal_feedback_child, revealer)

    def _reveal_feedback_child(self, revealer):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        try:
            revealer.set_transition_duration(self._content_transition_ms)
            revealer.set_reveal_child(True)
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _remove_tool_placeholder(self, tool_name):
        remaining = []
        for placeholder in self._feedback_placeholders:
            child = placeholder.get_child()
            if getattr(child, "_tool_name", None) == tool_name:
                placeholder.set_reveal_child(False)
                if placeholder.get_parent() is self.interaction_box:
                    if self._content_transition_ms:
                        self._schedule_remove_feedback_child(placeholder)
                    else:
                        self._remove_feedback_child(placeholder)
            else:
                remaining.append(placeholder)
        self._feedback_placeholders = remaining

    def _schedule_remove_feedback_child(self, widget):
        source_id = None

        def run():
            self._widget_timeouts.discard(source_id)
            return self._remove_feedback_child(widget)

        source_id = GLib.timeout_add(self._content_transition_ms, run)
        self._widget_timeouts.add(source_id)

    def _remove_feedback_child(self, widget):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        try:
            parent = widget.get_parent()
        except Exception:
            return GLib.SOURCE_REMOVE
        if parent is self.interaction_box:
            parent.remove(widget)
        return GLib.SOURCE_REMOVE

    def _scroll_feedback_to_bottom(self):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        try:
            adjustment = self.interaction_scroll.get_vadjustment()
            if adjustment is not None:
                adjustment.set_value(adjustment.get_upper())
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _feedback_has_content(self):
        show_text = (
            self._feedback_mode is VoiceFeedbackMode.FULL
            and bool(self._feedback_message)
        )
        show_widgets = bool(self._feedback_widgets) or (
            self._feedback_mode is not VoiceFeedbackMode.REQUIRED
            and bool(self._feedback_placeholders)
        )
        if self._feedback_mode is VoiceFeedbackMode.REQUIRED:
            return self._feedback_interactive
        if self._feedback_mode is VoiceFeedbackMode.TOOLS:
            return show_widgets
        return show_text or show_widgets or self._feedback_interactive

    def _refresh_feedback_card(self):
        if self._closing or self._destroying:
            return
        try:
            self._refresh_feedback_card_body()
        except Exception:
            return

    def _refresh_feedback_card_body(self):
        show_text = (
            self._feedback_mode is VoiceFeedbackMode.FULL
            and bool(self._feedback_message)
        )
        show_title = bool(
            self._feedback_interactive
            or self._feedback_widgets
            or self._feedback_placeholders
        )
        if show_text:
            if self.feedback_message_label.get_label() != self._feedback_message:
                self.feedback_message_label.set_label(self._feedback_message)
            if show_title:
                self.interaction_title.set_label(self._feedback_title or _("Reply"))
        elif show_title:
            self.interaction_title.set_label(self._feedback_title or _("Action required"))
        self.interaction_title.set_visible(show_title)
        if show_text:
            already_open = self._card_is_open()
            self.feedback_text_revealer.set_visible(True)
            self.feedback_text_revealer.set_transition_duration(
                self._content_transition_ms if already_open else 0
            )
            self.feedback_text_revealer.set_reveal_child(True)
            if already_open:
                self.feedback_text_revealer.set_transition_duration(
                    self._content_transition_ms
                )
        else:
            self.feedback_text_revealer.set_transition_duration(self._content_transition_ms)
            self.feedback_text_revealer.set_reveal_child(False)
            if not self._content_transition_ms:
                self.feedback_text_revealer.set_visible(False)

        if not self._feedback_has_content():
            self._hide_feedback_card()
            return

        self._clear_source("_interaction_hide_source")
        self.interaction_revealer.set_visible(True)
        self.interaction_revealer.set_reveal_child(True)
        if show_text or self._feedback_widgets:
            GLib.idle_add(self._scroll_feedback_to_bottom)
        if self._feedback_interactive:
            self._request_feedback_focus()
        else:
            self._release_feedback_focus()

    def _request_feedback_focus(self):
        if self._closing or self._destroying:
            return
        self.set_focusable(True)
        if self._layer_shell_active:
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.ON_DEMAND
            )
        elif self._x11_active and self._x11_surface is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                try:
                    display = self.get_display()
                    user_time = (
                        display.get_user_time()
                        if hasattr(display, "get_user_time")
                        else 0
                    )
                    self._x11_surface.set_user_time(user_time)
                except Exception:
                    pass
        self.present()

    def _release_feedback_focus(self):
        if self._destroying:
            return
        try:
            self.set_focusable(False)
            if self._layer_shell_active:
                Gtk4LayerShell.set_keyboard_mode(
                    self, Gtk4LayerShell.KeyboardMode.NONE
                )
            elif self._x11_active and self._x11_surface is not None:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    try:
                        self._x11_surface.set_user_time(0)
                    except Exception:
                        pass
        except Exception:
            pass

    def _hide_feedback_card(self):
        if self._closing or self._destroying:
            return
        try:
            if not self.interaction_revealer.get_reveal_child() and not self.interaction_revealer.get_visible():
                return
            self.feedback_text_revealer.set_reveal_child(False)
            self.interaction_revealer.set_reveal_child(False)
        except Exception:
            return
        self._release_feedback_focus()
        if self._transition_ms:
            self._clear_source("_interaction_hide_source")
            self._interaction_hide_source = GLib.timeout_add(
                self._transition_ms, self._hide_interaction_card
            )
        else:
            self._hide_interaction_card()

    def _hide_interaction_card(self):
        self._interaction_hide_source = None
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        try:
            if self.interaction_revealer.get_reveal_child() or self._feedback_has_content():
                return GLib.SOURCE_REMOVE
            self.interaction_revealer.set_visible(False)
            self.feedback_text_revealer.set_visible(False)
            self.set_default_size(240, -1)
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _clear_feedback_widgets(self):
        for revealer in list(self._feedback_widgets) + list(self._feedback_placeholders):
            try:
                parent = revealer.get_parent()
                if parent is self.interaction_box:
                    parent.remove(revealer)
            except Exception:
                pass
        self._feedback_widgets = []
        self._feedback_placeholders = []

    def _reset_feedback(self):
        self._feedback_message = ""
        self._feedback_shown_text = ""
        self._feedback_pending_text = None
        self._feedback_interactive = False
        self._feedback_title = _("Action required")
        self._clear_source("_feedback_text_source")
        try:
            self.feedback_message_label.set_label("")
            self.feedback_text_revealer.set_reveal_child(False)
        except Exception:
            return
        self._clear_feedback_widgets()
        self._hide_feedback_card()

    def _set_state(self, state: VoiceSessionState):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        if self._cancel_event.is_set() and state is not VoiceSessionState.CLOSING:
            return GLib.SOURCE_REMOVE
        if not self.session.transition(state):
            return GLib.SOURCE_REMOVE
        try:
            self._set_status(
                self._state_label(state),
                self._state_icon(state),
                spinning=state is VoiceSessionState.RUNNING,
            )
            self.root_box.remove_css_class("voice-mode-error")
            self.root_box.remove_css_class("voice-mode-waiting")
            if state is VoiceSessionState.SPEAKING:
                self.waveform.start_output_animation()
            else:
                self.waveform.set_idle()
            if state is VoiceSessionState.WAITING:
                self.root_box.add_css_class("voice-mode-waiting")
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _state_label(state: VoiceSessionState):
        return {
            VoiceSessionState.IDLE: _("Ready"),
            VoiceSessionState.LISTENING: _("Listening"),
            VoiceSessionState.TRANSCRIBING: _("Transcribing"),
            VoiceSessionState.RUNNING: _("Working"),
            VoiceSessionState.WAITING: _("Action needed"),
            VoiceSessionState.SPEAKING: _("Speaking"),
            VoiceSessionState.ERROR: _("Voice error"),
            VoiceSessionState.CLOSING: _("Closing"),
        }[state]

    @staticmethod
    def _state_icon(state: VoiceSessionState):
        return {
            VoiceSessionState.IDLE: "audio-input-microphone-symbolic",
            VoiceSessionState.LISTENING: "audio-input-microphone-symbolic",
            VoiceSessionState.TRANSCRIBING: "document-edit-symbolic",
            VoiceSessionState.RUNNING: "process-working-symbolic",
            VoiceSessionState.WAITING: "dialog-warning-symbolic",
            VoiceSessionState.SPEAKING: "audio-volume-high-symbolic",
            VoiceSessionState.ERROR: "dialog-error-symbolic",
            VoiceSessionState.CLOSING: "window-close-symbolic",
        }[state]

    def _set_status(self, text, icon_name=None, spinning=False):
        if self._closing or self._destroying:
            return
        try:
            if spinning:
                self.status_indicator.set_visible_child_name("spinner")
                self.status_spinner.start()
            elif icon_name:
                self.status_spinner.stop()
                self.status_icon.set_from_icon_name(icon_name)
                self.status_indicator.set_visible_child_name("icon")
            self.status_label.set_label(text)
            self.pill.set_tooltip_text(text)
            self.pill.update_property([Gtk.AccessibleProperty.LABEL], [text])
        except Exception:
            pass

    def _show_error(self, message: str):
        if self._closing or self._destroying:
            return GLib.SOURCE_REMOVE
        self.session.fail()
        try:
            self._set_status(message, "dialog-error-symbolic")
            self.root_box.add_css_class("voice-mode-error")
            self.waveform.set_idle()
            self._reset_feedback()
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def is_closing(self):
        return self._closing or self._destroying

    def cancel(self):
        if self._closing or self._destroying:
            return
        # Stop capture, tools, and playback immediately; only the lightweight
        # surface teardown waits for the visual exit transition and for the
        # capture worker to release PortAudio.
        self._closing = True
        self.session.cancel()
        tts = self._tts_handler or getattr(self.controller.handlers, "tts", None)
        if tts is not None and (self._owns_tts or self._tts_handler is tts):
            try:
                tts.stop()
            except Exception:
                pass
        self._clear_session_sources()
        GLib.idle_add(self._animate_close)

    def _animate_close(self):
        if self._destroying or self._close_animation_started:
            return GLib.SOURCE_REMOVE
        self._close_animation_started = True
        self._clear_source("_content_reveal_source")
        try:
            self.pill_content_revealer.set_reveal_child(False)
            mapped = self.get_mapped()
            revealing = self.motion_revealer.get_reveal_child()
        except Exception:
            return self._finalize_close()

        if not self._animations_enabled or not mapped or not revealing:
            return self._finalize_close()

        self.motion_revealer.set_transition_duration(self._close_transition_ms)
        self.motion_revealer.set_reveal_child(False)
        # Keep the transparent window alive for the transition plus one frame,
        # then release all session resources.
        self._close_animation_source = GLib.timeout_add(
            self._close_transition_ms + 20,
            self._finish_close_animation,
        )
        return GLib.SOURCE_REMOVE

    def _finish_close_animation(self):
        self._close_animation_source = None
        return self._finalize_close()

    def _on_close_request(self, *_args):
        self.cancel()
        return True

    def _disconnect_tts(self):
        tts = self._tts_handler
        if tts is not None:
            for token in self._tts_tokens:
                tts.disconnect(token)
        self._tts_tokens = []
        self._tts_handler = None

    def _clear_source(self, attr):
        source_id = getattr(self, attr, None)
        if source_id is None:
            return
        try:
            GLib.source_remove(source_id)
        except Exception:
            pass
        setattr(self, attr, None)

    def _clear_session_sources(self):
        for attr in (
            "_interaction_hide_source",
            "_feedback_text_source",
            "_content_reveal_source",
            "_wakeword_release_source",
            "_capture_restart_source",
            "_tts_finish_source",
        ):
            self._clear_source(attr)
        for source_id in list(self._widget_timeouts):
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        self._widget_timeouts.clear()

    def _finalize_close(self):
        if self._destroying or self._teardown_started:
            return GLib.SOURCE_REMOVE
        self._teardown_started = True
        self._closing = True
        self.session.cancel()
        self._tts_playback_generation += 1
        try:
            self.waveform.stop_animation()
        except Exception:
            pass
        self._disconnect_tts()
        self._clear_session_sources()
        self._clear_source("_close_animation_source")
        try:
            self._clear_feedback_widgets()
        except Exception:
            pass
        if self._settings_changed_handler is not None:
            try:
                self.settings.disconnect(self._settings_changed_handler)
            except Exception:
                pass
            self._settings_changed_handler = None
        if self._surface_width_handler is not None and self._x11_surface is not None:
            try:
                self._x11_surface.disconnect(self._surface_width_handler)
            except Exception:
                pass
            self._surface_width_handler = None
        if self._surface_height_handler is not None and self._x11_surface is not None:
            try:
                self._x11_surface.disconnect(self._surface_height_handler)
            except Exception:
                pass
            self._surface_height_handler = None
        if self._monitors_changed_handler is not None:
            try:
                display = self.get_display()
                if display is not None:
                    display.get_monitors().disconnect(self._monitors_changed_handler)
            except Exception:
                pass
            self._monitors_changed_handler = None
        self._x11_surface = None
        self._x11_xid = None
        self._x11_active = False
        try:
            self._remove_css_provider()
        except Exception:
            pass
        try:
            self.set_visible(False)
        except Exception:
            pass
        if self._close_wait_source is None:
            self._close_wait_source = GLib.timeout_add(
                20, self._destroy_when_capture_released
            )
        return GLib.SOURCE_REMOVE

    def _capture_released(self) -> bool:
        # Wait for PortAudio teardown before another microphone user, such
        # as wakeword detection, can open a new stream.
        thread = self._capture_thread or self._recording_thread
        return thread is None or not thread.is_alive()

    def _destroy_when_capture_released(self):
        if not self._capture_released():
            return GLib.SOURCE_CONTINUE
        self._close_wait_source = None
        return self._destroy_window()

    def _maybe_resume_wakeword(self):
        if not self._resume_wakeword:
            return
        self._resume_wakeword = False
        if self._capture_released():
            self._resume_workspace_wakewords()
            return
        GLib.timeout_add(20, self._resume_wakeword_when_ready)

    def _resume_wakeword_when_ready(self):
        if not self._capture_released():
            return GLib.SOURCE_CONTINUE
        self._resume_workspace_wakewords()
        return GLib.SOURCE_REMOVE

    def _resume_workspace_wakewords(self):
        for window in self._wakeword_windows:
            if window in self._application.main_windows and window.controller.newelle_settings.wakeword_enabled:
                GLib.idle_add(window.start_wakeword_detection)
        self._wakeword_windows = []

    def _notify_closed(self):
        if self._closed_notified:
            return
        self._closed_notified = True
        if self._workspace_controller is not None and self._workspace_cleanup_source is None:
            self._workspace_cleanup_source = GLib.timeout_add(20, self._close_workspace_when_idle)
        callback = self.on_closed
        self.on_closed = None
        try:
            self._maybe_resume_wakeword()
            if callback is not None:
                callback(self)
        finally:
            if self._application_held:
                self._application_held = False
                self._application.release()

    def _close_workspace_when_idle(self):
        thread = self._processing_thread
        if thread is not None and thread.is_alive():
            return GLib.SOURCE_CONTINUE
        self._workspace_cleanup_source = None
        controller = self._workspace_controller
        self._workspace_controller = None
        if controller is not None:
            controller.close_application()
        return GLib.SOURCE_REMOVE

    def _on_destroy(self, *_args):
        self._closing = True
        self._destroying = True
        self.session.cancel()
        self._tts_playback_generation += 1
        try:
            self.waveform.stop_animation()
        except Exception:
            pass
        self._disconnect_tts()
        self._clear_session_sources()
        self._clear_source("_close_animation_source")
        self._clear_source("_close_wait_source")
        if self._settings_changed_handler is not None:
            try:
                self.settings.disconnect(self._settings_changed_handler)
            except Exception:
                pass
            self._settings_changed_handler = None
        try:
            self._remove_css_provider()
        except Exception:
            pass
        self._feedback_widgets = []
        self._feedback_placeholders = []
        self._notify_closed()

    def _destroy_window(self):
        self._clear_source("_close_wait_source")
        if self._destroying:
            self._notify_closed()
            return GLib.SOURCE_REMOVE
        try:
            self.destroy()
        except Exception:
            self._on_destroy()
        # GtkWidget::destroy can be delayed while Python callbacks still
        # reference the window. Release our application hold on explicit
        # close, without waiting for the wrapper to be garbage-collected.
        self._destroying = True
        self._notify_closed()
        return GLib.SOURCE_REMOVE

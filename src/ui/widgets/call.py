from gi.repository import Gtk, Adw, GLib, Gio, GObject, Gdk
import threading
import time
import os
import wave
import struct
import math
import gettext
import pyaudio
import re
import tempfile
from collections import deque

from ...utility.strings import clean_message_tts, remove_emoji, remove_markdown, remove_thinking_blocks
from ...utility.vad import VoiceActivityDetector


CALL_CSS = """
.call-container {
    background: linear-gradient(180deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
}

.call-avatar-ring {
    border-radius: 50%;
    padding: 4px;
    background: linear-gradient(135deg, #00d9ff 0%, #00ff88 50%, #00d9ff 100%);
}

.call-avatar-ring-speaking {
    animation: pulse-ring 1.5s ease-in-out infinite;
}

@keyframes pulse-ring {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0, 217, 255, 0.7); }
    50% { opacity: 0.8; box-shadow: 0 0 0 20px rgba(0, 217, 255, 0); }
}

.call-avatar {
    border-radius: 50%;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.call-status-label {
    font-size: 14px;
    color: rgba(255, 255, 255, 0.7);
    font-weight: 500;
}

.call-name-label {
    font-size: 28px;
    color: #ffffff;
    font-weight: 700;
    letter-spacing: 0.5px;
}

.call-timer-label {
    font-size: 16px;
    color: rgba(255, 255, 255, 0.6);
    font-family: monospace;
    font-weight: 500;
}

.call-transcript-box {
    background: rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 16px;
    border: 1px solid rgba(255, 255, 255, 0.1);
}

.call-transcript-label {
    color: rgba(255, 255, 255, 0.9);
    font-size: 15px;
    line-height: 1.5;
}

.call-button-end {
    background: linear-gradient(135deg, #ff416c 0%, #ff4b2b 100%);
    border-radius: 50%;
    min-width: 72px;
    min-height: 72px;
    box-shadow: 0 4px 20px rgba(255, 65, 108, 0.4);
}

.call-button-end:hover {
    background: linear-gradient(135deg, #ff5c7c 0%, #ff6b4b 100%);
    box-shadow: 0 6px 25px rgba(255, 65, 108, 0.5);
}

.call-button-start {
    background: linear-gradient(135deg, #00d9ff 0%, #00ff88 100%);
    border-radius: 50%;
    min-width: 72px;
    min-height: 72px;
    box-shadow: 0 4px 20px rgba(0, 217, 255, 0.4);
}

.call-button-start:hover {
    background: linear-gradient(135deg, #00e9ff 0%, #10ff98 100%);
    box-shadow: 0 6px 25px rgba(0, 217, 255, 0.5);
}

.call-button-mute {
    background: rgba(255, 255, 255, 0.15);
    border-radius: 50%;
    min-width: 56px;
    min-height: 56px;
    border: 1px solid rgba(255, 255, 255, 0.2);
}

.call-button-mute:hover {
    background: rgba(255, 255, 255, 0.25);
}

.call-button-mute-active {
    background: rgba(255, 75, 75, 0.3);
    border: 1px solid rgba(255, 75, 75, 0.5);
}

.call-waveform {
    min-height: 40px;
}

.call-wave-bar {
    background: linear-gradient(180deg, #00d9ff 0%, #00ff88 100%);
    border-radius: 2px;
    min-width: 4px;
}

.call-listening-indicator {
    color: #00ff88;
    font-size: 13px;
    font-weight: 600;
}

.call-speaking-indicator {
    color: #00d9ff;
    font-size: 13px;
    font-weight: 600;
}

.call-button-convert {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-width: 72px;
    min-height: 72px;
    border-radius: 50%;
    box-shadow: 0 4px 20px rgba(102, 126, 234, 0.4);
}

.call-button-convert:hover {
    background: linear-gradient(135deg, #7688eb 0%, #865cb3 100%);
    box-shadow: 0 6px 25px rgba(102, 126, 234, 0.5);
}

.call-history-panel {
    background: rgba(0, 0, 0, 0.85);
    border-radius: 16px;
    margin: 16px;
    padding: 16px;
    min-width: 300px;
    max-width: 400px;
}

.call-history-scroll {
    background: transparent;
}

.call-history-box {
    spacing: 12px;
}

.call-message-user {
    background: rgba(0, 217, 255, 0.2);
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 0;
    border-left: 3px solid #00d9ff;
}

.call-message-assistant {
    background: rgba(102, 126, 234, 0.2);
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 0;
    border-left: 3px solid #667eea;
}

.call-message-label {
    color: rgba(255, 255, 255, 0.95);
    font-size: 14px;
    line-height: 1.4;
    wrap: true;
}

.call-message-sender {
    color: rgba(255, 255, 255, 0.6);
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 4px;
}

.call-button-history {
    background: rgba(255, 255, 255, 0.15);
    border-radius: 50%;
    min-width: 56px;
    min-height: 56px;
    border: 1px solid rgba(255, 255, 255, 0.2);
}

.call-button-history:hover {
    background: rgba(255, 255, 255, 0.25);
}

.call-button-history-active {
    background: rgba(102, 126, 234, 0.4);
    border: 1px solid rgba(102, 126, 234, 0.6);
}
"""


class CallPanel(Gtk.Box):
    """Modern call screen widget for live voice conversation with AI"""
    
    __gsignals__ = {
        'call-ended': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'transcript-updated': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'convert-to-chat': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    
    def __init__(self, controller, profile_name=None, profile_picture=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chat_id = None
        
        self.controller = controller
        self.profile_name = profile_name or "AI Assistant"
        self.profile_picture = profile_picture
        self.tab = None
        
        # Call state
        self.call_active = False
        self.is_muted = False
        self.call_start_time = None
        self.current_transcript = ""
        self.assistant_speaking = False
        self.user_speaking = False
        self.history_visible = False
        self.listen_during_tts = True

        # Conversation turn state
        self._call_generation = 0
        self._endpoint_debounce_seconds = 0.5
        self._turn_lock = threading.Lock()
        self._turn_in_progress = False
        self._active_turn_generation = None
        self._pending_barge_in = None
        self._barge_in_capture = False

        # Get username
        self.username = self.controller.newelle_settings.username
        
        # Audio settings
        self.sample_rate = 16000
        self.chunk_size = 512
        self.channels = 1
        self.audio_format = pyaudio.paInt16
        
        # VAD
        self.vad = VoiceActivityDetector(self.sample_rate)
        
        # Audio capture lifecycle.  The capture worker owns the native
        # PyAudio objects; the UI only requests cancellation and waits for
        # this event before starting another capture.
        self._capture_stopped = threading.Event()
        self._capture_stopped.set()
        self._capture_start_pending = False
        self._capture_start_source_id = None
        
        # Prebuffer for 1 second before speech starts
        self.prebuffer_chunks = int(self.sample_rate / self.chunk_size) + 1
        
        # Threads
        self.recording_thread = None
        self.timer_thread = None
        self.processing_thread = None

        # Chat history storage
        self.chat_history_messages = []
        
        # Waveform visualization
        self.wave_bars = []
        self.wave_levels = [0] * 12
        
        # Setup UI
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class("call-container")
        
        # Apply CSS
        self._apply_css()
        self._build_ui()
    
    def _apply_css(self):
        """Apply custom CSS styles"""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CALL_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    
    def _build_ui(self):
        """Build the call screen UI"""
        # Main overlay container
        self.overlay = Gtk.Overlay(
            hexpand=True,
            vexpand=True
        )

        # Background container
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            vexpand=True,
            hexpand=True,
            spacing=24
        )

        # Top spacer
        main_box.append(Gtk.Box(vexpand=True))

        # Avatar section
        avatar_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            spacing=16
        )

        # Avatar using Adw.Avatar wrapped in ring container
        self.avatar_ring = Gtk.Box(
            css_classes=["call-avatar-ring"],
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER
        )
        self.avatar_ring.set_size_request(128, 128)

        if self.profile_picture and os.path.exists(self.profile_picture):
            try:
                self.avatar = Adw.Avatar(
                    custom_image=Gdk.Texture.new_from_filename(self.profile_picture),
                    text=self.profile_name,
                    show_initials=True,
                    size=120
                )
            except Exception:
                self.avatar = Adw.Avatar(
                    text=self.profile_name,
                    show_initials=True,
                    size=120
                )
        else:
            self.avatar = Adw.Avatar(
                text=self.profile_name,
                show_initials=True,
                size=120
            )

        self.avatar_ring.append(self.avatar)
        avatar_container.append(self.avatar_ring)

        # Name label
        self.name_label = Gtk.Label(
            label=self.profile_name,
            css_classes=["call-name-label"]
        )
        avatar_container.append(self.name_label)

        # Status label
        self.status_label = Gtk.Label(
            label=_("Ready to call"),
            css_classes=["call-status-label"]
        )
        avatar_container.append(self.status_label)

        # Timer
        self.timer_label = Gtk.Label(
            label="00:00",
            css_classes=["call-timer-label"],
            visible=False
        )
        avatar_container.append(self.timer_label)

        main_box.append(avatar_container)

        # Waveform visualization
        self.waveform_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            spacing=4,
            css_classes=["call-waveform"],
            visible=False
        )
        for i in range(12):
            bar = Gtk.Box(css_classes=["call-wave-bar"])
            bar.set_size_request(4, 8)
            self.wave_bars.append(bar)
            self.waveform_box.append(bar)
        main_box.append(self.waveform_box)

        # Listening/Speaking indicator
        self.activity_indicator = Gtk.Label(
            label="",
            css_classes=["call-listening-indicator"],
            visible=False
        )
        main_box.append(self.activity_indicator)

        # Bottom spacer
        main_box.append(Gtk.Box(vexpand=True))

        self.overlay.set_child(main_box)

        # Right side: Chat history panel (initially hidden)
        self._build_history_panel()

        # Bottom: Controls overlay
        self._build_controls_overlay()

        self.append(self.overlay)

    def _build_history_panel(self):
        """Build toggleable chat history panel"""
        self.history_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.END,
            valign=Gtk.Align.FILL,
            margin_end=16,
            margin_top=80,
            margin_bottom=200,
            css_classes=["call-history-panel"],
            visible=False,
            width_request=320
        )

        # Header
        history_header = Gtk.Label(
            label=_("Chat History"),
            css_classes=["call-status-label"],
            margin_bottom=8
        )
        self.history_panel.append(history_header)

        # Scrollable message list
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            css_classes=["call-history-scroll"],
            vexpand=True
        )

        self.history_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=["call-history-box"],
            spacing=8
        )
        scroll.set_child(self.history_box)
        self.history_panel.append(scroll)

        self.overlay.add_overlay(self.history_panel)

    def _build_controls_overlay(self):
        """Build call controls overlay at bottom"""
        controls_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            margin_bottom=32,
            homogeneous=False
        )

        controls_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            homogeneous=True,
            spacing=32,
            margin_bottom=48
        )

        # Mute button
        self.mute_button = Gtk.Button(
            css_classes=["call-button-mute"],
            tooltip_text=_("Mute microphone")
        )
        mute_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        mute_icon.set_pixel_size(24)
        self.mute_button.set_child(mute_icon)
        self.mute_button.connect("clicked", self._on_mute_clicked)
        self.mute_button.set_sensitive(False)
        controls_box.append(self.mute_button)

        # Start/End call button
        self.call_button = Gtk.Button(
            css_classes=["call-button-start"]
        )
        call_icon = Gtk.Image.new_from_icon_name("call-start-symbolic")
        call_icon.set_pixel_size(32)
        self.call_button_icon = call_icon
        self.call_button.set_child(call_icon)
        self.call_button.connect("clicked", self._on_call_button_clicked)
        controls_box.append(self.call_button)

        # Speaker button (to mute TTS)
        self.speaker_button = Gtk.Button(
            css_classes=["call-button-mute"],
            tooltip_text=_("Mute speaker")
        )
        speaker_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        speaker_icon.set_pixel_size(24)
        self.speaker_button.set_child(speaker_icon)
        self.speaker_button.connect("clicked", self._on_speaker_clicked)
        self.speaker_button.set_sensitive(False)
        controls_box.append(self.speaker_button)

        controls_container.append(controls_box)

        # Convert to chat button (shown after call ends)
        self.convert_button = Gtk.Button(
            css_classes=["call-button-convert"],
            label=_("Convert to Chat"),
            visible=False,
            halign=Gtk.Align.CENTER
        )
        convert_icon = Gtk.Image.new_from_icon_name("chat-bubbles-text-symbolic")
        convert_icon.set_pixel_size(20)
        self.convert_button.set_child(convert_icon)
        self.convert_button.connect("clicked", self._on_convert_to_chat_clicked)
        self.convert_button.set_tooltip_text(_("Convert to chat"))
        controls_container.append(self.convert_button)

        self.overlay.add_overlay(controls_container)

        # Right side controls for history and listen toggle
        right_controls = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            spacing=16,
            margin_end=16,
            margin_top=16
        )

        # History toggle button
        self.history_button = Gtk.Button(
            css_classes=["call-button-history"],
            tooltip_text=_("Show/Hide chat history")
        )
        history_icon = Gtk.Image.new_from_icon_name("chat-bubbles-text-symbolic")
        history_icon.set_pixel_size(24)
        self.history_button.set_child(history_icon)
        self.history_button.connect("clicked", self._on_history_clicked)
        right_controls.append(self.history_button)

        # Auto-listen toggle button
        self.listen_toggle_button = Gtk.ToggleButton(
            css_classes=["call-button-history"],
            tooltip_text=_("Auto-listen while agent speaks")
        )
        listen_icon = Gtk.Image.new_from_icon_name("call-emergency-symbolic")
        listen_icon.set_pixel_size(24)
        self.listen_toggle_button.set_child(listen_icon)
        self.listen_toggle_button.set_active(True)
        self.listen_toggle_button.connect("toggled", self._on_listen_toggle)
        right_controls.append(self.listen_toggle_button)

        self.overlay.add_overlay(right_controls)
    
    def set_tab(self, tab):
        """Set the tab reference"""
        self.tab = tab
        if tab:
            tab.set_title(_("Call"))
            tab.set_icon(Gio.ThemedIcon(name="call-start-symbolic"))
    
    def _on_call_button_clicked(self, button):
        """Handle call button click"""
        if self.call_active:
            self.end_call()
        else:
            self.start_call()
    
    def _on_mute_clicked(self, button):
        """Handle mute button click"""
        with self._turn_lock:
            self.is_muted = not self.is_muted
            if self.is_muted:
                # Drop any capture queued before the mute request.  The
                # recording worker will reset its VAD state on its next
                # chunk, while keeping the native stream open for unmute.
                self._pending_barge_in = None
                self._barge_in_capture = False
        if self.is_muted:
            button.add_css_class("call-button-mute-active")
            button.get_child().set_from_icon_name("microphone-disabled-symbolic")
            self.activity_indicator.set_label(_("Muted"))
        else:
            button.remove_css_class("call-button-mute-active")
            button.get_child().set_from_icon_name("audio-input-microphone-symbolic")
            self._update_activity_indicator()
    
    def _on_speaker_clicked(self, button):
        """Handle speaker button click"""
        # Stop TTS playback
        if hasattr(self.controller, 'handlers') and self.controller.handlers.tts:
            self.controller.handlers.tts.stop()

    def _on_history_clicked(self, button):
        """Handle history toggle button click"""
        self.history_visible = not self.history_visible
        self.history_panel.set_visible(self.history_visible)

        if self.history_visible:
            button.add_css_class("call-button-history-active")
        else:
            button.remove_css_class("call-button-history-active")
    
    def _on_listen_toggle(self, button):
        """Handle listen during TTS toggle"""
        self.listen_during_tts = button.get_active()
        if self.listen_during_tts:
            button.remove_css_class("call-button-history-active")
        else:
            button.add_css_class("call-button-history-active")
    
    def _on_convert_to_chat_clicked(self, button):
        """Handle convert to chat button click"""
        if self.chat_id is not None:
            self.emit('convert-to-chat')
    
    def start_call(self):
        """Start the voice call"""
        if self.call_active:
            return

        # A previous capture may still be unwinding its PortAudio/PulseAudio
        # resources after the user stopped the call.  Never overlap a new
        # capture with that teardown.
        if not self._capture_stopped.is_set():
            if not self._capture_start_pending:
                self._capture_start_pending = True
                self._capture_start_source_id = GLib.timeout_add(
                    20, self._start_call_after_capture_stopped
                )
            return

        self._call_generation += 1
        call_generation = self._call_generation
        self.call_active = True
        self.call_start_time = time.time()
        self.current_transcript = ""
        self.chat_history_messages = []
        self.vad.reset()
        self.assistant_speaking = False
        self.user_speaking = False

        with self._turn_lock:
            self._pending_barge_in = None
            self._barge_in_capture = False
            if not self._turn_in_progress:
                self._active_turn_generation = None
                self.processing_thread = None

        # Clear history box
        while self.history_box.get_first_child():
            self.history_box.remove(self.history_box.get_first_child())

        # Update UI
        self.call_button_icon.set_from_icon_name("call-stop-symbolic")
        self.call_button.remove_css_class("call-button-start")
        self.call_button.add_css_class("call-button-end")
        self.call_button.remove_css_class("suggested-action")
        self.status_label.set_label(_("Connected"))
        self.timer_label.set_visible(True)
        self.waveform_box.set_visible(True)
        self.activity_indicator.set_visible(True)
        self.activity_indicator.set_label(_("Listening..."))
        self.mute_button.set_sensitive(True)
        self.speaker_button.set_sensitive(True)
        self.convert_button.set_visible(False)

        if self.tab:
            self.tab.set_title(_("Call - Active"))

        # Start threads
        self.recording_thread = threading.Thread(
            target=self._recording_loop,
            args=(call_generation,),
            daemon=True,
        )
        self._capture_stopped.clear()
        self.recording_thread.start()

        self.timer_thread = threading.Thread(
            target=self._timer_loop,
            args=(call_generation,),
            daemon=True,
        )
        self.timer_thread.start()

    def _start_call_after_capture_stopped(self):
        """Start a deferred call once the prior capture owns no resources."""
        if self.call_active:
            self._capture_start_pending = False
            self._capture_start_source_id = None
            return GLib.SOURCE_REMOVE
        if not self._capture_stopped.is_set():
            return GLib.SOURCE_CONTINUE

        self._capture_start_pending = False
        self._capture_start_source_id = None
        self.start_call()
        return GLib.SOURCE_REMOVE
    
    def end_call(self):
        """End the voice call"""
        self.call_active = False
        self._call_generation += 1
        ended_generation = self._call_generation
        self.assistant_speaking = False
        self.user_speaking = False

        with self._turn_lock:
            self._pending_barge_in = None
            self._barge_in_capture = False
        
        # Stop TTS
        if hasattr(self.controller, 'handlers') and self.controller.handlers.tts:
            self.controller.handlers.tts.stop()
        
        # Update UI
        GLib.idle_add(self._update_ui_after_end, ended_generation)
        
        self.emit('call-ended')
    
    def _update_ui_after_end(self, ended_generation):
        """Update UI after call ends"""
        if self.call_active or ended_generation != self._call_generation:
            return

        self.call_button_icon.set_from_icon_name("call-start-symbolic")
        self.call_button.remove_css_class("call-button-end")
        self.call_button.add_css_class("call-button-start")
        self.status_label.set_label(_("Call ended"))
        self.timer_label.set_visible(False)
        self.waveform_box.set_visible(False)
        self.activity_indicator.set_visible(False)
        self.mute_button.set_sensitive(False)
        self.speaker_button.set_sensitive(False)
        self.avatar_ring.remove_css_class("call-avatar-ring-speaking")
        self.convert_button.set_visible(True)
        
        if self.tab:
            self.tab.set_title(_("Call"))
        
        # Reset waveform
        for bar in self.wave_bars:
            bar.set_size_request(4, 8)
    
    def _is_current_call(self, call_generation):
        """Return whether work belongs to the currently active call session."""
        return self.call_active and call_generation == self._call_generation

    def _end_call_if_current(self, call_generation):
        """End a call only when the reporting worker still belongs to it."""
        if self._is_current_call(call_generation):
            self.end_call()

    def _set_timer_label(self, call_generation, label):
        """Update the timer only if its call session is still active."""
        if self._is_current_call(call_generation):
            self.timer_label.set_label(label)

    def _timer_loop(self, call_generation):
        """Update call timer"""
        while self._is_current_call(call_generation):
            if self.call_start_time:
                elapsed = int(time.time() - self.call_start_time)
                minutes = elapsed // 60
                seconds = elapsed % 60
                GLib.idle_add(
                    self._set_timer_label,
                    call_generation,
                    f"{minutes:02d}:{seconds:02d}"
                )
            time.sleep(1)
    
    def _recording_loop(self, call_generation):
        """Main recording loop with VAD"""
        audio_stream = None
        pyaudio_instance = None
        audio_prebuffer = deque(maxlen=self.prebuffer_chunks)
        speech_buffer = []
        finalize_deadline = None
        capture_is_barge_in = False
        microphone_was_suppressed = False

        def reset_capture():
            nonlocal speech_buffer, finalize_deadline, capture_is_barge_in
            speech_buffer = []
            finalize_deadline = None
            capture_is_barge_in = False
            audio_prebuffer.clear()
            self.vad.reset()
            with self._turn_lock:
                self._barge_in_capture = False

        try:
            pyaudio_instance = pyaudio.PyAudio()
            audio_stream = pyaudio_instance.open(
                format=self.audio_format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            if not self._is_current_call(call_generation):
                return

            consecutive_errors = 0
            max_consecutive_errors = 10

            while self._is_current_call(call_generation):
                try:
                    audio_data = audio_stream.read(self.chunk_size, exception_on_overflow=False)
                    consecutive_errors = 0  # Reset error counter on successful read
                except OSError as e:
                    if not self._is_current_call(call_generation):
                        break
                    consecutive_errors += 1
                    print(f"Audio stream error ({consecutive_errors}/{max_consecutive_errors}): {e}")

                    if consecutive_errors >= max_consecutive_errors:
                        print("Too many consecutive audio errors, stopping call")
                        GLib.idle_add(self._end_call_if_current, call_generation)
                        break

                    # Try to recover by continuing
                    time.sleep(0.1)
                    continue
                except Exception as e:
                    print(f"Unexpected audio error: {e}")
                    time.sleep(0.1)
                    continue

                if not self._is_current_call(call_generation):
                    break

                with self._turn_lock:
                    turn_in_progress = self._turn_in_progress
                    barge_in_capture = self._barge_in_capture
                    is_muted = self.is_muted

                can_barge_in = (
                    turn_in_progress
                    and self.listen_during_tts
                    and (self.assistant_speaking or barge_in_capture)
                )
                microphone_suppressed = is_muted or (
                    turn_in_progress and not can_barge_in
                )

                if microphone_suppressed:
                    if not microphone_was_suppressed:
                        reset_capture()
                        microphone_was_suppressed = True
                    continue

                if microphone_was_suppressed:
                    reset_capture()
                    microphone_was_suppressed = False

                # Update waveform visualization
                self._update_waveform(audio_data)

                # Add to prebuffer (circular buffer for pre-speech audio)
                audio_prebuffer.append(audio_data)

                # Process VAD
                is_speech, speech_started, speech_ended = self.vad.process_chunk(audio_data)

                started_new_capture = False
                if speech_started:
                    continuing_utterance = finalize_deadline is not None
                    if continuing_utterance:
                        finalize_deadline = None
                    else:
                        speech_buffer = list(audio_prebuffer)
                        started_new_capture = True
                        capture_is_barge_in = (
                            turn_in_progress
                            and self.listen_during_tts
                            and self.assistant_speaking
                        )
                        if capture_is_barge_in:
                            with self._turn_lock:
                                self._barge_in_capture = True

                    GLib.idle_add(self._on_speech_started, call_generation)

                if (is_speech or self.vad.is_speaking or finalize_deadline is not None) and not started_new_capture:
                    speech_buffer.append(audio_data)

                if speech_ended:
                    GLib.idle_add(self._on_speech_ended, call_generation)
                    finalize_deadline = time.monotonic() + self._endpoint_debounce_seconds

                if (
                    finalize_deadline is not None
                    and not is_speech
                    and time.monotonic() >= finalize_deadline
                ):
                    if speech_buffer:
                        self._process_speech(
                            b''.join(speech_buffer),
                            call_generation,
                            capture_is_barge_in,
                        )
                    reset_capture()

        except Exception as e:
            import traceback
            print(f"Recording loop error: {e}")
            print(traceback.format_exc())
            GLib.idle_add(self._end_call_if_current, call_generation)
        finally:
            # The capture worker is the sole owner of these native objects.
            # In particular, do not move this teardown to end_call(): that
            # method runs on GTK's thread while read() may still be active.
            if audio_stream:
                try:
                    audio_stream.stop_stream()
                except Exception:
                    pass
                try:
                    audio_stream.close()
                except Exception:
                    pass

            if pyaudio_instance:
                try:
                    pyaudio_instance.terminate()
                except Exception:
                    pass

            if self.recording_thread is threading.current_thread():
                self.recording_thread = None
            self._capture_stopped.set()
    
    def _update_waveform(self, audio_data):
        """Update waveform visualization"""
        # Calculate energy for visualization
        count = len(audio_data) // 2
        if count == 0:
            return
        
        try:
            shorts = struct.unpack("<" + str(count) + "h", audio_data)
            # Split into segments for bars
            segment_size = max(1, len(shorts) // 12)
            
            new_levels = []
            for i in range(12):
                start = i * segment_size
                end = min(start + segment_size, len(shorts))
                segment = shorts[start:end]
                if segment:
                    rms = math.sqrt(sum(s * s for s in segment) / len(segment))
                    # Normalize and scale
                    level = min(1.0, rms / 10000)
                    new_levels.append(level)
                else:
                    new_levels.append(0)
            
            self.wave_levels = new_levels
            GLib.idle_add(self._update_wave_bars)
        except Exception:
            pass
    
    def _update_wave_bars(self):
        """Update wave bar heights"""
        for i, bar in enumerate(self.wave_bars):
            if i < len(self.wave_levels):
                height = max(8, int(self.wave_levels[i] * 40))
                bar.set_size_request(4, height)
    
    def _on_speech_started(self, call_generation):
        """Called when speech is detected"""
        if not self._is_current_call(call_generation):
            return

        self.user_speaking = True
        self._update_activity_indicator()
        self.avatar_ring.add_css_class("call-avatar-ring-speaking")
        
        # Interrupt TTS if playing
        if self.assistant_speaking:
            if hasattr(self.controller, 'handlers') and self.controller.handlers.tts:
                self.controller.handlers.tts.stop()
            self.assistant_speaking = False
    
    def _on_speech_ended(self, call_generation):
        """Called when speech ends"""
        if not self._is_current_call(call_generation):
            return

        self.user_speaking = False
        self._update_activity_indicator()
        self.avatar_ring.remove_css_class("call-avatar-ring-speaking")
    
    def _update_activity_indicator(self):
        """Update the activity indicator label"""
        if self.is_muted:
            self.activity_indicator.set_label(_("Muted"))
            self.activity_indicator.remove_css_class("call-speaking-indicator")
            self.activity_indicator.add_css_class("call-listening-indicator")
        elif self.assistant_speaking:
            self.activity_indicator.set_label(self.profile_name + _(" speaking..."))
            self.activity_indicator.remove_css_class("call-listening-indicator")
            self.activity_indicator.add_css_class("call-speaking-indicator")
        elif self.user_speaking:
            self.activity_indicator.set_label(_("Listening..."))
            self.activity_indicator.remove_css_class("call-speaking-indicator")
            self.activity_indicator.add_css_class("call-listening-indicator")
        else:
            self.activity_indicator.set_label(_("Listening..."))
            self.activity_indicator.remove_css_class("call-speaking-indicator")
            self.activity_indicator.add_css_class("call-listening-indicator")
    
    def _process_speech(self, audio_data, call_generation, is_barge_in=False):
        """Start or queue one serialized STT/LLM/TTS turn."""
        if not audio_data or not self._is_current_call(call_generation):
            return

        turn_thread = threading.Thread(
            target=self._run_turn,
            args=(bytes(audio_data), call_generation),
            daemon=True,
        )

        with self._turn_lock:
            if self._turn_in_progress:
                if (
                    is_barge_in
                    and self._active_turn_generation == call_generation
                    and self._pending_barge_in is None
                ):
                    self._pending_barge_in = (bytes(audio_data), call_generation)
                return

            self._turn_in_progress = True
            self._active_turn_generation = call_generation
            self.processing_thread = turn_thread

        turn_thread.start()

    def _run_turn(self, audio_data, call_generation):
        """Write one immutable capture, process it, and release the turn."""
        temp_path = None
        try:
            if not self._is_current_call(call_generation):
                return

            with tempfile.NamedTemporaryFile(
                dir=self.controller.cache_dir,
                prefix="call_recording_",
                suffix=".wav",
                delete=False,
            ) as temporary_file:
                temp_path = temporary_file.name

            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data)

            self._recognize_and_respond(temp_path, call_generation)
        except Exception as e:
            import traceback
            print(f"Error processing call audio: {e}")
            print(traceback.format_exc())
            GLib.idle_add(
                self._add_message_to_history_if_current,
                call_generation,
                "System",
                _("Recognition error. Please try again."),
                True,
            )
        finally:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    print(f"Could not remove temporary call recording: {e}")
            self._finish_turn(call_generation)

    def _finish_turn(self, call_generation):
        """Release the active turn or start its single pending barge-in."""
        pending_barge_in = None

        with self._turn_lock:
            if self._active_turn_generation != call_generation:
                return

            if (
                self._is_current_call(call_generation)
                and self._pending_barge_in is not None
                and self._pending_barge_in[1] == call_generation
            ):
                pending_barge_in = self._pending_barge_in
                self._pending_barge_in = None
            else:
                self._pending_barge_in = None
                self._turn_in_progress = False
                self._active_turn_generation = None
                self.processing_thread = None

        if pending_barge_in is None:
            return

        audio_data, pending_generation = pending_barge_in
        turn_thread = threading.Thread(
            target=self._run_turn,
            args=(audio_data, pending_generation),
            daemon=True,
        )
        with self._turn_lock:
            if self._active_turn_generation != pending_generation:
                return
            self.processing_thread = turn_thread
        turn_thread.start()
    
    def _recognize_and_respond(self, audio_path, call_generation):
        """Recognize speech and get AI response"""
        try:
            if not self._is_current_call(call_generation):
                return

            # Get STT handler
            stt = self.controller.handlers.stt
            if not stt or not stt.is_installed():
                GLib.idle_add(
                    self._add_message_to_history_if_current,
                    call_generation,
                    "System",
                    _("Speech recognition not available"),
                    True
                )
                return

            # Recognize
            text = stt.recognize_file(audio_path)
            if not self._is_current_call(call_generation) or not text or text.strip() == "":
                return

            # Add user message to history
            GLib.idle_add(
                self._add_message_to_history_if_current,
                call_generation,
                self.username,
                text,
                False,
            )

            # Get LLM response
            self._get_ai_response(text, call_generation)

        except Exception as e:
            import traceback
            print(f"Recognition error: {e}")
            print(traceback.format_exc())
            GLib.idle_add(
                self._add_message_to_history_if_current,
                call_generation,
                "System",
                _("Recognition error. Please try again."),
                True
            )
    
    def _get_ai_response(self, user_message, call_generation):
        """Get AI response and play TTS using run_llm_with_tools"""
        try:
            if not self._is_current_call(call_generation):
                return

            if self.chat_id is None:
                self.chat_id = self.controller.create_call_chat()
            streaming_text = ""
            def on_message_callback(text):
                nonlocal streaming_text
                streaming_text += text

            def on_tool_result_callback(tool_name, result):
                tool_output = result.get_output() if result else "Tool executed"
                GLib.idle_add(
                    self._add_message_to_history_if_current,
                    call_generation,
                    "Tool",
                    f"[{tool_name}] {tool_output[:300]}",
                    False
                )

            self.controller.is_call_request = True
            try:
                response = self.controller.run_llm_with_tools(
                    message=user_message,
                    chat_id=self.chat_id,
                    on_message_callback=on_message_callback,
                    on_tool_result_callback=on_tool_result_callback,
                    save_chat=True,
                    force_tools_on_main_thread=True,
                )
            finally:
                self.controller.is_call_request = False

            if response and self._is_current_call(call_generation):
                GLib.idle_add(
                    self._add_message_to_history_if_current,
                    call_generation,
                    self.profile_name,
                    response,
                    False,
                )
                response = clean_message_tts(response)
                self._play_tts(response, call_generation)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            print(f"LLM error: {e}")
            # Ensure flag is reset
            self.controller.is_call_request = False
            GLib.idle_add(
                self._add_message_to_history_if_current,
                call_generation,
                "System",
                _("Error getting response. Please try again."),
                True
            )
    
    def _clean_response(self, response):
        """Clean response for TTS"""
        response = remove_thinking_blocks(response)
        response = remove_markdown(response)
        response = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', response)
        # Remove emoji 
        response = remove_emoji(response) 
        return response.strip()
    
    def _play_tts(self, text, call_generation):
        """Play TTS for the response"""
        if not text or not self._is_current_call(call_generation):
            return

        tts = self.controller.handlers.tts
        if not tts:
            return

        def on_tts_start():
            GLib.idle_add(
                self._set_assistant_speaking,
                call_generation,
                True,
            )

        def on_tts_stop():
            GLib.idle_add(
                self._set_assistant_speaking,
                call_generation,
                False,
            )

        tts.connect("start", on_tts_start)
        tts.connect("stop", on_tts_stop)

        try:
            tts.play(text)
        except Exception as e:
            import traceback
            print(f"TTS error: {e}")
            print(traceback.format_exc())
            GLib.idle_add(
                self._set_assistant_speaking,
                call_generation,
                False,
            )
    
    def _set_assistant_speaking(self, call_generation, speaking):
        """Update assistant speaking state"""
        if not self._is_current_call(call_generation):
            return

        self.assistant_speaking = speaking
        self._update_activity_indicator()
        if speaking:
            self.avatar_ring.add_css_class("call-avatar-ring-speaking")
        else:
            self.avatar_ring.remove_css_class("call-avatar-ring-speaking")

    def _add_message_to_history_if_current(
        self,
        call_generation,
        sender,
        text,
        is_error=False,
    ):
        """Add a transcript entry only for the currently active call."""
        if self._is_current_call(call_generation):
            self._add_message_to_history(sender, text, is_error)
    
    def _add_message_to_history(self, sender, text, is_error=False):
        """Add a message to the chat history panel"""
        # Create message box
        is_user = sender == self.username
        message_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=["call-message-user" if is_user else "call-message-assistant"]
        )

        # Sender label
        sender_label = Gtk.Label(
            label=sender,
            css_classes=["call-message-sender"],
            xalign=0
        )
        message_box.append(sender_label)

        # Message text
        message_label = Gtk.Label(
            label=text,
            css_classes=["call-message-label"],
            wrap=True,
            xalign=0,
            selectable=True
        )
        message_box.append(message_label)

        self.history_box.append(message_box)

        # Scroll to bottom
        if self.history_visible:
            GLib.idle_add(self._scroll_history_to_bottom)

        # Store in history
        self.chat_history_messages.append({
            "sender": sender,
            "text": text,
            "is_error": is_error
        })

        self.emit('transcript-updated', f"{sender}: {text}")

    def _scroll_history_to_bottom(self):
        """Scroll history panel to bottom"""
        # Find the scrolled window parent
        parent = self.history_box.get_parent()
        if parent and isinstance(parent, Gtk.ScrolledWindow):
            adj = parent.get_vadjustment()
            if adj:
                adj.set_value(adj.get_upper())

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

        # Get username
        self.username = self.controller.newelle_settings.username
        
        # Audio settings
        self.sample_rate = 16000
        self.chunk_size = 512
        self.channels = 1
        self.audio_format = pyaudio.paInt16
        
        # VAD
        self.vad = VoiceActivityDetector(self.sample_rate)
        
        # Audio buffers
        self.speech_buffer = []
        self.audio_stream = None
        self.pyaudio_instance = None
        
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

        # History toggle button
        self.history_button = Gtk.Button(
            css_classes=["call-button-history"],
            tooltip_text=_("Show/Hide chat history")
        )
        history_icon = Gtk.Image.new_from_icon_name("chat-bubbles-text-symbolic")
        history_icon.set_pixel_size(24)
        self.history_button.set_child(history_icon)
        self.history_button.connect("clicked", self._on_history_clicked)
        controls_box.append(self.history_button)

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
        self.is_muted = not self.is_muted
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
    
    def _on_convert_to_chat_clicked(self, button):
        """Handle convert to chat button click"""
        if self.chat_id is not None:
            self.emit('convert-to-chat')
    
    def start_call(self):
        """Start the voice call"""
        self.call_active = True
        self.call_start_time = time.time()
        self.current_transcript = ""
        self.speech_buffer = []
        self.chat_history_messages = []
        self.vad.reset()

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
        self.recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self.recording_thread.start()

        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()
    
    def end_call(self):
        """End the voice call"""
        self.call_active = False
        
        # Stop audio
        if self.audio_stream:
            try:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            except Exception:
                pass
            self.audio_stream = None
        
        if self.pyaudio_instance:
            try:
                self.pyaudio_instance.terminate()
            except Exception:
                pass
            self.pyaudio_instance = None
        
        # Stop TTS
        if hasattr(self.controller, 'handlers') and self.controller.handlers.tts:
            self.controller.handlers.tts.stop()
        
        # Update UI
        GLib.idle_add(self._update_ui_after_end)
        
        self.emit('call-ended')
    
    def _update_ui_after_end(self):
        """Update UI after call ends"""
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
    
    def _timer_loop(self):
        """Update call timer"""
        while self.call_active:
            if self.call_start_time:
                elapsed = int(time.time() - self.call_start_time)
                minutes = elapsed // 60
                seconds = elapsed % 60
                GLib.idle_add(
                    self.timer_label.set_label,
                    f"{minutes:02d}:{seconds:02d}"
                )
            time.sleep(1)
    
    def _recording_loop(self):
        """Main recording loop with VAD"""
        try:
            self.pyaudio_instance = pyaudio.PyAudio()
            self.audio_stream = self.pyaudio_instance.open(
                format=self.audio_format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )

            consecutive_errors = 0
            max_consecutive_errors = 10

            while self.call_active:
                if self.is_muted:
                    time.sleep(0.03)
                    consecutive_errors = 0
                    continue

                try:
                    audio_data = self.audio_stream.read(self.chunk_size, exception_on_overflow=False)
                    consecutive_errors = 0  # Reset error counter on successful read
                except OSError as e:
                    consecutive_errors += 1
                    print(f"Audio stream error ({consecutive_errors}/{max_consecutive_errors}): {e}")

                    if consecutive_errors >= max_consecutive_errors:
                        print("Too many consecutive audio errors, stopping call")
                        GLib.idle_add(self.end_call)
                        break

                    # Try to recover by continuing
                    time.sleep(0.1)
                    continue
                except Exception as e:
                    print(f"Unexpected audio error: {e}")
                    time.sleep(0.1)
                    continue

                # Update waveform visualization
                self._update_waveform(audio_data)

                # Process VAD
                is_speech, speech_started, speech_ended = self.vad.process_chunk(audio_data)

                if speech_started:
                    GLib.idle_add(self._on_speech_started)

                if is_speech or self.vad.is_speaking:
                    self.speech_buffer.append(audio_data)

                if speech_ended:
                    GLib.idle_add(self._on_speech_ended)
                    # Process the speech buffer
                    if len(self.speech_buffer) > 0:
                        self._process_speech()
                    self.speech_buffer = []

        except Exception as e:
            import traceback
            print(f"Recording loop error: {e}")
            print(traceback.format_exc())
            GLib.idle_add(self.end_call)
        finally:
            # Ensure stream is closed
            if self.audio_stream:
                try:
                    self.audio_stream.stop_stream()
                    self.audio_stream.close()
                except Exception:
                    pass
                self.audio_stream = None
    
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
    
    def _on_speech_started(self):
        """Called when speech is detected"""
        self.user_speaking = True
        self._update_activity_indicator()
        self.avatar_ring.add_css_class("call-avatar-ring-speaking")
        
        # Interrupt TTS if playing
        if self.assistant_speaking:
            if hasattr(self.controller, 'handlers') and self.controller.handlers.tts:
                self.controller.handlers.tts.stop()
            self.assistant_speaking = False
    
    def _on_speech_ended(self):
        """Called when speech ends"""
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
    
    def _process_speech(self):
        """Process recorded speech through STT and send to LLM"""
        if not self.speech_buffer:
            return
        
        # Save audio to temp file
        temp_path = os.path.join(self.controller.cache_dir, "call_recording.wav")
        try:
            wf = wave.open(temp_path, 'wb')
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(self.speech_buffer))
            wf.close()
        except Exception as e:
            print(f"Error saving audio: {e}")
            return
        
        # Recognize speech
        threading.Thread(
            target=self._recognize_and_respond,
            args=(temp_path,),
            daemon=True
        ).start()
    
    def _recognize_and_respond(self, audio_path):
        """Recognize speech and get AI response"""
        try:
            # Get STT handler
            stt = self.controller.handlers.stt
            if not stt or not stt.is_installed():
                GLib.idle_add(
                    self._add_message_to_history,
                    "System",
                    _("Speech recognition not available"),
                    True
                )
                return

            # Recognize
            text = stt.recognize_file(audio_path)
            if not text or text.strip() == "":
                return

            # Add user message to history
            GLib.idle_add(self._add_message_to_history, self.username, text, False)

            # Get LLM response
            self._get_ai_response(text)

        except Exception as e:
            import traceback
            print(f"Recognition error: {e}")
            print(traceback.format_exc())
            GLib.idle_add(
                self._add_message_to_history,
                "System",
                _("Recognition error. Please try again."),
                True
            )
    
    def _get_ai_response(self, user_message):
        """Get AI response and play TTS using run_llm_with_tools"""
        try:
            if self.chat_id is None:
                self.chat_id = self.controller.create_call_chat()
            streaming_text = ""
            def on_message_callback(text):
                nonlocal streaming_text
                streaming_text += text

            def on_tool_result_callback(tool_name, result):
                tool_output = result.get_output() if result else "Tool executed"
                GLib.idle_add(
                    self._add_message_to_history,
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
                    max_tool_calls=5,
                    save_chat=True,
                )
            finally:
                self.controller.is_call_request = False

            if response:
                GLib.idle_add(self._add_message_to_history, self.profile_name, response, False)
                if self.call_active:
                    response = clean_message_tts(response) 
                    self._play_tts(response)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            print(f"LLM error: {e}")
            # Ensure flag is reset
            self.controller.is_call_request = False
            GLib.idle_add(
                self._add_message_to_history,
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
    
    def _play_tts(self, text):
        """Play TTS for the response"""
        if not text or not self.call_active:
            return

        tts = self.controller.handlers.tts
        if not tts:
            return

        def on_tts_start():
            if self.call_active:
                GLib.idle_add(self._set_assistant_speaking, True)

        def on_tts_stop():
            if self.call_active:
                GLib.idle_add(self._set_assistant_speaking, False)

        tts.connect("start", on_tts_start)
        tts.connect("stop", on_tts_stop)

        try:
            tts.play(text)
        except Exception as e:
            import traceback
            print(f"TTS error: {e}")
            print(traceback.format_exc())
            GLib.idle_add(self._set_assistant_speaking, False)
    
    def _set_assistant_speaking(self, speaking):
        """Update assistant speaking state"""
        self.assistant_speaking = speaking
        self._update_activity_indicator()
        if speaking:
            self.avatar_ring.add_css_class("call-avatar-ring-speaking")
        else:
            self.avatar_ring.remove_css_class("call-avatar-ring-speaking")
    
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

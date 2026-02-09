"""Wakeword Detection System using Silero VAD

This module provides continuous wakeword detection by:
1. Listening to microphone continuously
2. Using pysilero-vad to detect voice activity
3. Transcribing speech segments using existing STT handler
4. Checking for wakeword and triggering callback
"""

import os
import threading
import time
import uuid
import wave
from collections import deque
from gi.repository import GLib

try:
    import pyaudio
    from pysilero_vad import SileroVoiceActivityDetector
    import array
    DEPENDENCIES_AVAILABLE = True
except ImportError:
    DEPENDENCIES_AVAILABLE = False


class WakewordDetector:
    """Continuous wakeword detector using Silero VAD and STT"""

    def __init__(self, stt_handler, wakeword, vad_aggressiveness=1,
                 pre_buffer_duration=0.5, silence_duration=1.0, energy_threshold=500, callback=None,
                 on_speech_started=None, on_transcribing=None, on_transcribing_done=None):
        """Initialize wakeword detector

        Args:
            stt_handler: STTHandler instance for transcription
            wakeword: Word or phrase to detect (case-insensitive)
            vad_aggressiveness: Kept for compatibility (not used by Silero VAD)
            pre_buffer_duration: Seconds of audio to capture before speech (0.1-2.0)
            silence_duration: Seconds of silence to end speech segment (0.5-5.0)
            energy_threshold: Audio energy threshold to filter noise (0-1000)
            callback: Function to call when wakeword detected (receives command text)
            on_speech_started: Callback when speech detection starts
            on_transcribing: Callback when transcription starts
            on_transcribing_done: Callback when transcription completes
        """
        if not DEPENDENCIES_AVAILABLE:
            raise ImportError("pysilero-vad, pyaudio, or numpy not available")

        self.stt_handler = stt_handler
        self.wakewords = self._parse_wakewords(wakeword)
        self.vad_aggressiveness = vad_aggressiveness  # Kept for compatibility
        self.pre_buffer_duration = pre_buffer_duration
        self.silence_duration = silence_duration
        self.energy_threshold = energy_threshold
        self.callback = callback
        self.on_speech_started = on_speech_started
        self.on_transcribing = on_transcribing
        self.on_transcribing_done = on_transcribing_done

        # Audio settings
        self.sample_rate = 16000  # Silero VAD works at 16kHz
        self.channels = 1

        # Noise reduction settings
        # Minimum speech frames: consecutive speech frames required to start detecting
        self.min_speech_frames = 1  # Require ~32ms of continuous speech (512 samples / 16000Hz)
        # Minimum speech ratio: percentage of frames that must be speech in a segment
        self.min_speech_ratio = 0.2  # 20% of frames must be speech (less strict for faster response)

        # State
        self.running = False
        self.thread = None
        self.audio = None
        self.stream = None
        self.vad = None
        self.chunk_size = None  # Will be set by VAD

    def is_running(self):
        """Check if detector is running"""
        return self.running

    def _parse_wakewords(self, wakeword_string):
        """Parse comma-separated wakewords into a list

        Args:
            wakeword_string: Comma-separated string of wakewords

        Returns:
            List of lowercase wakewords with whitespace stripped
        """
        if not wakeword_string:
            return []
        return [w.strip().lower() for w in wakeword_string.split(',') if w.strip()]

    def set_wakeword(self, word):
        """Update wakeword at runtime"""
        self.wakewords = self._parse_wakewords(word)

    def set_pre_buffer_duration(self, duration):
        """Update pre-buffer duration at runtime"""
        self.pre_buffer_duration = duration

    def set_silence_duration(self, duration):
        """Update silence duration at runtime"""
        self.silence_duration = duration

    def set_energy_threshold(self, threshold):
        """Update energy threshold at runtime"""
        self.energy_threshold = threshold

    def set_stt_handler(self, stt_handler):
        """Update STT handler at runtime"""
        self.stt_handler = stt_handler

    def _calculate_rms_energy(self, frame):
        """Calculate RMS (root mean square) energy of audio frame

        Args:
            frame: Audio frame bytes

        Returns:
            RMS energy value (0-32767 for 16-bit audio)
        """
        try:
            # Convert bytes to array of 16-bit integers
            audio_data = array.array('h', frame)
            if len(audio_data) == 0:
                return 0

            # Calculate RMS
            sum_squares = sum(x * x for x in audio_data)
            rms = (sum_squares / len(audio_data)) ** 0.5
            return rms
        except:
            return 0

    def _init_audio(self):
        """Initialize PyAudio and Silero VAD"""
        self.audio = pyaudio.PyAudio()

        # Initialize Silero VAD and get required chunk size
        self.vad = SileroVoiceActivityDetector()
        self.chunk_size = self.vad.chunk_samples()  # Returns 512 for 16kHz

        # Try to find a working input device
        device_index = None
        try:
            # Try default input device
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                input_device_index=device_index
            )
            self.stream.start_stream()
            print(f"WakewordDetector: Using default audio device (sample_rate={self.sample_rate}, chunk_size={self.chunk_size})")
        except Exception as e:
            print(f"WakewordDetector: Error opening audio stream: {e}")
            raise

    def _cleanup_audio(self):
        """Cleanup audio resources"""
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
            self.stream = None

        if self.audio is not None:
            try:
                self.audio.terminate()
            except:
                pass
            self.audio = None

    def _save_to_wav(self, frames, filename):
        """Save audio frames to WAV file

        Args:
            frames: List of audio frames
            filename: Output filename

        Returns:
            True if successful
        """
        try:
            with wave.open(filename, 'wb') as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
                wf.setframerate(self.sample_rate)
                wf.writeframes(b''.join(frames))
            return True
        except Exception as e:
            print(f"WakewordDetector: Error saving WAV: {e}")
            return False

    def _transcribe_audio(self, temp_file):
        """Transcribe audio file using STT handler

        Args:
            temp_file: Path to WAV file

        Returns:
            Transcribed text or None
        """
        print("recognizing")
        try:
            result = self.stt_handler.recognize_file(temp_file)
            return result
        except Exception as e:
            print(f"WakewordDetector: Transcription error: {e}")
            return None

    def _process_speech(self, frames):
        """Process detected speech segment

        Args:
            frames: List of audio frames
        """
        if not frames or len(frames) == 0:
            return

        # Save to temp file in GLib cache directory
        temp_file_path = None
        try:
            # Notify UI that transcription is starting
            if self.on_transcribing:
                GLib.idle_add(self.on_transcribing)

            # Use GLib cache directory for temp files
            cache_dir = GLib.get_user_cache_dir()
            wakeword_cache_dir = os.path.join(cache_dir, "newelle", "wakeword")
            os.makedirs(wakeword_cache_dir, exist_ok=True)

            # Create unique temp file name
            import uuid
            temp_file_path = os.path.join(wakeword_cache_dir, f"speech_{uuid.uuid4().hex}.wav")

            if not self._save_to_wav(frames, temp_file_path):
                return

            # Transcribe
            result = self._transcribe_audio(temp_file_path)

            result_lower = result.lower() if result else ""
            matched_wakeword = None
            for wakeword in self.wakewords:
                if wakeword in result_lower:
                    matched_wakeword = wakeword
                    break

            if matched_wakeword:
                # Remove matched wakeword from text
                command = result_lower.replace(matched_wakeword, "").strip()

                print(f"WakewordDetector: Wakeword '{matched_wakeword}' detected! Command: '{command}'")

                # Call callback on main thread
                if self.callback:
                    GLib.idle_add(self.callback, command)
            else:
                print(f"WakewordDetector: Speech detected but no wakeword found. Transcription: '{result_lower}'")

        except Exception as e:
            print(f"WakewordDetector: Error processing speech: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Notify UI that transcription is done
            if self.on_transcribing_done:
                GLib.idle_add(self.on_transcribing_done)
            # Cleanup temp file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as e:
                    print(f"WakewordDetector: Error removing temp file: {e}")

    def _detection_loop(self):
        """Main detection loop (runs in daemon thread)"""
        try:
            self._init_audio()

            # Pre-buffer to capture audio before speech starts
            pre_buffer_size = int(self.pre_buffer_duration * self.sample_rate / self.chunk_size)
            pre_buffer = deque(maxlen=pre_buffer_size)

            # State tracking
            in_speech = False
            silence_frames = 0
            silence_threshold = int(self.silence_duration * self.sample_rate / self.chunk_size)
            speech_frames = []
            consecutive_speech_frames = 0  # Track consecutive speech frames

            wakewords_str = ', '.join(f"'{w}'" for w in self.wakewords)
            print(f"WakewordDetector: Starting detection loop for {wakewords_str}")
            print(f"WakewordDetector: Energy threshold={self.energy_threshold}, min speech frames={self.min_speech_frames}")

            while self.running:
                try:
                    # Read audio chunk
                    if self.stream is None or not self.stream.is_active():
                        print("WakewordDetector: Stream not active, attempting to reinitialize...")
                        self._cleanup_audio()
                        time.sleep(0.5)
                        self._init_audio()
                        continue

                    frame = self.stream.read(self.chunk_size, exception_on_overflow=False)

                    if not frame or len(frame) == 0:
                        continue

                    # Add to pre-buffer
                    pre_buffer.append(frame)

                    # Calculate audio energy to filter out low-level noise
                    energy = self._calculate_rms_energy(frame)

                    # Skip low-energy frames (noise)
                    if energy < self.energy_threshold:
                        # Reset consecutive speech counter on low energy
                        if not in_speech:
                            consecutive_speech_frames = 0
                        continue

                    # Run VAD on frame - Silero VAD returns probability (0-1)
                    # Speech is detected when probability >= 0.5
                    try:
                        speech_probability = self.vad(frame)
                        is_speech = speech_probability >= 0.5
                    except Exception as vad_error:
                        print(f"WakewordDetector: VAD error: {vad_error}")
                        # Skip this frame on VAD error
                        continue

                    if is_speech:
                        consecutive_speech_frames += 1

                        # Only start capturing speech if we have enough consecutive speech frames
                        if consecutive_speech_frames >= self.min_speech_frames and not in_speech:
                            # Speech started - capture pre-buffer
                            in_speech = True
                            speech_frames = list(pre_buffer)
                            print("WakewordDetector: Speech started")
                            # Notify UI that speech detection started
                            if self.on_speech_started:
                                GLib.idle_add(self.on_speech_started)

                        if in_speech:
                            speech_frames.append(frame)
                            silence_frames = 0
                    else:
                        # Reset consecutive speech counter on non-speech
                        if not in_speech:
                            consecutive_speech_frames = max(0, consecutive_speech_frames - 1)

                        if in_speech:
                            silence_frames += 1

                            # Check for silence timeout - STOP recording immediately
                            if silence_frames >= silence_threshold:
                                # Don't append silence frames - process what we have
                                if len(speech_frames) > 0:
                                    # Calculate speech ratio (consecutive speech / total frames captured)
                                    speech_ratio = consecutive_speech_frames / len(speech_frames)
                                    print(f"WakewordDetector: Speech ended, processing... (speech ratio: {speech_ratio:.2f}, frames: {len(speech_frames)})")
                                    self._process_speech(speech_frames)
                                in_speech = False
                                speech_frames = []
                                consecutive_speech_frames = 0
                                pre_buffer.clear()

                except OSError as e:
                    print(f"WakewordDetector: Audio I/O error: {e}")
                    if not self.running:
                        break
                    # Try to reinitialize audio
                    try:
                        self._cleanup_audio()
                        time.sleep(1.0)
                        self._init_audio()
                    except:
                        time.sleep(0.5)
                except Exception as e:
                    print(f"WakewordDetector: Error in detection loop: {type(e).__name__}: {e}")
                    import traceback
                    traceback.print_exc()
                    if not self.running:
                        break
                    time.sleep(0.1)

        except Exception as e:
            print(f"WakewordDetector: Fatal error in detection loop: {e}")
        finally:
            self._cleanup_audio()
            print("WakewordDetector: Detection loop stopped")

    def start(self):
        """Start continuous listening in daemon thread"""
        if self.running:
            print("WakewordDetector: Already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.thread.start()
        print("WakewordDetector: Started")

    def stop(self):
        """Stop listening"""
        if not self.running:
            return

        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None

        self._cleanup_audio()
        print("WakewordDetector: Stopped")

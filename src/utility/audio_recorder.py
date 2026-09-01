import pyaudio
import wave
import struct
from typing import Callable
import os
import math
import threading

class AudioRecorder:
    """Record audio with optional auto-stop on silence detection."""
    def __init__(self, auto_stop: bool = False, stop_function: Callable = lambda *_args: (), silence_threshold_percent: float = 0.01, silence_duration: int = 2):
        self.recording = False
        self.frames = []
        self.auto_stop = auto_stop
        self.stop_function = stop_function
        self.silence_threshold_percent = silence_threshold_percent
        self.silence_duration = silence_duration
        self.sample_format = pyaudio.paInt16
        self.channels = 1
        self.sample_rate = 44100
        self.chunk_size = 1024
        self.silent_chunks = 0
        self.max_rms = 1000  # Max reasonable value for rms 
        self.sample_width = 2  # paInt16
        self._stop_event = threading.Event()
        self._capture_stopped = threading.Event()
        self._capture_stopped.set()
        self._state_lock = threading.Lock()

    def start_recording(self, output_file):
        try:
            if os.path.exists(output_file):
                os.remove(output_file)
        except OSError as exc:
            print(f"Could not prepare recording file: {exc}")
            return False

        with self._state_lock:
            if not self._capture_stopped.is_set():
                return False
            self._capture_stopped.clear()
            self._stop_event.clear()
            self.recording = True

        self.frames = []
        self.silent_chunks = 0

        p = None
        stream = None
        try:
            # The worker that creates these objects is the only owner allowed
            # to stop/close/terminate them.
            p = pyaudio.PyAudio()
            stream = p.open(
                format=self.sample_format,
                channels=self.channels,
                rate=self.sample_rate,
                frames_per_buffer=self.chunk_size,
                input=True,
            )
            silence_threshold = self.max_rms * self.silence_threshold_percent
            required_chunks = math.ceil(
                self.silence_duration * (self.sample_rate / self.chunk_size)
            )
            while not self._stop_event.is_set():
                data = stream.read(self.chunk_size, exception_on_overflow=False)
                self.frames.append(data)
                if self.auto_stop:
                    rms = self._calculate_rms(data)
                    if rms < silence_threshold:
                        self.silent_chunks += 1
                    else:
                        self.silent_chunks = 0
                    if self.silent_chunks >= required_chunks:
                        self._stop_event.set()
        except Exception as exc:
            # Keep teardown in this worker even when the device disappears or
            # the stream cannot be opened.
            print(f"Audio recording error: {exc}")
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
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass

            with self._state_lock:
                self.recording = False

            try:
                self.save_recording(output_file)
            finally:
                try:
                    self.stop_function()
                finally:
                    # Signal completion only after the WAV has been written
                    # and the UI callback has been queued.
                    self._capture_stopped.set()
        return True

    def stop_recording(self, output_file=None):
        del output_file
        self.recording = False
        self._stop_event.set()

    def is_stopped(self):
        """Return whether the capture worker has released native resources."""
        return self._capture_stopped.is_set()

    def save_recording(self, output_file):
        with wave.open(output_file, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.sample_width)
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(self.frames))

    def _calculate_rms(self, data):
        """Calculate the root mean square of the audio data."""
        count = len(data) // 2  # Each sample is 2 bytes (16-bit)
        format = "<" + str(count) + "h"  # little-endian signed shorts
        shorts = struct.unpack(format, data)
        mean = sum(shorts) / count
        shorts_demeaned = [sample - mean for sample in shorts]
        sum_squares = sum(sample * sample for sample in shorts_demeaned)
        rms = (sum_squares / count) ** 0.5
        return rms

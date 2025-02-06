import pyaudio 
import wave
import struct
from typing import Callable
import os
import math

class AudioRecorder:
    """Record audio with optional auto-stop on silence detection."""
    def __init__(self, auto_stop: bool = False, stop_function: Callable = lambda _: (), silence_threshold_percent: float = 0.01, silence_duration: int = 2):
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

    def start_recording(self, output_file):
        if os.path.exists(output_file):
            os.remove(output_file)
        self.recording = True
        self.frames = []
        self.silent_chunks = 0
        p = pyaudio.PyAudio()
        stream = p.open(format=self.sample_format,
                       channels=self.channels,
                       rate=self.sample_rate,
                       frames_per_buffer=self.chunk_size,
                       input=True)
        silence_threshold = self.max_rms * self.silence_threshold_percent
        required_chunks = math.ceil(self.silence_duration * (self.sample_rate / self.chunk_size))
        while self.recording:
            data = stream.read(self.chunk_size)
            self.frames.append(data)
            if self.auto_stop:
                rms = self._calculate_rms(data)
                if rms < silence_threshold:
                    self.silent_chunks += 1
                else:
                    self.silent_chunks = 0
                if self.silent_chunks >= required_chunks:
                    self.recording = False
        stream.stop_stream()
        stream.close()
        p.terminate()
        self.save_recording(output_file)

    def stop_recording(self, output_file):
        self.recording = False

    def save_recording(self, output_file):
        p = pyaudio.PyAudio()
        wf = wave.open(output_file, 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(p.get_sample_size(self.sample_format))
        wf.setframerate(self.sample_rate)
        wf.writeframes(b''.join(self.frames))
        wf.close()
        p.terminate()
        self.stop_function()

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

from abc import abstractmethod
from typing import Callable

from subprocess import Popen
import threading 
import time
import os
from ..handler import Handler

class TTSHandler(Handler):
    """Every TTS handler should extend this class."""
    key = ""
    schema_key = "tts-voice"
    voices : tuple
    _play_lock : threading.Semaphore = threading.Semaphore(1)
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.settings = settings
        self.path = path
        self.voices = tuple()
        self.on_start = lambda : None
        self.on_stop  = lambda : None
        self.play_process = None

    def get_extra_settings(self) -> list:
        """Get extra settings for the TTS"""
        voices = self.get_voices()
        default = "" if len(voices) == 0 else voices[0][1]
        return [
            {
                "key": "voice",
                "type": "combo",
                "title": _("Voice"),
                "description": _("Choose the preferred voice"),
                "default": default,
                "values": voices
            }
        ]

    def get_voices(self):
        """Return a tuple containing the available voices"""
        return tuple()

    def voice_available(self, voice):
        """Check fi a voice is available"""
        for l in self.get_voices():
            if l[1] == voice:
                return True
        return False

    @abstractmethod
    def save_audio(self, message, file):
        """Save an audio in a certain file path"""
        pass

    def play_audio(self, message):
        """Play an audio from the given message"""
        # Generate random name
        file_name = self.get_tempname("wav")
        path = os.path.join(self.path, file_name)
        self.save_audio(message, path)
        self.playsound(path)
        try:
            os.remove(path)
        except Exception as e:
            print("Could not delete file: " + str(e))
    
    def get_tempname(self, extension: str) -> str:
        timestamp = str(int(time.time()))
        random_part = str(os.urandom(8).hex())
        file_name = f"{timestamp}_{random_part}." + extension
        return file_name
    
    def connect(self, signal: str, callback: Callable):
        if signal == "start":
            self.on_start = callback
        elif signal == "stop":
            self.on_stop = callback

    def playsound(self, path):
        """Play an audio from the given path, handling incorrect file extensions"""
        import mimetypes
        self.stop()
        self._play_lock.acquire()
        self.on_start()
        try:
            p = Popen(["ffplay", "-nodisp", "-autoexit", "-hide_banner", path])
            self.play_process = p
            p.wait()
            p.terminate()
        except Exception as e:
            print("Error playing the audio: " + str(e))
            pass
        self.on_stop()
        self.play_process = None
        self._play_lock.release()
     
    def stop(self):
        if self.play_process is not None:
            self.play_process.terminate()

    def get_current_voice(self):
        """Get the current selected voice"""
        voice = self.get_setting("voice")
        if voice is None:
            if self.voices == ():
                return None
            return self.voices[0][1]
        else:
            return voice

    def set_voice(self, voice):
        """Set the given voice"""
        self.set_setting("voice", voice)

    def play(self, text):
        """Automatically plays with either stream or regular TTS"""
        if self.streaming_enabled():
            self.play_audio_stream(text)
        else:
            self.play_audio(text)
    def streaming_enabled(self) -> bool:
        """Return True if the TTS handler supports streaming audio playback."""
        return False

    def play_audio_stream(self, message):
        """Play audio from the given message using streaming.

        Drives ``get_audio_stream(message)`` through an ffmpeg → ffplay pipeline.
        The format is determined by ``get_stream_format_args()``.  Subclasses only
        need to override ``get_audio_stream`` and ``get_stream_format_args``; the
        playback machinery is handled here.
        """
        import subprocess

        stop_event = threading.Event()

        self.stop()
        self._play_lock.acquire()
        self.on_start()

        ffmpeg_process = None
        ffplay_process = None

        def monitor_ffplay():
            if ffplay_process:
                ffplay_process.wait()
                stop_event.set()

        try:
            fmt_args = self.get_stream_format_args()

            ffmpeg_process = Popen(
                ["ffmpeg"] + fmt_args + ["-i", "-", "-f", "wav", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            ffplay_process = Popen(
                ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-i", "pipe:0"],
                stdin=ffmpeg_process.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.play_process = ffplay_process
            ffmpeg_process.stdout.close()

            threading.Thread(target=monitor_ffplay, daemon=True).start()

            try:
                for chunk in self.get_audio_stream(message):
                    if stop_event.is_set():
                        break
                    ffmpeg_process.stdin.write(chunk)
            except BrokenPipeError:
                pass
            finally:
                try:
                    ffmpeg_process.stdin.close()
                except Exception:
                    pass

        except Exception as e:
            print("Error playing streaming audio: " + str(e))
        finally:
            stop_event.set()
            for proc in (ffmpeg_process, ffplay_process):
                if proc is not None:
                    try:
                        proc.wait()
                        proc.terminate()
                    except Exception:
                        pass
            self.play_process = None
            self.on_stop()
            self._play_lock.release()

    def get_stream_format_args(self) -> list:
        """Return ffmpeg/ffplay input format arguments for the audio produced by get_audio_stream.

        These are prepended before ``-i pipe:0`` in ffmpeg and before ``-i -`` in ffplay so
        that the tools know how to interpret the raw byte stream.  For WAV output (the
        default) no arguments are needed because ffmpeg auto-detects the container.
        Override in subclasses that produce a specific raw format (e.g. mp3, s16le).

        Returns:
            list: e.g. ["-f", "mp3"] or ["-f", "s16le", "-ar", "24000", "-ac", "1"]
        """
        return []

    def get_audio_stream(self, message):
        """Yield raw audio bytes for *message* as a generator.

        The default implementation saves audio to a temporary WAV file and yields
        its contents in 4096-byte chunks.  Streaming-capable subclasses should
        override this to yield audio bytes directly as they are produced, avoiding
        the need to buffer the entire clip first.

        Yields:
            bytes: successive chunks of raw audio bytes in the format described by
                   ``get_stream_format_args()``.
        """
        file_name = self.get_tempname("wav")
        path = os.path.join(self.path, file_name)
        try:
            self.save_audio(message, path)
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

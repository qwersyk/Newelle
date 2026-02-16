import threading
import asyncio
import io
import subprocess
from subprocess import Popen
from pydub import AudioSegment

from .tts import TTSHandler
from ...utility.force_sync import force_sync

class EdgeTTSHandler(TTSHandler):
    key = "edge_tts"
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.voices = tuple()
        voices = self.get_setting("voices")
        if voices is None or len(voices) < 2:
            self.voices = (("en-US-AvaNeural","en-US-AvaNeural"), ("en-GB-SoniaNeural", "en-GB-SoniaNeural"),)
            threading.Thread(target=self.get_voices).start() 
        elif len(voices) > 0:
            self.voices = self.get_setting("voices")

    @staticmethod
    def get_extra_requirements() -> list:
        return ["edge_tts"]

    def install(self):
        super().install()
        threading.Thread(target=self.get_voices).start() 
       
    def get_extra_settings(self) -> list:
        return [ 
            {   
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "default": "en-US-AvaNeural",
            },
            {
                "key": "pitch",
                "title": "Pitch",
                "description": "Pitch to use",
                "type": "range",
                "min": 0.0,
                "max": 40.0,
                "round-digits": 0,
                "default": 0.0
            }, 
        ]

    def save_audio(self, message, file):
        import edge_tts
        communicate = edge_tts.Communicate(message, self.get_setting("voice"), pitch= "+{}Hz".format(round(self.get_setting("pitch"))))
        mp3 = file + ".mp3"
        communicate.save_sync(mp3)
        AudioSegment.from_mp3(mp3).export(file, format="wav")

    def get_voices(self) -> tuple:
        if not self.is_installed():
            return self.voices
        import edge_tts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        @force_sync 
        async def get_voices():
            voices = await edge_tts.list_voices()
            voices = sorted(voices, key=lambda voice: voice["ShortName"])
            result = tuple()
            for voice in voices:
                result += ((voice["ShortName"], voice["ShortName"]),)
            self.voices = result
            self.set_setting("voices", self.voices)
            self.settings_update()
        _ = get_voices()
        return self.voices

    def streaming_enabled(self) -> bool:
        return True

    def play_audio_stream(self, message):
        import edge_tts
        import os

        self.stop()
        self._play_lock.acquire()
        self.on_start()

        try:
            ffplay_process = None
            try:
                ffplay_process = Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-f", "mp3", "-i", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.play_process = ffplay_process
                communicate = edge_tts.Communicate(
                    message,
                    self.get_setting("voice"),
                    pitch="+{}Hz".format(round(self.get_setting("pitch")))
                )

                for chunk in communicate.stream_sync():
                    if chunk["type"] == "audio" and ffplay_process.stdin:
                        ffplay_process.stdin.write(chunk["data"])

                if ffplay_process.stdin:
                    ffplay_process.stdin.close()

            finally:
                if ffplay_process is not None:
                    ffplay_process.wait()
                    ffplay_process.terminate()

        except Exception as e:
            print("Error playing streaming audio: " + str(e))
            pass

        self.on_stop()
        self._play_lock.release()

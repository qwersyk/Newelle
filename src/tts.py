from abc import abstractmethod
from typing import Any, Callable
from gtts import gTTS, lang
from subprocess import check_output
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
from pygame import mixer
import threading, time, requests
import os, json, pyaudio
from .extra import can_escape_sandbox, force_sync
from pydub import AudioSegment
import asyncio, random, string
from requests_toolbelt.multipart.encoder import MultipartEncoder
from .handler import Handler

class TTSHandler(Handler):
    """Every TTS handler should extend this class."""
    key = ""
    schema_key = "tts-voice"
    voices : tuple
    _play_lock : threading.Semaphore = threading.Semaphore(1)
    def __init__(self, settings, path):
        mixer.init()
        self.settings = settings
        self.path = path
        self.voices = tuple()
        self.on_start = lambda : None
        self.on_stop  = lambda : None
        pass

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

    def get_tempname(self, extension: str):
        timestamp = str(int(time.time()))
        random_part = str(os.urandom(8).hex())
        file_name = f"{timestamp}_{random_part}." + extension
        return file_name
 
    def play_audio(self, message):
        """Play an audio from the given message"""
        # Generate random name
        file_name = self.get_tempname("wav")
        path = os.path.join(self.path, file_name)
        self.save_audio(message, path)
        self.playsound(path)
        os.remove(path)

    def connect(self, signal: str, callback: Callable):
        if signal == "start":
            self.on_start = callback
        elif signal == "stop":
            self.on_stop = callback

    def playsound(self, path):
        """Play an audio from the given path"""
        self.stop()
        self._play_lock.acquire()
        self.on_start()
        mixer.music.load(path)
        mixer.music.play()
        while mixer.music.get_busy():
            time.sleep(0.1)
        self.on_stop()
        self._play_lock.release()

    def stop(self):
        if mixer.music.get_busy():
            mixer.music.stop()

    def is_playing(self) -> bool:
        return mixer.music.get_busy()

    def is_installed(self) -> bool:
        """If all the requirements are installed"""
        return True

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


class gTTSHandler(TTSHandler):
    key = "gtts"
   
    def get_voices(self):
        if len(self.voices) > 0:
            return self.voices
        x = lang.tts_langs()
        res = tuple()
        for l in x:
            t = (x[l], l)
            res += (t,)
        self.voices = res
        return res

    def save_audio(self, message, file):
        voice = self.get_current_voice()
        if not self.voice_available(voice):
            voice = self.get_voices()[0][1]
        tts = gTTS(message, lang=voice)
        tts.save(file)


class EspeakHandler(TTSHandler):
    
    key = "espeak"

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_voices(self):
        if len(self.voices) > 0:
            return self.voices
        if not self.is_installed():
            return self.voices
        output = check_output(["flatpak-spawn", "--host", "espeak", "--voices"]).decode("utf-8")
        # Extract the voice names from the output
        lines = output.strip().split("\n")[1:]
        voices = tuple()
        for line in lines:
            spl = line.split()
            voices += ((spl[3], spl[4]),)
        self.voices = voices
        return voices

    def play_audio(self, message):
        self._play_lock.acquire()
        check_output(["flatpak-spawn", "--host", "espeak", "-v" + str(self.get_current_voice()), message])
        self._play_lock.release()

    def save_audio(self, message, file):
        r = check_output(["flatpak-spawn", "--host", "espeak", "-f", "-v" + str(self.get_current_voice()), message, "--stdout"])
        f = open(file, "wb")
        f.write(r)

    def is_installed(self):
        if not can_escape_sandbox():
            return False
        output = check_output(["flatpak-spawn", "--host", "whereis", "espeak"]).decode("utf-8")
        paths = []
        if ":" in output:
            paths = output.split(":")[1].split()
        if len(paths) > 0:
            return True
        return False

class CustomTTSHandler(TTSHandler):
    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.key = "custom_command"
        self.voices = tuple()

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_extra_settings(self) -> list:
        return [{
            "key": "command",
            "title": _("Command to execute"),
            "description": _("{0} will be replaced with the model fullpath"),
            "type": "entry",
            "default": ""
        }]


    def is_installed(self):
        return True

    def play_audio(self, message):
        command = self.get_setting("command")
        if command is not None:
            self._play_lock.acquire()
            check_output(["flatpak-spawn", "--host", "bash", "-c", command.replace("{0}", message)])
            self._play_lock.release()


class VoiceVoxHanlder(TTSHandler):
    key = "voicevox"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._loop = asyncio.new_event_loop()
        self._thr = threading.Thread(target=self._loop.run_forever, name="Async Runner", daemon=True)
        self.voices = tuple()
        voices = self.get_setting("voices")
        if voices is None or len(voices) == 0:
            threading.Thread(target=self.get_voices).start() 
        elif len(voices) > 0:
            self.voices = self.get_setting("voices")

    def update_voices(self):
        if self.get_setting("voices") is None or len(self.get_setting("voices")) == 0:
            threading.Thread(target=self.get_voices).start()
    
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": "API Endpoint",
                "description": "URL of VoiceVox API endpoint",
                "type": "entry",
                "default": "https://meowskykung-voicevox-engine.hf.space",
            },
            {
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "default": "1",
            }
        ]

    def save_audio(self, message, file):
        from voicevox import Client

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        speaker = int(self.get_setting("voice"))
        endpoint = self.get_setting("endpoint")
        @force_sync
        async def save(message, speaker, endpoint):
            async with Client(base_url=endpoint) as client:
                audioquery = await client.create_audio_query(message, speaker=speaker)
                with open(file, "wb") as f:
                    f.write(await audioquery.synthesis(speaker=speaker))
        _ = save(message, speaker, endpoint)

    def get_voices(self) -> tuple:
        from voicevox import Client

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        endpoint = self.get_setting("endpoint")
        @force_sync
        async def get_voices(endpoint):
            ret = tuple()
            async with Client(base_url=endpoint) as client:
                speakers = await client.fetch_speakers()
                i = 1
                for speaker in speakers:
                    ret+= ((speaker.name, i), )
                    i+=1
            self.voices = ret
        _ = get_voices(endpoint)
        self.set_setting("voices", self.voices)
        return self.voices

    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            self.set_setting("voices", tuple())
            threading.Thread(target=self.get_voices).start()

class VitsHandler(TTSHandler):
    key = "vits"


    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.voices = tuple()
        voices = self.get_setting("voices")
        if voices is None or len(voices) == 0:
            threading.Thread(target=self.get_voices).start() 
        elif len(voices) > 0:
            self.voices = self.get_setting("voices")
    
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": "API Endpoint",
                "description": "URL of VITS API endpoint",
                "type": "entry",
                "default": "https://artrajz-vits-simple-api.hf.space/",
            },
            {   
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "default": "0",
            }

        ]

    def get_voices(self):
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        r = requests.get(endpoint + "/voice/speakers", timeout=10)
        if r.status_code == 200:
            js = r.json()
            result = tuple()
            for speaker in js["VITS"]:
                result += ((str(speaker["id"]) + "| " + speaker["name"] + " " + (str(speaker["lang"]) if len(speaker["lang"]) < 5 else "[Multi]"), str(speaker["id"])), )
            self.voices = result
            self.set_setting("voices", self.voices)
            return result 
        else:
            return tuple()

    def save_audio(self, message, file):
        self.voice_vits(message, file) 
    
    def voice_vits(self, text, filename, format="wav", lang="auto", length=1, noise=0.667, noisew=0.8, max=50):
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        id = self.get_setting("voice")
        fields = {
            "text": text,
            "id": str(id),
            "format": format,
            "lang": lang,
            "length": str(length),
            "noise": str(noise),
            "noisew": str(noisew),
            "max": str(max),
        }
        boundary = "----VoiceConversionFormBoundary" + "".join(
            random.sample(string.ascii_letters + string.digits, 16)
        )

        m = MultipartEncoder(fields=fields, boundary=boundary)
        headers = {"Content-Type": m.content_type}
        url = f"{endpoint}/voice"

        res = requests.post(url=url, data=m, headers=headers)
        path = filename

        with open(path, "wb") as f:
            f.write(res.content)
        return path
    
    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            self.set_setting("voices", tuple())
            threading.Thread(target=self.get_voices).start()

class EdgeTTSHandler(TTSHandler):
    key = "edge_tts"
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.voices = tuple()
        voices = self.get_setting("voices")
        if voices is None or len(voices) < 2:
            self.voices = (("en-GB-SoniaNeural", "en-GB-SoniaNeural"),)
            threading.Thread(target=self.get_voices).start() 
        elif len(voices) > 0:
            self.voices = self.get_setting("voices")

    def get_extra_settings(self) -> list:
        return [ 
            {   
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "default": "en-GB-SoniaNeural",
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

    def get_voices(self):
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
        _ = get_voices()
        return self.voices

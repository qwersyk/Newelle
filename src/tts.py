from abc import abstractmethod
from typing import Any
from gtts import gTTS, lang
from subprocess import check_output
import threading, time, requests
import os, json, pyaudio
from .extra import can_escape_sandbox, force_sync
from pydub import AudioSegment
import asyncio, random, string
from requests_toolbelt.multipart.encoder import MultipartEncoder

class TTSHandler:
    """Every TTS handler should extend this class."""
    key = ""
    voices : tuple
 
    _playing : bool = False
    _play_lock : threading.Semaphore = threading.Semaphore(1)

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path
        self.voices = tuple()
        pass

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return False

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

    @staticmethod
    def get_extra_requirements() -> list:
        """Get the extra requirements for the tts"""
        return []

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

    def playsound(self, path):
        self._play_lock.acquire()
        audio = AudioSegment.from_file(path)
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(format=self.p.get_format_from_width(audio.sample_width),
                        channels=audio.channels,
                        rate=audio.frame_rate,
                        output=True
                    )
        # Play audio
        self._playing = True
        self.stream.write(audio.raw_data)
        self._playing = False
        self._play_lock.release()

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

    def set_setting(self, setting, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string("tts-voice"))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][setting] = value
        self.settings.set_string("tts-voice", json.dumps(j))

    def get_setting(self, name) -> Any:
        """Get setting from key"""
        j = json.loads(self.settings.get_string("tts-voice"))
        if self.key not in j or not isinstance(j[self.key], dict) or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        """Get the default setting from a key"""
        for x in self.get_extra_settings():
            if x["key"] == name:
                return x["default"]
        return None

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





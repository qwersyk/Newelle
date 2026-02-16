from pathlib import Path
from .tts import TTSHandler
from ...utility.pip import install_module, find_module
from ...handlers import ErrorSeverity, ExtraSettings
import subprocess
from subprocess import Popen


class CustomOpenAITTSHandler(TTSHandler):
    key = "custom_openai_tts"
    response_format = "mp3"

    def install(self):
        # Assuming pip_path is available from the base class or context
        install_module("openai",self.pip_path)
        if not self.is_installed():
            self.throw("OpenAI installation failed", ErrorSeverity.ERROR)

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting("endpoint", _("Endpoint"), _("Custom endpoint of the service to use"), "https://api.openai.com/v1/"),
            ExtraSettings.EntrySetting("api_key", _("API Key"), _("The API key to use"), "", password=True),
            ExtraSettings.EntrySetting("voice", _("Voice"), _("The voice to use"), "alloy"),
            ExtraSettings.EntrySetting("model", _("Model"), _("The model to use"), "tts-1"),
            ExtraSettings.EntrySetting("instructions", _("Instructions"), _("Instructions for the voice generation. Leave it blank to avoid this field"), ""),
            ExtraSettings.ComboSetting("response_format", _("Response format"), _("The response format to use"), ["mp3", "wav"], "mp3"),
        ]
    def is_installed(self) -> bool:
        return find_module("openai") is not None 

    def save_audio(self, message, file):
        from openai import OpenAI
        from openai import NOT_GIVEN
        speech_file_path = file
        print("ae")
        try:
            client = OpenAI(api_key=self.get_setting("api_key"), base_url=self.get_setting("endpoint"))
            response = client.audio.speech.create(
                model=self.get_setting("model"), 
                voice=self.get_setting("voice"),
                input=message,
                response_format=self.get_setting("response_format"),
                instructions=self.get_setting("instructions") if self.get_setting("instructions") != "" else NOT_GIVEN
            )
            response.write_to_file(speech_file_path)
        except Exception as e:
            print(e)
            self.throw(f"TTS error: {e}", ErrorSeverity.ERROR)
   
    def streaming_enabled(self) -> bool:
        return True 

    def play_audio_stream(self, message):
        import threading

        self.stop()
        self._play_lock.acquire()
        self.on_start()

        ffmpeg_process = None
        ffplay_process = None
        stop_streaming = threading.Event()

        def monitor_ffplay():
            if ffplay_process:
                ffplay_process.wait()
                if not stop_streaming.is_set():
                    stop_streaming.set()

        try:
            ffmpeg_process = Popen(
                ["ffmpeg", "-f", self.get_setting("response_format"), "-i", "-", "-f", "wav", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )

            ffplay_process = Popen(
                ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-i", "pipe:0"],
                stdin=ffmpeg_process.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            self.play_process = ffplay_process

            threading.Thread(target=monitor_ffplay, daemon=True).start()

            if ffmpeg_process.stdout:
                ffmpeg_process.stdout.close()

            from openai import OpenAI
            from openai import NOT_GIVEN
            
            client = OpenAI(api_key=self.get_setting("api_key"), base_url=self.get_setting("endpoint"))
            
            response = client.audio.speech.create(
                model=self.get_setting("model"), 
                voice=self.get_setting("voice"),
                input=message,
                response_format=self.get_setting("response_format"),
            )
            
            if ffmpeg_process and ffmpeg_process.stdin:
                try:
                    for chunk in response.iter_bytes():
                        if stop_streaming.is_set():
                            break
                        ffmpeg_process.stdin.write(chunk)
                except BrokenPipeError:
                    pass

            if ffmpeg_process.stdin:
                try:
                    ffmpeg_process.stdin.close()
                except Exception:
                    pass

        except Exception as e:
            print("Error playing streaming audio: " + str(e))
            self._play_lock.release()
        finally:
            stop_streaming.set()
            if ffmpeg_process:
                ffmpeg_process.terminate()
                ffmpeg_process.wait()
            if ffplay_process:
                ffplay_process.wait()
                ffplay_process.terminate()
            self.on_stop()
            self._play_lock.release()

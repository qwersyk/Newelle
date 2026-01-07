from subprocess import check_output
import threading

from .tts import TTSHandler
from ...utility.system import can_escape_sandbox, get_spawn_command 
from gi.repository import GLib


class EspeakHandler(TTSHandler):
    
    key = "espeak"
    is_installed_check = False 

    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_voices(self):
        if len(self.voices) > 0:
            return self.voices
        if not self.is_installed():
            return self.voices
        output = check_output(get_spawn_command() + ["espeak", "--voices"]).decode("utf-8")
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
        check_output(get_spawn_command() + ["espeak", "-v" + str(self.get_current_voice()), message])
        self._play_lock.release()

    def save_audio(self, message, file):
        r = check_output(get_spawn_command() + ["espeak", "-f", "-v" + str(self.get_current_voice()), message, "--stdout"])
        f = open(file, "wb")
        f.write(r)

    def is_installed(self):
        if not can_escape_sandbox():
            return False
        GLib.idle_add(threading.Thread(target=self.is_installed_check).start) 
        return self.is_installed_check

    def check_install(self):
        output = check_output(get_spawn_command() + ["whereis", "espeak"]).decode("utf-8")
        paths = []
        if ":" in output:
            paths = output.split(":")[1].split()
        if len(paths) > 0:
            self.is_installed_check = True
        self.is_installed_check = False

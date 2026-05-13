import json
import os
import signal
import datetime

from ..handler import Handler


class Interface(Handler):
    schema_key = "interfaces-settings"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.controller = None

    def start(self):
        pass

    def stop(self):
        pass

    def is_locally_running(self):
        """Check if this interface instance is running locally (same process)."""
        return self._is_locally_running()

    def _is_locally_running(self):
        return False

    def is_running(self):
        return self._is_locally_running() or Interface.check_external_running(self.key, self.path)

    def set_controller(self, controller):
        self.controller = controller

    # ── State file management for cross-process interface detection ──

    @staticmethod
    def _get_state_file_path(key, path):
        """Get the state file path for a given interface key and handler path."""
        state_dir = os.path.join(os.path.dirname(path), "interface_states")
        return os.path.join(state_dir, f"{key}.json")

    def _get_state_dir(self):
        """Get (and create if needed) the directory for interface state files."""
        state_dir = os.path.join(os.path.dirname(self.path), "interface_states")
        os.makedirs(state_dir, exist_ok=True)
        return state_dir

    def _get_state_file(self):
        """Get the state file path for this interface."""
        return Interface._get_state_file_path(self.key, self.path)

    def _write_state_file(self):
        """Write a state file indicating this interface is running."""
        self._get_state_dir()  # ensure directory exists
        state = {
            "pid": os.getpid(),
            "key": self.key,
            "started_at": datetime.datetime.now().isoformat(),
        }
        with open(self._get_state_file(), "w") as f:
            json.dump(state, f)

    def _clear_state_file(self):
        """Remove the state file when the interface stops."""
        try:
            os.remove(self._get_state_file())
        except FileNotFoundError:
            pass

    @staticmethod
    def check_external_running(key, path):
        """Check if an interface is running in another process by reading its state file."""
        state_file = Interface._get_state_file_path(key, path)
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            pid = state.get("pid")
            if pid is None:
                return False
            # Check if the process is still alive
            os.kill(pid, 0)
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        except (ProcessLookupError, OSError):
            # Clean up stale state file
            try:
                os.remove(state_file)
            except FileNotFoundError:
                pass
            return False

    @staticmethod
    def stop_external(key, path):
        """Stop an externally running interface by killing its process.

        Returns True if the process was successfully terminated, False otherwise.
        """
        state_file = Interface._get_state_file_path(key, path)
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            pid = state.get("pid")
            if pid is None:
                return False
            os.kill(pid, signal.SIGTERM)
            os.remove(state_file)
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        except ProcessLookupError:
            # Process already gone, clean up stale file
            try:
                os.remove(state_file)
            except FileNotFoundError:
                pass
            return True
        except OSError:
            return False

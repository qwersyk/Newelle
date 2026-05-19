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

    @staticmethod
    def _get_process_start_time(pid):
        """Get the start time (in clock ticks since boot) of a process from /proc."""
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                data = f.read()
            close_paren = data.rfind(")")
            fields = data[close_paren + 2:].split()
            return int(fields[19])
        except (FileNotFoundError, ValueError, IndexError, PermissionError, OSError):
            return None

    @staticmethod
    def _is_same_process(pid, stored_start_time):
        """Check if the process with the given PID is the same one that wrote the state file."""
        if stored_start_time is None:
            return False
        if pid == os.getpid():
            return False
        current_start_time = Interface._get_process_start_time(pid)
        if current_start_time is None:
            return False
        return current_start_time == stored_start_time

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
        self._get_state_dir()
        state = {
            "pid": os.getpid(),
            "key": self.key,
            "started_at": datetime.datetime.now().isoformat(),
            "pid_start_time": Interface._get_process_start_time(os.getpid()),
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
    def _cleanup_state_file(state_file):
        try:
            os.remove(state_file)
        except (FileNotFoundError, OSError):
            pass

    @staticmethod
    def _read_state_file(state_file):
        """Read and validate a state file. Returns (state_dict, is_valid)."""
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None, False
        pid = state.get("pid")
        if not isinstance(pid, int):
            Interface._cleanup_state_file(state_file)
            return None, False
        if not Interface._is_same_process(pid, state.get("pid_start_time")):
            Interface._cleanup_state_file(state_file)
            return None, False
        return state, True

    @staticmethod
    def check_external_running(key, path):
        """Check if an interface is running in another process by reading its state file."""
        state_file = Interface._get_state_file_path(key, path)
        try:
            state, valid = Interface._read_state_file(state_file)
            if not valid or state is None:
                return False
            pid = state.get("pid")
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            Interface._cleanup_state_file(state_file)
            return False
        except Exception:
            Interface._cleanup_state_file(state_file)
            return False

    @staticmethod
    def stop_external(key, path):
        """Stop an externally running interface by killing its process.

        Returns True if the process was successfully terminated, False otherwise.
        """
        state_file = Interface._get_state_file_path(key, path)
        try:
            state, valid = Interface._read_state_file(state_file)
            if not valid or state is None:
                return False
            pid = state.get("pid")
            os.kill(pid, signal.SIGTERM)
            Interface._cleanup_state_file(state_file)
            return True
        except ProcessLookupError:
            Interface._cleanup_state_file(state_file)
            return True
        except OSError:
            return False
        except Exception:
            Interface._cleanup_state_file(state_file)
            return False

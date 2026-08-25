"""PTY-backed persistent command sessions for interactive assistant tools."""

from __future__ import annotations

import atexit
import codecs
from dataclasses import dataclass
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid


DEFAULT_SESSION_WAIT_MS = 500
DEFAULT_SESSION_OUTPUT_CHARS = 8000
MAX_SESSION_OUTPUT_CHARS = 20000
MAX_SESSION_WAIT_MS = 30000
SESSION_BUFFER_CHARS = 100000
MAX_RUNNING_SESSIONS_PER_OWNER = 8
MAX_FINISHED_SESSIONS_PER_OWNER = 8
SESSION_OUTPUT_SETTLE_SECONDS = 0.05


_PTY_CHILD_LAUNCHER = """
import fcntl
import os
import sys
import termios

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
"""


_FIXED_KEY_SEQUENCES = {
    "ENTER": "\r",
    "TAB": "\t",
    "SHIFT_TAB": "\x1b[Z",
    "ESC": "\x1b",
    "SPACE": " ",
    "BACKSPACE": "\x7f",
    "DELETE": "\x1b[3~",
    "UP": "\x1b[A",
    "DOWN": "\x1b[B",
    "RIGHT": "\x1b[C",
    "LEFT": "\x1b[D",
    "HOME": "\x1b[H",
    "END": "\x1b[F",
    "PAGE_UP": "\x1b[5~",
    "PAGE_DOWN": "\x1b[6~",
    "CTRL_BACKSLASH": "\x1c",
    "CTRL_RIGHT_BRACKET": "\x1d",
}
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _FIXED_KEY_SEQUENCES[f"CTRL_{_letter}"] = chr(ord(_letter) - ord("A") + 1)

SUPPORTED_KEY_NAMES = tuple(_FIXED_KEY_SEQUENCES)


class CommandSessionError(RuntimeError):
    """Raised when a persistent command session action is invalid."""


class _TerminalOutputCleaner:
    """Incrementally remove ANSI/terminal controls without leaking fragments."""

    _STRING_STARTS = frozenset("PX^_")

    def __init__(self):
        self._state = "text"

    def feed(self, text: str) -> str:
        output = []
        for character in text:
            if self._state == "text":
                if character == "\x1b":
                    self._state = "escape"
                elif character == "\r":
                    output.append("\n")
                elif character in "\n\t" or ord(character) >= 0x20:
                    if not 0x7F <= ord(character) <= 0x9F:
                        output.append(character)
                continue

            if self._state == "escape":
                if character == "[":
                    self._state = "csi"
                elif character == "]":
                    self._state = "osc"
                elif character in self._STRING_STARTS:
                    self._state = "string"
                elif 0x20 <= ord(character) <= 0x2F:
                    self._state = "escape_intermediate"
                else:
                    # Two-byte escape sequence, or a malformed lone escape.
                    self._state = "text"
                continue

            if self._state == "escape_intermediate":
                if 0x30 <= ord(character) <= 0x7E:
                    self._state = "text"
                continue

            if self._state == "csi":
                if "@" <= character <= "~":
                    self._state = "text"
                continue

            if self._state == "osc":
                if character == "\x07":
                    self._state = "text"
                elif character == "\x1b":
                    self._state = "osc_escape"
                continue

            if self._state == "osc_escape":
                self._state = "text" if character == "\\" else "osc"
                continue

            if self._state == "string":
                if character == "\x1b":
                    self._state = "string_escape"
                continue

            if self._state == "string_escape":
                self._state = "text" if character == "\\" else "string"

        return "".join(output)


@dataclass(frozen=True)
class CommandSessionRead:
    output: str
    lost_chars: int
    remaining_chars: int
    next_offset: int
    total_chars: int
    running: bool
    exit_code: int | None


class CommandSession:
    """One interactive process attached to a pseudo-terminal."""

    def __init__(
        self,
        session_id: str,
        command: str,
        working_dir: str,
        owner,
        host_prefix: list[str] | None = None,
        on_exit=None,
    ):
        self.session_id = session_id
        self.command = command
        self.working_dir = os.path.abspath(os.path.expanduser(working_dir))
        self.owner = owner
        self._on_exit = on_exit
        self.created_at = time.monotonic()
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._buffer = ""
        self._buffer_start = 0
        self._total_chars = 0
        self._read_offset = 0
        self._terminal_buffer = ""
        self._terminal_listeners = {}
        self._next_terminal_listener_id = 1
        self._master_fd: int | None = None
        self._exit_code: int | None = None

        if not os.path.isdir(self.working_dir):
            raise CommandSessionError(
                f"Working directory does not exist or is not a directory: {self.working_dir}"
            )

        master_fd, slave_fd = pty.openpty()
        try:
            self._set_window_size(slave_fd, rows=40, columns=120)
        except OSError as error:
            os.close(master_fd)
            os.close(slave_fd)
            raise CommandSessionError(str(error)) from error
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("BASH_FUNC_")
        }
        environment.update({"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "40"})
        cmd = [
            *(host_prefix or []),
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            command,
        ]
        # Popen's start_new_session creates the process group, while this tiny
        # exec wrapper makes the already-connected slave its controlling TTY.
        # Keeping this outside preexec_fn is important in the app's threaded
        # process: Python explicitly warns that preexec_fn can deadlock there.
        cmd = [sys.executable, "-c", _PTY_CHILD_LAUNCHER, *cmd]

        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=self.working_dir,
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            os.close(master_fd)
            os.close(slave_fd)
            raise CommandSessionError(str(error)) from error
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        self._master_fd = master_fd
        os.set_blocking(master_fd, False)
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"command-session-{session_id}",
            daemon=True,
        )
        self._reader_thread.start()

    @staticmethod
    def _set_window_size(fd: int, rows: int, columns: int) -> None:
        size = struct.pack("HHHH", rows, columns, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)

    @property
    def is_running(self) -> bool:
        return self.process.poll() is None

    @property
    def exit_code(self) -> int | None:
        code = self.process.poll()
        return self._exit_code if code is None else code

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def total_chars(self) -> int:
        with self._condition:
            return self._total_chars

    def _append_output(self, text: str, terminal_text: str = "") -> None:
        listeners = ()
        with self._condition:
            if text:
                self._buffer += text
                self._total_chars += len(text)
                overflow = len(self._buffer) - SESSION_BUFFER_CHARS
                if overflow > 0:
                    self._buffer = self._buffer[overflow:]
                    self._buffer_start += overflow
                self._condition.notify_all()
            if terminal_text:
                self._terminal_buffer = (
                    self._terminal_buffer + terminal_text
                )[-SESSION_BUFFER_CHARS:]
                listeners = tuple(self._terminal_listeners.values())

        for listener in listeners:
            try:
                listener(terminal_text)
            except Exception:
                pass

    def _reader_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        cleaner = _TerminalOutputCleaner()
        master_fd = self._master_fd
        try:
            while master_fd is not None:
                try:
                    readable, _, _ = select.select([master_fd], [], [], 0.1)
                except (OSError, ValueError):
                    break
                if not readable:
                    if self.process.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                except BlockingIOError:
                    continue
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    break
                if not chunk:
                    break
                terminal_text = decoder.decode(chunk)
                self._append_output(cleaner.feed(terminal_text), terminal_text)
            terminal_text = decoder.decode(b"", final=True)
            self._append_output(cleaner.feed(terminal_text), terminal_text)
        finally:
            try:
                self._exit_code = self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._exit_code = self.process.poll()
            with self._condition:
                self._condition.notify_all()
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            self._master_fd = None
            if self._on_exit is not None:
                try:
                    self._on_exit(self)
                except Exception:
                    pass

    def wait_for_output(self, after_offset: int, wait_ms: int) -> None:
        wait_seconds = max(0, min(wait_ms, MAX_SESSION_WAIT_MS)) / 1000
        deadline = time.monotonic() + wait_seconds
        with self._condition:
            while self.is_running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self._total_chars <= after_offset:
                    self._condition.wait(timeout=remaining)
                    continue

                observed_total = self._total_chars
                settle_time = min(remaining, SESSION_OUTPUT_SETTLE_SECONDS)
                self._condition.wait(timeout=settle_time)
                if self._total_chars == observed_total:
                    break

    def read(
        self,
        *,
        wait_ms: int = DEFAULT_SESSION_WAIT_MS,
        max_chars: int = DEFAULT_SESSION_OUTPUT_CHARS,
        mode: str = "incremental",
        after_offset: int | None = None,
    ) -> CommandSessionRead:
        max_chars = max(1, min(max_chars, MAX_SESSION_OUTPUT_CHARS))
        if mode not in ("incremental", "snapshot"):
            raise CommandSessionError("read_mode must be 'incremental' or 'snapshot'")

        with self._condition:
            if mode == "incremental":
                cursor = self._read_offset if after_offset is None else max(0, after_offset)
                wait_after_offset = cursor
            else:
                cursor = self._total_chars
                wait_after_offset = 0 if self._total_chars == 0 else None
        if wait_after_offset is not None:
            self.wait_for_output(wait_after_offset, wait_ms)

        with self._condition:
            if mode == "snapshot":
                raw_output = self._buffer[-max_chars:]
                lost_chars = max(0, self._total_chars - len(raw_output))
                remaining_chars = 0
                next_offset = self._total_chars
            elif mode == "incremental":
                start_offset = max(cursor, self._buffer_start)
                lost_chars = max(0, self._buffer_start - cursor)
                relative_start = start_offset - self._buffer_start
                raw_output = self._buffer[relative_start:relative_start + max_chars]
                self._read_offset = start_offset + len(raw_output)
                next_offset = self._read_offset
                remaining_chars = max(0, self._total_chars - self._read_offset)
            running = self.is_running
            exit_code = self.exit_code
            total_chars = self._total_chars

        return CommandSessionRead(
            output=raw_output,
            lost_chars=lost_chars,
            remaining_chars=remaining_chars,
            next_offset=next_offset,
            total_chars=total_chars,
            running=running,
            exit_code=exit_code,
        )

    def write_text(self, text: str) -> int:
        if not isinstance(text, str) or not text:
            raise CommandSessionError("input_text must be a non-empty string")
        if len(text) > 65536:
            raise CommandSessionError("input_text cannot exceed 65536 characters")
        return self._write_bytes(text.encode("utf-8"))

    def send_keys(self, keys: list[str]) -> int:
        if not isinstance(keys, list) or not keys:
            raise CommandSessionError("keys must be a non-empty array")
        if len(keys) > 64:
            raise CommandSessionError("At most 64 keys can be sent in one action")
        sequences = []
        for key in keys:
            normalized = str(key).strip().upper().replace("+", "_").replace("-", "_")
            sequence = _FIXED_KEY_SEQUENCES.get(normalized)
            if sequence is None:
                raise CommandSessionError(
                    f"Unsupported key '{key}'. Supported keys: {', '.join(SUPPORTED_KEY_NAMES)}"
                )
            sequences.append(sequence)
        return self._write_bytes("".join(sequences).encode("utf-8"))

    def subscribe_terminal_output(self, listener) -> tuple[int, str]:
        """Subscribe to raw PTY output and return a replayable recent snapshot."""
        if not callable(listener):
            raise CommandSessionError("Terminal output listener must be callable")
        with self._condition:
            listener_id = self._next_terminal_listener_id
            self._next_terminal_listener_id += 1
            self._terminal_listeners[listener_id] = listener
            snapshot = self._terminal_buffer
        return listener_id, snapshot

    def unsubscribe_terminal_output(self, listener_id: int) -> None:
        with self._condition:
            self._terminal_listeners.pop(listener_id, None)

    def resize(self, rows: int, columns: int) -> None:
        """Resize the PTY used by the interactive process."""
        rows = max(1, int(rows))
        columns = max(1, int(columns))
        with self._write_lock:
            master_fd = self._master_fd
            if master_fd is None:
                raise CommandSessionError(f"Session '{self.session_id}' is closed")
            try:
                self._set_window_size(master_fd, rows, columns)
            except OSError as error:
                raise CommandSessionError(str(error)) from error

    def _write_bytes(self, payload: bytes) -> int:
        if not self.is_running:
            raise CommandSessionError(
                f"Session '{self.session_id}' has exited with code {self.exit_code}"
            )
        with self._write_lock:
            master_fd = self._master_fd
            if master_fd is None:
                raise CommandSessionError(f"Session '{self.session_id}' is closed")
            written = 0
            while written < len(payload):
                try:
                    count = os.write(master_fd, payload[written:])
                except BlockingIOError:
                    _, writable, _ = select.select([], [master_fd], [], 1)
                    if not writable:
                        raise CommandSessionError("Timed out while writing to the session")
                    continue
                except OSError as error:
                    raise CommandSessionError(str(error)) from error
                written += count
            return written

    def terminate(self, timeout: float = 2.0) -> None:
        if self.is_running:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.process.terminate()
                except (ProcessLookupError, OSError):
                    pass
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        self.process.kill()
                    except (ProcessLookupError, OSError):
                        pass
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        self._reader_thread.join(timeout=1)
        if self.is_running:
            raise CommandSessionError(f"Failed to terminate session '{self.session_id}'")


class CommandSessionManager:
    """Own and route persistent PTY sessions, isolated by chat owner."""

    def __init__(self):
        self._sessions: dict[str, CommandSession] = {}
        self._lock = threading.Lock()
        atexit.register(self.shutdown_all)

    def _prune_finished_locked(self, owner) -> None:
        finished_sessions = sorted(
            (
                session
                for session in self._sessions.values()
                if session.owner == owner and not session.is_running
            ),
            key=lambda session: session.created_at,
        )
        for old_session in finished_sessions[:-MAX_FINISHED_SESSIONS_PER_OWNER]:
            self._sessions.pop(old_session.session_id, None)

    def _on_session_exit(self, session: CommandSession) -> None:
        with self._lock:
            self._prune_finished_locked(session.owner)

    def start(
        self,
        command: str,
        working_dir: str,
        owner,
        *,
        host_prefix: list[str] | None = None,
    ) -> CommandSession:
        if not isinstance(command, str) or not command.strip():
            raise CommandSessionError("command is required for the start action")
        with self._lock:
            self._prune_finished_locked(owner)

            running_count = sum(
                session.owner == owner and session.is_running
                for session in self._sessions.values()
            )
            if running_count >= MAX_RUNNING_SESSIONS_PER_OWNER:
                raise CommandSessionError(
                    f"At most {MAX_RUNNING_SESSIONS_PER_OWNER} sessions may run per chat"
                )
            session_id = uuid.uuid4().hex[:8]
            while session_id in self._sessions:
                session_id = uuid.uuid4().hex[:8]
            session = CommandSession(
                session_id,
                command,
                working_dir,
                owner,
                host_prefix=host_prefix,
                on_exit=self._on_session_exit,
            )
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str, owner) -> CommandSession:
        if not session_id:
            raise CommandSessionError("session_id is required for this action")
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.owner != owner:
            raise CommandSessionError(f"Session '{session_id}' was not found in this chat")
        return session

    def remove(self, session_id: str, owner) -> CommandSession:
        session = self.get(session_id, owner)
        with self._lock:
            self._sessions.pop(session_id, None)
        return session

    def forget(self, session: CommandSession) -> None:
        """Forget a known session without failing if exit pruning removed it."""
        with self._lock:
            if self._sessions.get(session.session_id) is session:
                self._sessions.pop(session.session_id, None)

    def list(self, owner) -> list[CommandSession]:
        with self._lock:
            return sorted(
                (session for session in self._sessions.values() if session.owner == owner),
                key=lambda session: session.created_at,
            )

    def list_all(self) -> list[CommandSession]:
        """Return currently running sessions across all chat owners."""
        with self._lock:
            return sorted(
                (session for session in self._sessions.values() if session.is_running),
                key=lambda session: session.created_at,
            )

    def shutdown_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.terminate()
            except (CommandSessionError, OSError, subprocess.SubprocessError):
                pass


def format_session_result(
    session: CommandSession,
    read_result: CommandSessionRead | None = None,
    *,
    action: str,
) -> str:
    """Format stable session metadata and optional terminal output for the LLM."""
    running = session.is_running
    succeeded = action == "terminate" or running or session.exit_code in (None, 0)
    parts = [
        f"Status: {'success' if succeeded else 'failure'}",
        f"Action: {action}",
        f"Session ID: {session.session_id}",
        f"Session State: {'running' if running else 'exited'}",
        f"PID: {session.pid}",
        f"Working Directory: {session.working_dir}",
    ]
    if not running:
        parts.append(f"Exit Code: {session.exit_code}")
    if read_result is not None:
        parts.append(f"Total Output Characters: {read_result.total_chars}")
        if read_result.lost_chars:
            parts.append(f"Older Output Characters Not Included: {read_result.lost_chars}")
        if read_result.remaining_chars:
            parts.append(f"Remaining Output Characters: {read_result.remaining_chars}")
        parts.append(f"Next Output Offset: {read_result.next_offset}")
        parts.append("Output:\n" + (read_result.output or "(no new output)"))
    return "\n".join(parts)


_default_manager: CommandSessionManager | None = None
_default_manager_lock = threading.Lock()


def get_command_session_manager() -> CommandSessionManager:
    """Return the process-wide manager shared by integrations and shutdown hooks."""
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = CommandSessionManager()
    return _default_manager


def shutdown_command_sessions() -> None:
    """Terminate all sessions if the terminal interface has been used."""
    if _default_manager is not None:
        _default_manager.shutdown_all()

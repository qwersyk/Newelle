"""Cancellable process runner for local C++ backend builds."""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading


class BuildCancelled(Exception):
    """Raised by a source build when the user stops it."""


class BuildProcess:
    """Run one build command at a time and terminate its whole process tree."""

    def __init__(self, name: str = "build"):
        self.name = name
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._termination_started = False
        self._atexit_registered = False

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def begin(self) -> None:
        """Prepare this runner for a new build."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError(f"{self.name} is already running")
            self._cancel_requested.clear()
            self._termination_started = False

    def run(self, command, *, env=None, cwd=None, on_output=None) -> bool:
        """Run a command, forwarding output line by line to ``on_output``."""
        if self.cancel_requested:
            return False

        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=(os.name != "nt"),
        )

        with self._lock:
            if self._cancel_requested.is_set():
                cancel_before_register = True
            else:
                cancel_before_register = False
                self._process = process

        if cancel_before_register:
            self._terminate_process_group(process)
            try:
                process.stdout.close()
            except (AttributeError, OSError, ValueError):
                pass
            return False

        try:
            assert process.stdout is not None
            for line in process.stdout:
                if on_output is not None:
                    on_output(line)
            process.wait()
            return process.returncode == 0 and not self.cancel_requested
        finally:
            try:
                process.stdout.close()
            except (AttributeError, OSError, ValueError):
                pass
            with self._lock:
                if self._process is process:
                    self._process = None

    def cancel(self) -> None:
        """Request cancellation without blocking the GTK main loop."""
        self._cancel_requested.set()
        with self._lock:
            process = self._process
            if process is None or self._termination_started:
                return
            self._termination_started = True

        threading.Thread(
            target=self._terminate_process_group,
            args=(process,),
            name=f"{self.name}-cancel",
            daemon=True,
        ).start()

    def stop(self) -> None:
        """Stop the active build and wait for it during application exit."""
        self._cancel_requested.set()
        with self._lock:
            process = self._process
            if process is None:
                return
            self._termination_started = True
        self._terminate_process_group(process)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass

        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def register_atexit(self) -> None:
        """Ensure a build cannot outlive the application."""
        if self._atexit_registered:
            return
        self._atexit_registered = True
        atexit.register(self.stop)

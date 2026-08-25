import gettext
import importlib
import os
import re
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from .download_manager import DownloadKind, get_download_manager

_ = gettext.gettext


LOCK_SEMAPHORE = threading.Semaphore(1)
LOCKS = {}
INSTALLING_PACKAGES = []
PIP_INSTALLED = False

_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT = {}
_RAW_PROGRESS_SUPPORTED = None
_RAW_PROGRESS_LOCK = threading.Lock()


@dataclass
class _InstallFlight:
    event: threading.Event = field(default_factory=threading.Event)
    result: subprocess.CompletedProcess | None = None
    handle: object | None = None


class PipProgressParser:
    """Turn pip's stable raw progress lines into task update dictionaries."""

    _progress = re.compile(r"^Progress\s+(\d+)\s+of\s+(\d+)\s*$")
    _download = re.compile(r"^\s*(?:Downloading|Using cached)\s+(\S+)")

    def __init__(self):
        self.current_item = None

    def parse(self, line: str) -> dict | None:
        text = line.strip()
        match = self._progress.match(text)
        if match:
            current, total = (int(match.group(1)), int(match.group(2)))
            return {
                "phase": (
                    f"Downloading {self.current_item}"
                    if self.current_item
                    else _("Downloading package")
                ),
                "fraction": current / total if total > 0 else None,
                "transferred_bytes": current,
                "total_bytes": total if total > 0 else None,
            }

        match = self._download.match(text)
        if match:
            item = match.group(1).split("?", 1)[0].rstrip(".")
            self.current_item = os.path.basename(item) or item
            return {
                "phase": _("Downloading {name}").format(name=self.current_item),
                "reset_progress": True,
            }
        if text.startswith("Collecting "):
            return {
                "phase": _("Resolving {name}").format(
                    name=text.removeprefix("Collecting ").split()[0]
                ),
                "reset_progress": True,
            }
        if text.startswith("Building wheel for "):
            return {"phase": text.rstrip("."), "reset_progress": True}
        if text.startswith("Installing collected packages"):
            return {"phase": _("Installing packages"), "reset_progress": True}
        if text.startswith("Successfully installed"):
            return {"phase": _("Finalizing installation"), "reset_progress": True}
        return None


def is_module_available(module_name: str) -> bool:
    """Check whether a module can be found without importing it."""
    if module_name in sys.modules:
        return True
    try:
        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except (ModuleNotFoundError, ImportError, ValueError):
        return False


def find_module(full_module_name):
    return is_module_available(full_module_name) or None


def runtime_find_module(full_module_name):
    try:
        return importlib.import_module(full_module_name)
    except Exception:  # noqa: BLE001 - importing third-party modules may fail arbitrarily
        return None


def _pip_supports_raw_progress() -> bool:
    global _RAW_PROGRESS_SUPPORTED
    if _RAW_PROGRESS_SUPPORTED is not None:
        return _RAW_PROGRESS_SUPPORTED
    with _RAW_PROGRESS_LOCK:
        if _RAW_PROGRESS_SUPPORTED is not None:
            return _RAW_PROGRESS_SUPPORTED
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15,
                check=False,
            )
            _RAW_PROGRESS_SUPPORTED = (
                result.returncode == 0
                and "--progress-bar" in result.stdout
                and "raw" in result.stdout
            )
        except (OSError, subprocess.SubprocessError):
            _RAW_PROGRESS_SUPPORTED = False
    return _RAW_PROGRESS_SUPPORTED


def _task_update(handle, update):
    if handle is not None and update:
        handle.update(**update)


def _run_pip(command, environment, handle):
    parser = PipProgressParser()
    tail = []
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
        )
    except (OSError, ValueError) as error:
        return subprocess.CompletedProcess(command, 127, stdout=str(error))

    if process.stdout is not None:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            tail.append(line.rstrip())
            if len(tail) > 40:
                tail.pop(0)
            _task_update(handle, parser.parse(line))
        process.stdout.close()
    returncode = process.wait()
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout="\n".join(tail),
    )


def install_module(module, path, update=True, cache_dir=None, task=None):
    """Install requirements into ``path`` while reporting observable progress.

    The return contract remains compatible with the old helper: callers receive
    a ``CompletedProcess`` (or ``None`` for a bootstrap failure) and are not
    forced to handle a new exception type.
    """
    global PIP_INSTALLED

    manager = get_download_manager()
    inherited_handle = task or manager.current_task()
    source_id = f"pip:{os.path.abspath(path)}:{module}"

    flight_key = (os.path.abspath(path), module, bool(update))
    with _INFLIGHT_LOCK:
        flight = _INFLIGHT.get(flight_key)
        leader = flight is None
        if leader:
            handle = inherited_handle
            owns_task = handle is None
            if owns_task:
                handle = manager.create_task(
                    _("Install {name}").format(name=module),
                    kind=DownloadKind.DEPENDENCY,
                    source_id=source_id,
                    phase=_("Waiting to install"),
                    queued=True,
                )
            flight = _InstallFlight(handle=handle)
            _INFLIGHT[flight_key] = flight
            INSTALLING_PACKAGES.append(module)
        else:
            owns_task = False
            handle = inherited_handle or flight.handle

    if not leader:
        if handle is not flight.handle:
            handle.queue(_("Waiting for the same dependency installation"))
        flight.event.wait()
        result = flight.result
        if handle is not flight.handle:
            if result is not None and result.returncode == 0:
                handle.start(_("Using the shared dependency installation"))
            else:
                handle.fail(_("The shared pip installation failed"), _("Failed"))
        return result

    with LOCK_SEMAPHORE:
        path_lock = LOCKS.setdefault(os.path.abspath(path), threading.Semaphore(1))

    acquired = path_lock.acquire(blocking=False)
    if not acquired:
        handle.queue(_("Waiting for another dependency installation"))
        path_lock.acquire()
    handle.start(_("Preparing pip"))

    result = None
    try:
        environment = os.environ.copy()
        environment["TMPDIR"] = cache_dir or os.path.join(os.getcwd(), "tmp")
        os.makedirs(environment["TMPDIR"], exist_ok=True)

        if find_module("pip") is None and not PIP_INSTALLED:
            handle.update(phase=_("Downloading pip"), reset_progress=True)
            bootstrap = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        "wget -q https://bootstrap.pypa.io/get-pip.py -O get-pip.py "
                        "&& python get-pip.py && rm -f get-pip.py"
                    ),
                ],
                cwd=os.path.dirname(path),
                env=environment,
                check=False,
            )
            if bootstrap.returncode != 0:
                PIP_INSTALLED = False
                handle.fail(_("Could not bootstrap pip"), _("Failed"))
                return None
            PIP_INSTALLED = True

        command = [sys.executable, "-m", "pip", "install", "--target", path]
        if update:
            command.append("--upgrade")
        if _pip_supports_raw_progress():
            command.extend(["--progress-bar", "raw"])
        command.extend(shlex.split(module))
        handle.start(_("Resolving dependencies"))
        result = _run_pip(command, environment, handle)
        if result.returncode == 0:
            print(module + " installed")
            if owns_task:
                handle.complete(_("Installed"))
        else:
            detail = (result.stdout or "").strip()
            error = f"pip exited with code {result.returncode}"
            if detail:
                error += f": {detail[-1000:]}"
            handle.fail(error, _("Failed"))
        return result
    except Exception as error:  # noqa: BLE001 - preserve the legacy no-raise contract
        PIP_INSTALLED = False
        handle.fail(str(error), _("Failed"))
        print("Error installing " + module + " " + str(error))
        return None
    finally:
        path_lock.release()
        with _INFLIGHT_LOCK:
            flight.result = result
            flight.event.set()
            _INFLIGHT.pop(flight_key, None)
            try:
                INSTALLING_PACKAGES.remove(module)
            except ValueError:
                pass

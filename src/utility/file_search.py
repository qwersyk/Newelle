"""Fast, dependency-free filesystem search helpers."""

import fnmatch
from functools import cache
import io
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Optional, Pattern

GLOB_RESULT_LIMIT = 1000
GLOB_TIME_LIMIT = 10.0

# Directories that hold generated or vendored content. They are skipped unless
# the pattern names them explicitly.
_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".cache",
        ".tox",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
    }
)


@cache
def _ripgrep_command() -> Optional[tuple[str, ...]]:
    executable = shutil.which("rg")
    if executable is not None:
        return (executable,)

    # The Flatpak has permission to run host commands. This makes an existing
    # host ripgrep installation useful without adding a package dependency to
    # the application or changing behavior when it is unavailable.
    flatpak_spawn = shutil.which("flatpak-spawn")
    if not os.getenv("container") or flatpak_spawn is None:
        return None
    try:
        probe = subprocess.run(
            (flatpak_spawn, "--host", "which", "rg"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0:
        return (flatpak_spawn, "--host", "rg")
    return None


@dataclass
class GlobSearchResult:
    """Outcome of :func:`glob_search`.

    ``matches`` holds absolute paths, newest first. ``directories`` is the
    subset of ``matches`` that are directories.
    """

    matches: list[str] = field(default_factory=list)
    directories: set[str] = field(default_factory=set)
    truncated: bool = False
    timed_out: bool = False
    cancelled: bool = False

    @property
    def incomplete(self) -> bool:
        return self.truncated or self.timed_out or self.cancelled


_MAGIC = re.compile(r"[*?\[{]")


def _expand_braces(pattern: str) -> list[str]:
    """Expand ``{a,b}`` groups (including nested ones) into plain patterns."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    before, after = pattern[: match.start()], pattern[match.end() :]
    expanded = []
    for part in match.group(1).split(","):
        for candidate in _expand_braces(before + part + after):
            if candidate not in expanded:
                expanded.append(candidate)
    return expanded


def _group_patterns(search_dir: str, pattern: str) -> dict[str, list[str]]:
    """Map each search root to the relative patterns that apply to it.

    Patterns without a ``/`` match at any depth. Absolute patterns are split
    into their literal directory prefix (the root) and the remaining pattern.
    """
    groups: dict[str, list[str]] = {}
    for expanded in _expand_braces(pattern.strip()):
        expanded = expanded.strip()
        if expanded.startswith("~"):
            expanded = os.path.expanduser(expanded)

        root = search_dir
        anchored = "/" in expanded
        if os.path.isabs(expanded):
            parts = expanded.split("/")
            literal = []
            index = 0
            while index < len(parts) and not _MAGIC.search(parts[index]):
                literal.append(parts[index])
                index += 1
            base = "/".join(literal) or "/"
            remainder = "/".join(parts[index:])
            if not remainder:
                base, remainder = os.path.dirname(base) or "/", os.path.basename(base)
            root, expanded, anchored = base, remainder, True

        while expanded.startswith("./"):
            expanded = expanded[2:]
        expanded = expanded.lstrip("/")
        if not expanded or expanded == ".":
            continue
        if not anchored:
            expanded = "**/" + expanded

        patterns = groups.setdefault(root, [])
        if expanded not in patterns:
            patterns.append(expanded)
    return groups


def _segment_regex(segment: str) -> str:
    out = []
    index = 0
    length = len(segment)
    while index < length:
        char = segment[index]
        if char == "*":
            while index + 1 < length and segment[index + 1] == "*":
                index += 1
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = index + 1
            if end < length and segment[end] in "!^":
                end += 1
            if end < length and segment[end] == "]":
                end += 1
            while end < length and segment[end] != "]":
                end += 1
            if end >= length:
                out.append(re.escape(char))
            else:
                body = segment[index + 1 : end]
                negate = body.startswith(("!", "^"))
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\").replace("[", "\\[")
                out.append(f"[{'^' if negate else ''}{body}]")
                index = end
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


def _compile_glob(pattern: str) -> Pattern[str]:
    """Compile a glob (``*``, ``**``, ``?``, ``[...]``) into a path regex."""
    parts = [part for part in pattern.split("/") if part]
    regex = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            regex.append("[^/].*" if last else "(?:[^/]+/)*")
        else:
            regex.append(_segment_regex(part))
            if not last:
                regex.append("/")
    if pattern.endswith("/"):
        regex.append("/")
    try:
        return re.compile("".join(regex) + r"\Z", re.DOTALL)
    except re.error as error:
        raise ValueError(f"Invalid glob pattern '{pattern}': {error}") from error


def _list_files_ripgrep(
    root: str,
    include_hidden: bool,
    skipped: frozenset[str],
    cancel: Optional[threading.Event],
    deadline: Optional[float],
    consume: Callable[[str], bool],
) -> Optional[bool]:
    """Stream the files below *root* through ripgrep (honouring .gitignore).

    Returns ``None`` when ripgrep cannot be used, ``False`` when the search
    was stopped early and ``True`` when every file was listed.
    """
    command_prefix = _ripgrep_command()
    if command_prefix is None:
        return None

    command = [
        *command_prefix,
        "--files",
        "--null",
        "--color=never",
        "--no-messages",
        "--no-require-git",
    ]
    if include_hidden:
        command.append("--hidden")
    command.extend(f"--glob=!{name}" for name in sorted(skipped))
    command.extend(("--", root))

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None

    done = threading.Event()
    killed = threading.Event()

    def watchdog():
        while not done.is_set():
            if (cancel is not None and cancel.is_set()) or (
                deadline is not None and time.monotonic() >= deadline
            ):
                killed.set()
                process.kill()
                return
            done.wait(0.1)

    threading.Thread(target=watchdog, daemon=True).start()

    stopped = False
    buffer = b""
    try:
        assert process.stdout is not None
        while not stopped:
            chunk = process.stdout.read1(65536)
            if not chunk:
                break
            buffer += chunk
            *records, buffer = buffer.split(b"\0")
            for record in records:
                if not record:
                    continue
                relative = os.path.relpath(os.fsdecode(record), root)
                if not consume(relative):
                    stopped = True
                    break
    finally:
        done.set()
        if stopped:
            process.kill()
        if process.stdout is not None:
            process.stdout.close()
        process.wait()

    if stopped or killed.is_set():
        return False
    if process.returncode not in (0, 1):
        return None
    return True


def _walk_python(
    root: str,
    include_hidden: bool,
    skipped: frozenset[str],
    should_stop: Callable[[], bool],
    consider: Callable[[str, bool], bool],
) -> bool:
    """Depth-first ``scandir`` walk used when ripgrep is unavailable."""
    visited = set()
    try:
        root_stat = os.stat(root)
        visited.add((root_stat.st_dev, root_stat.st_ino))
    except OSError:
        return True

    pending = [(root, "")]
    while pending:
        if should_stop():
            return False
        directory, relative_dir = pending.pop()
        children = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = entry.name
                    if name in skipped or (name.startswith(".") and not include_hidden):
                        continue
                    relative = f"{relative_dir}/{name}" if relative_dir else name
                    try:
                        is_directory = entry.is_dir(follow_symlinks=True)
                    except OSError:
                        is_directory = False
                    if not consider(relative, is_directory):
                        return False
                    if not is_directory:
                        continue
                    try:
                        info = entry.stat()
                    except OSError:
                        continue
                    key = (info.st_dev, info.st_ino)
                    if key in visited:
                        continue
                    visited.add(key)
                    children.append((entry.path, relative))
        except OSError:
            continue
        pending.extend(reversed(children))
    return True


def _modified_time(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def glob_search(
    search_dir: str,
    pattern: str,
    limit: int = GLOB_RESULT_LIMIT,
    timeout: Optional[float] = GLOB_TIME_LIMIT,
    cancel: Optional[threading.Event] = None,
) -> GlobSearchResult:
    """Find files and directories matching *pattern* below *search_dir*.

    Patterns without a ``/`` match at any depth, ``{a,b}`` braces are
    expanded, and hidden entries are skipped unless the pattern mentions one.
    Ripgrep is used when available so ``.gitignore`` is honoured; otherwise a
    plain walk skips well-known generated directories. The search stops at
    *limit* results, after *timeout* seconds, or when *cancel* is set, and
    returns what it found so far.
    """
    search_dir = os.path.abspath(os.path.expanduser(search_dir))
    if not os.path.isdir(search_dir):
        raise FileNotFoundError(f"Directory does not exist: {search_dir}")
    if not pattern or not pattern.strip():
        raise ValueError("Pattern cannot be empty")

    result = GlobSearchResult()
    groups = _group_patterns(search_dir, pattern)
    deadline = time.monotonic() + timeout if timeout else None
    found: dict[str, bool] = {}

    def should_stop() -> bool:
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            return True
        if deadline is not None and time.monotonic() >= deadline:
            result.timed_out = True
            return True
        return False

    def search_root(root: str, patterns: list[str]) -> bool:
        regexes = [_compile_glob(item) for item in patterns]
        segments = {part for item in patterns for part in item.split("/")}
        include_hidden = any(
            part.startswith(".") and part not in (".", "..") for part in segments
        )
        skipped = _IGNORED_DIRECTORIES - segments
        seen_directories: set[str] = set()

        def consider(relative: str, is_directory: bool) -> bool:
            if not any(
                regex.match(relative) or (is_directory and regex.match(relative + "/"))
                for regex in regexes
            ):
                return True
            path = os.path.join(root, relative)
            if path in found:
                return True
            if len(found) >= limit:
                result.truncated = True
                return False
            found[path] = is_directory
            return True

        def consume_file(relative: str) -> bool:
            chain = []
            parent = os.path.dirname(relative)
            while parent and parent not in seen_directories:
                seen_directories.add(parent)
                chain.append(parent)
                parent = os.path.dirname(parent)
            for directory in reversed(chain):
                if not consider(directory, True):
                    return False
            return consider(relative, False)

        completed = _list_files_ripgrep(
            root, include_hidden, skipped, cancel, deadline, consume_file
        )
        if completed is None:
            completed = _walk_python(
                root, include_hidden, skipped, should_stop, consider
            )
        elif not completed:
            should_stop()
        return completed

    for root, patterns in groups.items():
        if not search_root(root, patterns):
            break

    result.matches = sorted(found, key=lambda path: (-_modified_time(path), path))
    result.directories = {path for path, is_directory in found.items() if is_directory}
    return result


def _iter_searchable_files(
    search_path: str, glob_patterns: tuple[str, ...]
) -> Iterator[str]:
    """Yield files without allocating the lists produced by ``os.walk``."""
    pending = [search_path]

    while pending:
        directory = pending.pop()
        child_directories = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not entry.name.startswith("."):
                                child_directories.append(entry.path)
                        elif entry.is_file(follow_symlinks=True) and (
                            not glob_patterns
                            or any(
                                fnmatch.fnmatch(entry.name, pattern)
                                for pattern in glob_patterns
                            )
                        ):
                            yield entry.path
                    except OSError:
                        continue
        except OSError:
            continue

        # Match os.walk's depth-first order while retaining scandir's native
        # directory order.
        pending.extend(reversed(child_directories))


def _search_file(
    file_path: str,
    regex: Pattern[str],
    glob_patterns: tuple[str, ...],
    limit: Optional[int],
) -> list[tuple[str, int, str]]:
    if glob_patterns and not any(
        fnmatch.fnmatch(os.path.basename(file_path), pattern)
        for pattern in glob_patterns
    ):
        return []

    matches = []
    try:
        # Reading and decoding ordinary files in bulk moves the hot path into
        # optimized C routines and requires only one open/read pass per file.
        with open(file_path, "rb") as file:
            content = file.read(16 * 1024 * 1024 + 1)
            if b"\0" in content[:8192]:
                return []

            if len(content) <= 16 * 1024 * 1024:
                text = content.decode("utf-8", errors="replace")
                if "\r" in text:
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                lines = text.splitlines(keepends=True)
            else:
                # Keep memory bounded for unusually large files.
                file.seek(0)
                lines = io.TextIOWrapper(
                    file, encoding="utf-8", errors="replace"
                )

            for line_number, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append((file_path, line_number, line))
                    if limit and len(matches) >= limit:
                        break
    except (OSError, UnicodeError):
        return []
    return matches


def _grep_with_ripgrep(
    search_path: str,
    regex: Pattern[str],
    glob_patterns: tuple[str, ...],
    limit: Optional[int],
) -> Optional[list[tuple[str, int, str]]]:
    """Use ripgrep when available, returning ``None`` when it cannot be used."""
    command_prefix = _ripgrep_command()
    if (
        command_prefix is None
        or not isinstance(regex.pattern, str)
        or r"\A" in regex.pattern
        or (os.path.isfile(search_path) and os.path.islink(search_path))
    ):
        return None

    command = [
        *command_prefix,
        "--null",
        "--line-number",
        "--with-filename",
        "--no-heading",
        "--color=never",
        "--no-messages",
        "--no-ignore",
        "--hidden",
        "--crlf",
    ]
    for pattern in glob_patterns:
        command.append(f"--glob={pattern}")
    # Exclusions come last because ripgrep resolves overlapping globs using
    # the later rule. Hidden files are retained, matching the Python fallback;
    # only hidden directories are pruned.
    if os.path.isdir(search_path):
        command.extend(("--glob=!.*/*", "--glob=!**/.*/**"))
    command.extend(("-e", regex.pattern, "--", search_path))

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None

    matches = []
    path_fragments = []
    stopped_at_limit = False
    try:
        assert process.stdout is not None
        for record in process.stdout:
            # --null terminates the path independently of any newlines that a
            # valid Unix filename may contain.
            if b"\0" not in record:
                path_fragments.append(record)
                continue
            raw_path, separator, remainder = record.partition(b"\0")
            if not separator:
                continue
            if path_fragments:
                raw_path = b"".join(path_fragments) + raw_path
                path_fragments.clear()
            raw_line_number, separator, raw_content = remainder.partition(b":")
            if not separator:
                continue
            try:
                line_number = int(raw_line_number)
            except ValueError:
                continue
            content = raw_content.decode("utf-8", errors="replace")
            if "\r" in content:
                content = content.replace("\r\n", "\n").replace("\r", "\n")
            matches.append((os.fsdecode(raw_path), line_number, content))
            if limit and len(matches) >= limit:
                stopped_at_limit = True
                process.terminate()
                break
    finally:
        if process.stdout is not None:
            process.stdout.close()
        process.wait()

    # Exit 2 includes regex features unsupported by ripgrep. Preserve the
    # tool's Python-regex compatibility by transparently using the fallback.
    if not stopped_at_limit and process.returncode not in (0, 1):
        return None
    return matches


def grep_files(
    search_path: str,
    regex: Pattern[str],
    glob_patterns: list[str],
    limit: Optional[int] = None,
    use_ripgrep: bool = True,
) -> list[tuple[str, int, str]]:
    """Search text files efficiently while preserving traversal ordering.

    Search work is streamed one file at a time and stops as soon as the result
    limit is reached, without queueing the entire tree in memory.
    """
    if limit is not None:
        limit = int(limit)
        if limit < 0:
            limit = 1
        elif limit == 0:
            limit = None
    patterns = tuple(glob_patterns)

    if use_ripgrep:
        ripgrep_matches = _grep_with_ripgrep(
            search_path, regex, patterns, limit
        )
        if ripgrep_matches is not None:
            return ripgrep_matches

    if os.path.isfile(search_path):
        return _search_file(search_path, regex, patterns, limit)

    matches = []
    for file_path in _iter_searchable_files(search_path, patterns):
        file_matches = _search_file(file_path, regex, (), limit)
        if limit:
            matches.extend(file_matches[: max(0, limit - len(matches))])
            if len(matches) >= limit:
                break
        else:
            matches.extend(file_matches)

    return matches

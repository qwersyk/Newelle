"""GitHub marketplace for Newelle extensions.

The catalog deliberately treats extension files as untrusted data. Python files
are parsed with :mod:`ast` for the small amount of validation needed by the UI;
they are never imported, compiled, or executed while browsing the marketplace.
"""

from __future__ import annotations

import ast
import base64
import gettext
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import zipfile
from collections import OrderedDict
from html import escape as escape_html
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import gi
import requests

gi.require_version("WebKit", "6.0")
from gi.repository import Adw, GLib, Gtk, WebKit

from ..utility.system import open_website
from ..utility.download_manager import (
    DownloadCancelled,
    DownloadKind,
    get_download_manager,
)

_ = gettext.gettext

GITHUB_API = "https://api.github.com"
GITHUB_TOPIC = "newelle-extension"
DEFAULT_PAGE_SIZE = 18
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_README_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_REPOSITORY_FILES = 250
MAX_PYTHON_FILES = 100
MAX_INSTALL_BYTES = 12 * 1024 * 1024
SEARCH_DEBOUNCE_MS = 450
SEARCH_CACHE_SIZE = 16
GITHUB_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)
REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Newelle-Extension-Marketplace/1.0 (+https://github.com/qwersyk/Newelle)",
}
README_EXTRACTOR_SCRIPT = r"""
(function () {
  const marker = "data-newelle-readme";
  const selectors = [
    '[data-testid="readme"] .markdown-body',
    'article.markdown-body',
    '.markdown-body'
  ];

  function findReadme() {
    for (const selector of selectors) {
      const candidates = document.querySelectorAll(selector);
      for (const candidate of candidates) {
        if (candidate.textContent && candidate.textContent.trim().length > 0) {
          return candidate;
        }
      }
    }
    return null;
  }

  function showOnlyReadme() {
    if (document.documentElement.hasAttribute(marker)) {
      return true;
    }
    const readme = findReadme();
    if (!readme || !document.body) {
      return false;
    }

    const wrapper = document.createElement("main");
    wrapper.className = "newelle-readme-shell";
    wrapper.appendChild(readme.cloneNode(true));
    document.body.replaceChildren(wrapper);
    document.body.className = "";
    document.body.style.margin = "0";
    document.documentElement.setAttribute(marker, "true");

    const style = document.createElement("style");
    style.textContent = `
      html, body { min-height: 100%; }
      body { background: Canvas; color: CanvasText; }
      .newelle-readme-shell {
        box-sizing: border-box;
        max-width: 980px;
        margin: 0 auto;
        padding: 24px 28px 40px;
      }
      .newelle-readme-shell .markdown-body { max-width: none; }
      .newelle-readme-shell img { max-width: 100%; height: auto; }
      .newelle-readme-shell pre { overflow-x: auto; }
      .newelle-readme-shell table { display: block; overflow-x: auto; }
    `;
    document.head.appendChild(style);
    return true;
  }

  if (showOnlyReadme()) {
    return;
  }
  const observer = new MutationObserver(function () {
    if (showOnlyReadme()) {
      observer.disconnect();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  [250, 750, 1500, 3000].forEach(function (delay) {
    setTimeout(function () {
      if (showOnlyReadme()) {
        observer.disconnect();
      }
    }, delay);
  });
})();
"""


class ExtensionCatalogError(ValueError):
    """Raised when GitHub returns data that cannot be safely consumed."""


def _required_string(value, label, max_length=4096):
    if not isinstance(value, str) or not value.strip():
        raise ExtensionCatalogError(f"{label} must be a non-empty string")
    if len(value) > max_length or "\x00" in value:
        raise ExtensionCatalogError(f"{label} is invalid")
    return value


def _https_url(value, label, allowed_hosts=None):
    value = _required_string(value, label)
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ExtensionCatalogError(f"{label} must be a valid HTTPS URL")
    if allowed_hosts is not None and (parsed.hostname or "").lower() not in allowed_hosts:
        raise ExtensionCatalogError(f"{label} uses an unsupported host")
    return value


def _github_headers():
    headers = dict(REQUEST_HEADERS)
    token = os.environ.get("NEWELLE_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_response(response, max_bytes=MAX_RESPONSE_BYTES, task=None, phase=None):
    chunks = []
    size = 0
    total = int(response.headers.get("content-length", 0))
    if task is not None:
        task.update(
            phase=phase or _("Downloading extension files"),
            reset_progress=True,
            cancellable=True,
        )
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        if task is not None:
            task.check_cancelled()
        size += len(chunk)
        if size > max_bytes:
            raise ExtensionCatalogError("The GitHub response is too large")
        chunks.append(chunk)
        if task is not None:
            task.update(
                fraction=size / total if total > 0 else None,
                transferred_bytes=size,
                total_bytes=total if total > 0 else None,
            )
    return b"".join(chunks)


def _github_json(url, session=requests, allow_not_found=False):
    with session.get(
        url,
        headers=_github_headers(),
        timeout=(5, 25),
        stream=True,
    ) as response:
        if response.status_code == 404 and allow_not_found:
            return None
        payload = _read_response(response)
        if response.status_code >= 400:
            message = None
            try:
                decoded = payload.decode("utf-8")
                message_payload = json.loads(decoded)
                if isinstance(message_payload, dict):
                    message = message_payload.get("message")
            except (UnicodeDecodeError, ValueError):
                pass
            raise ExtensionCatalogError(
                message or f"GitHub request failed ({response.status_code})"
            )
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExtensionCatalogError("GitHub returned invalid JSON") from exc


def _github_bytes(url, session=requests, max_bytes=MAX_FILE_BYTES, task=None):
    with session.get(
        url,
        headers={**_github_headers(), "Accept": "application/octet-stream"},
        timeout=(5, 30),
        stream=True,
    ) as response:
        response.raise_for_status()
        return _read_response(
            response,
            max_bytes=max_bytes,
            task=task,
            phase=_("Downloading extension payload"),
        )


def _repo_identity(full_name):
    full_name = _required_string(full_name, "repository name", 300).removesuffix(".git")
    parts = full_name.split("/")
    if len(parts) != 2 or any(
        not GITHUB_SEGMENT_RE.fullmatch(part) or part in {".", ".."}
        for part in parts
    ):
        raise ExtensionCatalogError("GitHub returned an invalid repository name")
    return parts[0], parts[1]


def _safe_relative_path(path):
    if not isinstance(path, str) or not path or len(path) > 500 or "\x00" in path:
        raise ExtensionCatalogError("GitHub returned an invalid file path")
    relative = PurePosixPath(path)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ExtensionCatalogError("GitHub returned an unsafe file path")
    return relative


def _is_extension_module(module_name):
    return module_name.lstrip(".").split(".")[-1:] == ["extensions"]


def inspect_python_source(content):
    """Statically check whether *content* defines a Newelle extension."""
    if not isinstance(content, (bytes, bytearray)):
        return {"is_python": False, "is_extension": False, "error": _("Invalid file data")}
    if len(content) > MAX_FILE_BYTES:
        return {
            "is_python": True,
            "is_extension": False,
            "error": _("Python file is larger than 2 MB"),
        }
    try:
        source = bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError:
        return {
            "is_python": True,
            "is_extension": False,
            "error": _("Python file is not valid UTF-8"),
        }
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "is_python": True,
            "is_extension": False,
            "error": _("Python syntax error on line {}" ).format(exc.lineno or "?"),
        }

    imported_bases = set()
    module_aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            for imported in node.names:
                local_name = imported.asname or imported.name
                if imported.name == "NewelleExtension" and _is_extension_module(module):
                    imported_bases.add(local_name)
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if _is_extension_module(imported.name):
                    module_aliases[imported.asname or imported.name.split(".")[-1]] = imported.name

    class_names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name == "NewelleExtension":
            continue
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in imported_bases:
                class_names.append(node.name)
                break
            if (
                isinstance(base, ast.Attribute)
                and base.attr == "NewelleExtension"
                and isinstance(base.value, ast.Name)
                and base.value.id in module_aliases
            ):
                class_names.append(node.name)
                break

    if not class_names:
        return {
            "is_python": True,
            "is_extension": False,
            "error": _("No class inheriting NewelleExtension was found"),
        }
    return {
        "is_python": True,
        "is_extension": True,
        "class_names": class_names,
        "error": _("Newelle extension"),
    }


def _file_record(path, content=None):
    relative = _safe_relative_path(path)
    is_python = relative.suffix.lower() == ".py"
    record = {
        "path": str(relative),
        "is_python": is_python,
        "is_extension": False,
        "error": _("Not a Python file") if not is_python else _("Not checked"),
        "content": None,
    }
    if is_python and content is not None:
        check = inspect_python_source(content)
        record.update(check)
        if check.get("is_extension"):
            record["content"] = bytes(content)
    return record


def _archive_members(asset_name, content):
    """Return safe regular-file members from a release archive."""
    members = []
    if asset_name.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                path = _safe_relative_path(info.filename)
                if info.file_size > MAX_FILE_BYTES:
                    continue
                members.append((str(path), archive.read(info)))
        return members

    if asset_name.lower().endswith(ARCHIVE_SUFFIXES[1:]) or tarfile.is_tarfile(io.BytesIO(content)):
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            for info in archive.getmembers():
                if not info.isfile() or info.size > MAX_FILE_BYTES:
                    continue
                path = _safe_relative_path(info.name)
                member = archive.extractfile(info)
                if member is not None:
                    members.append((str(path), member.read(MAX_FILE_BYTES + 1)))
        return members
    return []


def _release_files(release, session, task=None):
    if not isinstance(release, dict) or release.get("draft") is True:
        return None
    assets = release.get("assets")
    candidates = []
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            candidates.append((asset.get("name"), asset.get("browser_download_url"), asset.get("size", 0)))
    # GitHub also exposes generated source archives for every release. They are
    # useful when a project publishes no custom release asset at all.
    candidates.extend(
        [
            ("source.zip", release.get("zipball_url"), 0),
            ("source.tar.gz", release.get("tarball_url"), 0),
        ]
    )
    for name, url, declared_size in candidates:
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if not name.lower().endswith(".py") and not name.lower().endswith(ARCHIVE_SUFFIXES):
            continue
        if not _https_url(
            url,
            "release asset URL",
            {"api.github.com", "github.com", "objects.githubusercontent.com"},
        ):
            continue
        if not isinstance(declared_size, int) or declared_size < 0 or declared_size > MAX_ARCHIVE_BYTES:
            continue
        try:
            content = _github_bytes(url, session, MAX_ARCHIVE_BYTES, task=task)
        except (requests.RequestException, ExtensionCatalogError):
            continue
        if name.lower().endswith(".py"):
            members = [(name, content)]
        else:
            try:
                members = _archive_members(name, content)
            except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, ExtensionCatalogError):
                continue
        if not any(path.lower().endswith(".py") for path, _data in members):
            continue
        files = [_file_record(path, data) for path, data in members[:MAX_REPOSITORY_FILES]]
        if any(item["is_python"] for item in files):
            version = release.get("tag_name") or release.get("name") or _("latest release")
            return files, _("Release {} · {}" ).format(version, name), release.get("html_url")
    return None


def _tree_files(owner, repository, branch, session, task=None):
    tree_url = (
        f"{GITHUB_API}/repos/{quote(owner)}/{quote(repository)}/git/trees/"
        f"{quote(branch, safe='')}?recursive=1"
    )
    payload = _github_json(tree_url, session)
    entries = payload.get("tree") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ExtensionCatalogError("GitHub returned an invalid repository tree")
    if len(entries) > MAX_REPOSITORY_FILES * 4:
        raise ExtensionCatalogError("The repository contains too many files to inspect")

    file_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        path = _safe_relative_path(entry.get("path"))
        file_entries.append((path, entry))
    file_entries = file_entries[:MAX_REPOSITORY_FILES]
    python_entries = [
        (path, entry) for path, entry in file_entries if path.suffix.lower() == ".py"
    ]
    if len(python_entries) > MAX_PYTHON_FILES:
        raise ExtensionCatalogError("The repository contains too many Python files to inspect")

    files = []
    for path, entry in file_entries:
        if path.suffix.lower() != ".py":
            files.append(_file_record(str(path)))
            continue
        # The tree API's `url` points to /git/blobs/{sha}. GitHub rejects the
        # octet-stream request used here for that endpoint with HTTP 415. Raw
        # content is also the right source for validating exactly this branch.
        download_url = (
            f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repository)}/"
            f"{quote(branch, safe='')}/{quote(str(path), safe='/')}"
        )
        _https_url(download_url, "source file URL", {"api.github.com", "raw.githubusercontent.com"})
        size = entry.get("size", 0)
        if not isinstance(size, int) or size < 0 or size > MAX_FILE_BYTES:
            files.append(
                {
                    "path": str(path),
                    "is_python": True,
                    "is_extension": False,
                    "error": _("Python file is larger than 2 MB"),
                    "content": None,
                }
            )
            continue
        content = _github_bytes(download_url, session, MAX_FILE_BYTES, task=task)
        files.append(_file_record(str(path), content))
    return files


def _repository_archive_files(owner, repository, branch, session, task=None):
    """Read a branch archive without spending another GitHub API request."""
    archive_url = (
        f"https://github.com/{quote(owner)}/{quote(repository)}/archive/refs/heads/"
        f"{quote(branch, safe='')}.zip"
    )
    _https_url(archive_url, "repository archive URL", {"github.com", "www.github.com"})
    content = _github_bytes(archive_url, session, MAX_ARCHIVE_BYTES, task=task)
    members = _archive_members("source.zip", content)
    if not members:
        raise ExtensionCatalogError("The repository archive contained no readable files")
    files = [
        _file_record(path, data)
        for path, data in members[:MAX_REPOSITORY_FILES]
    ]
    if sum(1 for item in files if item["is_python"]) > MAX_PYTHON_FILES:
        raise ExtensionCatalogError("The repository contains too many Python files to inspect")
    return files


def _readme(owner, repository, session):
    payload = _github_json(
        f"{GITHUB_API}/repos/{quote(owner)}/{quote(repository)}/readme",
        session,
        allow_not_found=True,
    )
    if not isinstance(payload, dict):
        return _("No README.md was found in this repository."), None
    readme_url = payload.get("html_url")
    if isinstance(readme_url, str):
        try:
            readme_url = _https_url(
                readme_url,
                "README URL",
                {"github.com", "www.github.com"},
            )
        except ExtensionCatalogError:
            readme_url = None
    content = payload.get("content")
    if isinstance(content, str) and payload.get("encoding") == "base64":
        try:
            raw = base64.b64decode(content, validate=False)
            if len(raw) <= MAX_README_BYTES:
                return raw.decode("utf-8", errors="replace"), readme_url
        except (ValueError, UnicodeDecodeError):
            pass
    download_url = payload.get("download_url")
    if isinstance(download_url, str):
        try:
            raw = _github_bytes(download_url, session, MAX_README_BYTES)
            return raw.decode("utf-8", errors="replace"), readme_url
        except (requests.RequestException, ExtensionCatalogError):
            pass
    return _("The README could not be rendered."), readme_url


def search_extensions(query="", page=1, sort_by="stars", per_page=DEFAULT_PAGE_SIZE, session=requests):
    """Search public repositories carrying the Newelle extension topic."""
    if not isinstance(page, int) or page < 1:
        raise ExtensionCatalogError("page is invalid")
    if sort_by not in {"stars", "updated"}:
        raise ExtensionCatalogError("sort order is invalid")
    query = (query or "").strip()
    if len(query) > 120:
        raise ExtensionCatalogError("search query is too long")
    github_query = f"{query} topic:{GITHUB_TOPIC}" if query else f"topic:{GITHUB_TOPIC}"
    url = (
        f"{GITHUB_API}/search/repositories?q={quote(github_query)}"
        f"&sort={quote(sort_by)}&order=desc&page={page}&per_page={per_page}"
    )
    payload = _github_json(url, session)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ExtensionCatalogError("GitHub returned an invalid repository list")
    repositories = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        full_name = item.get("full_name")
        if not isinstance(full_name, str):
            continue
        _repo_identity(full_name)
        repositories.append(
            {
                "full_name": full_name,
                "name": _required_string(item.get("name"), "repository name", 200),
                "owner": _required_string(
                    (item.get("owner") or {}).get("login"), "repository owner", 200
                ),
                "description": (item.get("description") or _("No description provided."))[:1000],
                "stars": max(0, int(item.get("stargazers_count", 0) or 0)),
                "forks": max(0, int(item.get("forks_count", 0) or 0)),
                "updated_at": item.get("updated_at") or "",
                "archived": item.get("archived") is True,
                "default_branch": _required_string(
                    item.get("default_branch") or "main", "default branch", 250
                ),
                "html_url": _https_url(
                    item.get("html_url"), "repository URL", {"github.com", "www.github.com"}
                ),
            }
        )
    return {
        "repositories": repositories,
        "total": int(payload.get("total_count", len(repositories)) or 0),
        "incomplete": payload.get("incomplete_results") is True,
    }


def load_extension_repository(repository, session=requests, task=None):
    """Load README and statically validated files for one repository."""
    owner, name = _repo_identity(repository["full_name"])
    branch = _required_string(
        repository.get("default_branch") or "main",
        "default branch",
        250,
    )
    try:
        releases = _github_json(
            f"{GITHUB_API}/repos/{quote(owner)}/{quote(name)}/releases?per_page=10",
            session,
            allow_not_found=True,
        )
    except (ExtensionCatalogError, requests.RequestException):
        # Public repositories can still be inspected from their public branch
        # archive when the unauthenticated API quota is exhausted.
        releases = None
    release_result = None
    if isinstance(releases, list):
        for release in releases:
            release_result = _release_files(release, session, task=task)
            if release_result is not None:
                break

    if release_result is not None:
        files, source_label, release_url = release_result
        source_kind = "release"
    else:
        try:
            files = _repository_archive_files(owner, name, branch, session, task=task)
            source_label = _("Repository archive · {}" ).format(branch)
        except (ExtensionCatalogError, requests.RequestException):
            # Keep the API tree as a fallback for unusually large or disabled
            # branch archives.
            files = _tree_files(owner, name, branch, session, task=task)
            source_label = _("Repository default branch · {}" ).format(branch)
        release_url = None
        source_kind = "repository"

    readme = _("README rendered from the repository on GitHub.")
    readme_url = repository.get("html_url")
    if not isinstance(readme_url, str):
        readme_url = f"https://github.com/{quote(owner)}/{quote(name)}"
    readme_url = _https_url(
        readme_url,
        "repository URL",
        {"github.com", "www.github.com"},
    )
    valid_count = sum(1 for item in files if item.get("is_extension"))
    if valid_count == 0:
        source_label += " · " + _("no valid extension files found")
    return {
        **repository,
        "readme": readme,
        "readme_url": readme_url,
        "files": files,
        "source_kind": source_kind,
        "source_label": source_label,
        "release_url": release_url,
        "valid_count": valid_count,
    }


def _output_filename(path):
    parts = [re.sub(r"[^A-Za-z0-9_.-]", "_", part) for part in PurePosixPath(path).parts]
    filename = "__".join(parts)
    if len(filename) > 180:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:10]
        filename = f"extension_{digest}_{parts[-1]}"
    return filename if filename.endswith(".py") else f"{filename}.py"


def install_extension_files(files, extension_dir):
    """Write selected, already validated extension bytes without importing them."""
    os.makedirs(extension_dir, exist_ok=True)
    selected = [item for item in files if item.get("is_python") and item.get("is_extension")]
    total_bytes = sum(len(item.get("content") or b"") for item in selected)
    if not selected or total_bytes > MAX_INSTALL_BYTES:
        raise ExtensionCatalogError("The selected extension files exceed the install limit")
    installed = []
    for item in selected:
        content = item.get("content")
        if not isinstance(content, bytes):
            raise ExtensionCatalogError("A selected file is no longer available")
        filename = _output_filename(item["path"])
        destination = os.path.join(extension_dir, filename)
        stem, suffix = os.path.splitext(filename)
        counter = 2
        while os.path.exists(destination):
            destination = os.path.join(extension_dir, f"{stem}_{counter}{suffix}")
            counter += 1
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".py", prefix="newelle-extension-", delete=False
            ) as temporary:
                temporary.write(content)
                temporary_path = temporary.name
            shutil.copyfile(temporary_path, destination)
            installed.append(destination)
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
    return installed


def _format_stars(stars):
    if stars >= 1_000_000:
        return f"{stars / 1_000_000:.1f}M"
    if stars >= 1_000:
        return f"{stars / 1_000:.1f}k"
    return str(stars)


def _format_updated(value):
    if not value:
        return _("recently")
    return value[:10]


class ExtensionMarketplaceView(Gtk.Box):
    """Discover, review, and install files from the Newelle GitHub topic."""

    def __init__(self, parent, controller, install_callback=None, on_installed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.parent_window = parent
        self.controller = controller
        self.install_callback = install_callback
        self.on_installed = on_installed
        self.cache = OrderedDict()
        self.current_query = ""
        self.current_page = 0
        self.current_sort = "stars"
        self.has_next = False
        self.loading = False
        self.closed = False
        self.request_generation = 0
        self.debounce_source = None
        self.result_cards = []
        self.selected_repository = None
        self.detail_page = None
        self.detail_files = []
        self.file_checks = []
        self.install_button = None
        self.install_spinner = None

        self.connect("unrealize", self._on_unrealize)
        self.toast_overlay = Adw.ToastOverlay()
        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=200,
            vhomogeneous=False,
        )
        self.toast_overlay.set_child(self.stack)
        self.append(self.toast_overlay)
        self._build_search_page()
        GLib.idle_add(self._load_default_feed)

    def _build_status(self, icon_name, title, description, spinner=False):
        status = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=14,
            margin_top=24,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
            valign=Gtk.Align.CENTER,
        )
        if spinner:
            indicator = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        else:
            indicator = Gtk.Image(icon_name=icon_name, pixel_size=32, valign=Gtk.Align.CENTER)
            indicator.add_css_class("dim-label")
        status.append(indicator)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        heading = Gtk.Label(label=title, xalign=0, wrap=True)
        heading.add_css_class("heading")
        copy.append(heading)
        body = Gtk.Label(label=description, xalign=0, wrap=True)
        body.add_css_class("dim-label")
        copy.append(body)
        status.append(copy)
        return status, body

    def _build_search_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_top=12,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search Newelle extensions"), hexpand=True
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda _entry: self._search_now())
        controls.append(self.search_entry)

        self.popular_button = Gtk.ToggleButton(label=_("Popular"), active=True)
        self.popular_button.add_css_class("flat")
        self.recent_button = Gtk.ToggleButton(label=_("Recent"), group=self.popular_button)
        self.recent_button.add_css_class("flat")
        self.popular_button.connect("toggled", self._on_sort_changed)
        self.recent_button.connect("toggled", self._on_sort_changed)
        controls.append(self.popular_button)
        controls.append(self.recent_button)
        page.append(controls)

        self.results_stack = Gtk.Stack(vhomogeneous=False)
        initial, self.initial_description = self._build_status(
            "system-search-symbolic",
            _("Browse community extensions"),
            _("Loading repositories tagged newelle-extension…"),
        )
        self.results_stack.add_named(initial, "initial")
        loading, _loading_description = self._build_status(
            None, _("Loading extensions"), _("Fetching repositories from GitHub…"), spinner=True
        )
        self.results_stack.add_named(loading, "loading")
        empty, _empty_description = self._build_status(
            "system-search-symbolic", _("No extensions found"), _("Try a broader search."),
        )
        self.results_stack.add_named(empty, "empty")
        self.error_page, self.error_description = self._build_status(
            "dialog-warning-symbolic", _("Could not load extensions"), _("Check your connection and try again."),
        )
        self.results_stack.add_named(self.error_page, "error")

        results = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        self.results_summary = Gtk.Label(xalign=0, css_classes=["dim-label"])
        results.append(self.results_summary)
        self.catalog_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            row_spacing=12,
            column_spacing=12,
            min_children_per_line=1,
            max_children_per_line=3,
            valign=Gtk.Align.START,
        )
        results.append(self.catalog_flow)
        self.load_more_button = Gtk.Button(label=_("Load more"), halign=Gtk.Align.CENTER, css_classes=["pill"])
        self.load_more_button.connect("clicked", lambda _button: self._load_more())
        results.append(self.load_more_button)
        self.results_stack.add_named(results, "results")
        self.results_stack.set_visible_child_name("initial")
        page.append(self.results_stack)
        self.stack.add_named(page, "search")
        self.stack.set_visible_child_name("search")

    def _on_unrealize(self, _widget):
        self.closed = True
        self.request_generation += 1
        if self.debounce_source is not None:
            GLib.source_remove(self.debounce_source)
            self.debounce_source = None

    def _on_search_changed(self, _entry):
        if self.debounce_source is not None:
            GLib.source_remove(self.debounce_source)
        self.debounce_source = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS, self._debounced_search
        )

    def _debounced_search(self):
        self.debounce_source = None
        self._search_now()
        return GLib.SOURCE_REMOVE

    def _on_sort_changed(self, button):
        if not button.get_active():
            return
        self.current_sort = "stars" if button is self.popular_button else "updated"
        if self.current_page:
            self._search_now()

    def _load_default_feed(self):
        if not self.current_page and not self.loading:
            self._request_page("", 1)
        return GLib.SOURCE_REMOVE

    def _search_now(self):
        self.current_query = self.search_entry.get_text().strip()
        self.current_page = 0
        self.has_next = False
        self._clear_results()
        self._request_page(self.current_query, 1)

    def _load_more(self):
        if not self.loading and self.has_next:
            self._request_page(self.current_query, self.current_page + 1)

    def _request_page(self, query, page):
        if self.loading:
            return
        self.loading = True
        self.request_generation += 1
        generation = self.request_generation
        self.load_more_button.set_sensitive(False)
        if page == 1:
            self.results_stack.set_visible_child_name("loading")
        cache_key = (query.casefold(), self.current_sort, page)
        if cache_key in self.cache:
            GLib.idle_add(self._finish_search, self.cache[cache_key], None, page, generation)
            return

        def worker():
            try:
                result = search_extensions(query, page, self.current_sort)
                error = None
            except (ExtensionCatalogError, requests.RequestException, OSError, ValueError) as exc:
                result = None
                error = str(exc)
            GLib.idle_add(self._finish_search, result, error, page, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_search(self, result, error, page, generation):
        if self.closed or generation != self.request_generation:
            return GLib.SOURCE_REMOVE
        self.loading = False
        self.load_more_button.set_sensitive(True)
        if error is not None or result is None:
            if page == 1:
                self.error_description.set_label(error or _("The request failed."))
                self.results_stack.set_visible_child_name("error")
            else:
                self.toast_overlay.add_toast(Adw.Toast(title=_("Could not load more extensions")))
            return GLib.SOURCE_REMOVE

        cache_key = (self.current_query.casefold(), self.current_sort, page)
        self.cache[cache_key] = result
        self.cache.move_to_end(cache_key)
        while len(self.cache) > SEARCH_CACHE_SIZE:
            self.cache.popitem(last=False)
        repositories = result["repositories"]
        if page == 1 and not repositories:
            self.results_stack.set_visible_child_name("empty")
            return GLib.SOURCE_REMOVE
        for repository in repositories:
            self._append_repository_card(repository)
        self.current_page = page
        self.has_next = len(repositories) == DEFAULT_PAGE_SIZE and len(self.result_cards) < 100
        if self.current_query:
            self.results_summary.set_label(
                _("Showing {shown} community extensions").format(shown=len(self.result_cards))
            )
        else:
            self.results_summary.set_label(
                _("{shown} extensions tagged newelle-extension").format(shown=len(self.result_cards))
            )
        self.load_more_button.set_visible(self.has_next)
        self.results_stack.set_visible_child_name("results")
        return GLib.SOURCE_REMOVE

    def _clear_results(self):
        child = self.catalog_flow.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.catalog_flow.remove(child)
            child = next_child
        self.result_cards = []

    def _append_repository_card(self, repository):
        card = Gtk.Button(hexpand=True, width_request=270, css_classes=["card"])
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=14,
            margin_bottom=14,
            margin_start=14,
            margin_end=14,
        )
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        heading.append(Gtk.Image(icon_name="extension-symbolic", valign=Gtk.Align.START))
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        name = Gtk.Label(label=repository["name"], xalign=0, wrap=True)
        name.add_css_class("heading")
        title_box.append(name)
        owner = Gtk.Label(label=repository["owner"], xalign=0, ellipsize=3)
        owner.add_css_class("dim-label")
        title_box.append(owner)
        heading.append(title_box)
        heading.append(Gtk.Image(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER))
        content.append(heading)

        description = Gtk.Label(
            label=repository["description"], xalign=0, yalign=0, wrap=True,
            lines=4, ellipsize=3, max_width_chars=36,
        )
        description.add_css_class("dim-label")
        content.append(description)
        metadata = Gtk.Label(
            label=_("★ {stars}  ·  updated {updated}").format(
                stars=_format_stars(repository["stars"]),
                updated=_format_updated(repository["updated_at"]),
            ),
            xalign=0,
        )
        metadata.add_css_class("caption")
        content.append(metadata)
        if repository.get("archived"):
            archived_label = Gtk.Label(
                label=_("Archived · may be outdated or unmaintained"),
                xalign=0,
                wrap=True,
            )
            archived_label.add_css_class("warning")
            archived_label.add_css_class("caption")
            content.append(archived_label)
        card.set_child(content)
        card.connect("clicked", self._show_detail, repository)
        self.catalog_flow.append(card)
        self.result_cards.append((repository, card))

    def _show_detail(self, _button, repository):
        self.selected_repository = repository
        if self.detail_page is not None:
            self.stack.remove(self.detail_page)
        self.detail_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        back = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["flat"])
        back.connect("clicked", lambda _button: self.stack.set_visible_child_name("search"))
        header.append(back)
        title = Gtk.Label(label=repository["name"], xalign=0, hexpand=True, wrap=True)
        title.add_css_class("title-2")
        header.append(title)
        open_button = Gtk.Button(icon_name="internet-symbolic", css_classes=["flat"])
        open_button.set_tooltip_text(_("Open repository on GitHub"))
        open_button.connect("clicked", lambda _button: open_website(repository["html_url"]))
        header.append(open_button)
        self.install_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        header.append(self.install_spinner)
        self.install_button = Gtk.Button(label=_("Loading files…"), css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
        self.install_button.set_sensitive(False)
        self.install_button.connect("clicked", self._install_selected)
        header.append(self.install_button)
        self.detail_page.append(header)

        detail_stack = Gtk.Stack(vhomogeneous=False)
        loading, _loading_body = self._build_status(
            None, _("Inspecting repository"), _("Reading the README and checking Python files without executing them…"), spinner=True
        )
        detail_stack.add_named(loading, "loading")
        self.detail_error, self.detail_error_body = self._build_status(
            "dialog-warning-symbolic", _("Could not inspect repository"), _("The repository could not be read."),
        )
        detail_stack.add_named(self.detail_error, "error")
        self.detail_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        detail_stack.add_named(self.detail_content, "content")
        detail_stack.set_visible_child_name("loading")
        self.detail_page.append(detail_stack)
        self.stack.add_named(self.detail_page, "detail")
        self.stack.set_visible_child_name("detail")

        generation = self.request_generation = self.request_generation + 1

        def worker():
            try:
                manager = get_download_manager()
                with manager.operation(
                    _("Download extension {name}").format(
                        name=repository["name"]
                    ),
                    kind=DownloadKind.EXTENSION,
                    source_id=f"extension:{repository['full_name']}",
                    phase=_("Inspecting extension payload"),
                    cancellable=True,
                ) as task:
                    result = load_extension_repository(repository, task=task)
                error = None
            except DownloadCancelled:
                result = None
                error = _("Download cancelled")
            except (ExtensionCatalogError, requests.RequestException, OSError, ValueError) as exc:
                result = None
                error = str(exc)
            GLib.idle_add(self._finish_detail, result, error, generation, detail_stack)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_detail(self, repository, error, generation, detail_stack):
        if self.closed or generation != self.request_generation:
            return GLib.SOURCE_REMOVE
        if error is not None or repository is None:
            self.detail_error_body.set_label(error or _("The request failed."))
            detail_stack.set_visible_child_name("error")
            self.install_button.set_label(_("Unavailable"))
            return GLib.SOURCE_REMOVE

        self.selected_repository = repository
        self.detail_files = repository["files"]
        self.file_checks = []
        self.detail_content.remove(self.detail_content.get_first_child()) if self.detail_content.get_first_child() else None

        overview = Adw.PreferencesGroup(
            title=repository["full_name"],
            description=repository["description"],
        )
        overview.add(
            Adw.ActionRow(
                title=_("Source"), subtitle=repository["source_label"], icon_name="folder-download-symbolic"
            )
        )
        overview.add(
            Adw.ActionRow(
                title=_("Popularity"), subtitle=_("{stars} GitHub stars · {forks} forks").format(
                    stars=f"{repository['stars']:,}", forks=f"{repository['forks']:,}"
                ), icon_name="star-filled-rounded-symbolic"
            )
        )
        if repository.get("archived"):
            overview.add(
                Adw.ActionRow(
                    title=_("Archived repository"),
                    subtitle=_("This repository may be outdated or unmaintained."),
                    icon_name="dialog-warning-symbolic",
                )
            )
        warning = Adw.ActionRow(
            title=_("Review before installing"),
            subtitle=_("Extensions are Python code and can access the capabilities granted to Newelle."),
            icon_name="dialog-warning-symbolic",
        )
        overview.add(warning)
        self.detail_content.append(overview)

        readme_group = Adw.PreferencesGroup(title=_("README"), description=_("Rendered from the repository README.md"))
        readme_scroll = Gtk.ScrolledWindow(
            min_content_height=260,
            max_content_height=460,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        readme_view = self._build_readme_webview(repository)
        readme_scroll.set_child(readme_view)
        readme_group.add(readme_scroll)
        self.detail_content.append(readme_group)

        files_group = Adw.PreferencesGroup(
            title=_("Files to download"),
            description=_(
                "Release files are selected by default; default-branch files must be selected manually."
            ),
        )
        for item in self.detail_files:
            status = _("Valid Newelle extension") if item.get("is_extension") else item.get("error", _("Rejected"))
            row = Adw.ActionRow(title=item["path"], subtitle=status)
            default_selected = (
                repository.get("source_kind") == "release"
                and item.get("is_extension", False)
            )
            check = Gtk.CheckButton(
                active=default_selected,
                sensitive=item.get("is_extension", False),
                valign=Gtk.Align.CENTER,
            )
            check.connect("toggled", lambda _check: self._update_install_button())
            row.add_prefix(check)
            if item.get("is_extension"):
                row.add_suffix(Gtk.Image(icon_name="emblem-default-symbolic", css_classes=["success"]))
            else:
                row.add_suffix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            files_group.add(row)
            self.file_checks.append((item, check))
        if not self.detail_files:
            files_group.add(Adw.ActionRow(title=_("No files found"), subtitle=_("This repository did not expose any files to inspect.")))
        self.detail_content.append(files_group)
        self.install_spinner.stop()
        self._update_install_button()
        detail_stack.set_visible_child_name("content")
        return GLib.SOURCE_REMOVE

    def _build_readme_webview(self, repository):
        """Show GitHub's server-rendered README inside a restricted WebView."""
        content_manager = WebKit.UserContentManager()
        content_manager.add_script(
            WebKit.UserScript.new(
                README_EXTRACTOR_SCRIPT,
                WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.END,
                ["https://github.com/*", "https://www.github.com/*"],
                [],
            )
        )
        webview = WebKit.WebView(
            hexpand=True,
            vexpand=True,
            user_content_manager=content_manager,
        )
        settings = WebKit.Settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(False)
        webview.set_settings(settings)
        webview.connect("decide-policy", self._on_readme_policy)

        readme_url = repository.get("readme_url")
        if readme_url:
            # The API's html_url is GitHub's rendered README page, so images,
            # tables, fenced code blocks, and relative links render correctly.
            webview.load_uri(readme_url)
        else:
            fallback = escape_html(repository.get("readme", ""))
            fallback_html = (
                "<html><head><meta charset='utf-8'><style>"
                "body{font-family:sans-serif;padding:18px;white-space:pre-wrap;"
                "color:#e8e8e8;background:#242424}"
                "</style></head><body>"
                f"{fallback}</body></html>"
            )
            webview.load_html(fallback_html, "https://github.com/")
        return webview

    def _on_readme_policy(self, _webview, decision, decision_type):
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        action = decision.get_navigation_action()
        request = action.get_request() if action is not None else None
        uri = request.get_uri() if request is not None else None
        hostname = (urlparse(uri).hostname or "").lower() if uri else ""
        if hostname in {"github.com", "www.github.com"}:
            decision.use()
            return False
        if uri:
            decision.ignore()
            open_website(uri)
            return True
        decision.ignore()
        return True

    def _update_install_button(self):
        if self.install_button is None:
            return
        selected = sum(1 for item, check in self.file_checks if check.get_active() and item.get("is_extension"))
        self.install_button.set_label(_("Install {count} selected").format(count=selected))
        self.install_button.set_sensitive(selected > 0)

    def _install_selected(self, _button):
        selected = [item for item, check in self.file_checks if check.get_active() and item.get("is_extension")]
        if not selected or self.install_callback is None:
            return
        self.install_button.set_sensitive(False)
        self.install_spinner.start()
        install_button = self.install_button
        install_spinner = self.install_spinner

        def worker():
            try:
                manager = get_download_manager()
                repository_name = (
                    self.selected_repository.get("name", _("extension"))
                    if self.selected_repository
                    else _("extension")
                )
                with manager.operation(
                    _("Install extension {name}").format(name=repository_name),
                    kind=DownloadKind.EXTENSION,
                    source_id=f"extension-install:{repository_name}",
                    phase=_("Installing extension"),
                    cancellable=False,
                ):
                    installed = self.install_callback(selected)
                error = None
            except (ExtensionCatalogError, OSError, ValueError) as exc:
                installed = None
                error = str(exc)
            GLib.idle_add(self._finish_install, installed, error, install_button, install_spinner)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_install(self, installed, error, install_button, install_spinner):
        if self.closed:
            return GLib.SOURCE_REMOVE
        install_spinner.stop()
        if error is not None or not installed:
            install_button.set_sensitive(True)
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("Could not install extension: {}" ).format(error or _("Unknown error")))
            )
            self._update_install_button()
            return GLib.SOURCE_REMOVE
        install_button.set_label(_("Installed {count} files").format(count=len(installed)))
        install_button.set_sensitive(False)
        if self.on_installed is not None:
            self.on_installed(installed)
        self.toast_overlay.add_toast(
            Adw.Toast(title=_("Extension files added and activated."))
        )
        return GLib.SOURCE_REMOVE

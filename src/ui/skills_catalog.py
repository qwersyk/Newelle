"""Lazy SkillsMP catalog for discovering and installing Agent Skills."""

from __future__ import annotations

import gettext
import json
import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse

import requests
from gi.repository import Adw, GLib, Gtk

from ..utility.system import open_website
from ..utility.download_manager import (
    DownloadCancelled,
    DownloadKind,
    current_download_task,
    get_download_manager,
)

_ = gettext.gettext

SKILLSMP_API_URL = "https://skillsmp.com/api/v1/skills/search"
SEARCH_PAGE_SIZE = 20
DEFAULT_FEED_SIZE = 6
DEFAULT_FEED_QUERY = "skill"
MAX_RENDERED_RESULTS = 100
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SKILL_FILES = 100
MAX_SKILL_FILE_BYTES = 2 * 1024 * 1024
MAX_SKILL_TOTAL_BYTES = 10 * 1024 * 1024
MAX_SKILL_DEPTH = 8
SEARCH_DEBOUNCE_MS = 450
SEARCH_CACHE_SIZE = 20
REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Newelle-SkillsMP/1.0 (+https://github.com/qwersyk/Newelle)",
}
GITHUB_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SkillsCatalogError(ValueError):
    """Raised when a catalog response or downloadable skill is invalid."""


def _required_string(value, label, max_length=4096):
    if not isinstance(value, str) or not value.strip():
        raise SkillsCatalogError(f"{label} must be a non-empty string")
    if len(value) > max_length or "\x00" in value:
        raise SkillsCatalogError(f"{label} is invalid")
    return value


def _https_url(value, label, allowed_hosts=None):
    value = _required_string(value, label, 4096)
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise SkillsCatalogError(f"{label} must be a valid HTTPS URL")
    hostname = (parsed.hostname or "").lower()
    if allowed_hosts is not None and hostname not in allowed_hosts:
        raise SkillsCatalogError(f"{label} uses an unsupported host")
    return value


def validate_search_response(payload):
    """Validate the bounded subset of the SkillsMP response consumed by the UI."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = error.get("message") if isinstance(error, dict) else None
        raise SkillsCatalogError(message or "SkillsMP returned an invalid response")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise SkillsCatalogError("SkillsMP response is missing data")
    raw_skills = data.get("skills")
    pagination = data.get("pagination")
    if not isinstance(raw_skills, list) or len(raw_skills) > 50:
        raise SkillsCatalogError("SkillsMP returned an invalid skills list")
    if not isinstance(pagination, dict):
        raise SkillsCatalogError("SkillsMP response is missing pagination")

    skills = []
    seen_ids = set()
    for index, item in enumerate(raw_skills):
        if not isinstance(item, dict):
            raise SkillsCatalogError(f"skills[{index}] must be an object")
        skill_id = _required_string(item.get("id"), f"skills[{index}].id", 300)
        if skill_id in seen_ids:
            raise SkillsCatalogError("SkillsMP returned duplicate skills")
        seen_ids.add(skill_id)
        stars = item.get("stars")
        updated_at = item.get("updatedAt")
        if not isinstance(stars, int) or stars < 0:
            raise SkillsCatalogError(f"skills[{index}].stars is invalid")
        if not isinstance(updated_at, int) or updated_at < 0:
            raise SkillsCatalogError(f"skills[{index}].updatedAt is invalid")
        language = item.get("contentLanguage")
        if language is not None and (
            not isinstance(language, str) or len(language) > 12
        ):
            raise SkillsCatalogError(f"skills[{index}].contentLanguage is invalid")

        skills.append(
            {
                "id": skill_id,
                "name": _required_string(
                    item.get("name"), f"skills[{index}].name", 200
                ),
                "author": _required_string(
                    item.get("author"), f"skills[{index}].author", 200
                ),
                "description": _required_string(
                    item.get("description"), f"skills[{index}].description", 2000
                ),
                "contentLanguage": language,
                "githubUrl": _https_url(
                    item.get("githubUrl"),
                    f"skills[{index}].githubUrl",
                    {"github.com", "www.github.com"},
                ),
                "skillUrl": _https_url(
                    item.get("skillUrl"),
                    f"skills[{index}].skillUrl",
                    {"skillsmp.com", "www.skillsmp.com"},
                ),
                "stars": stars,
                "updatedAt": updated_at,
            }
        )

    page = pagination.get("page")
    has_next = pagination.get("hasNext")
    total = pagination.get("total")
    if not isinstance(page, int) or page < 1 or not isinstance(has_next, bool):
        raise SkillsCatalogError("SkillsMP pagination is invalid")
    if not isinstance(total, int) or total < 0:
        raise SkillsCatalogError("SkillsMP result total is invalid")

    return {
        "skills": skills,
        "page": page,
        "has_next": has_next,
        "total": total,
        "total_is_exact": pagination.get("totalIsExact") is True,
    }


def _read_json_response(response, max_bytes=MAX_RESPONSE_BYTES):
    chunks = []
    size = 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise SkillsCatalogError("The server response is too large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillsCatalogError("The server returned invalid JSON") from exc


def search_skills(
    query,
    page=1,
    sort_by="stars",
    api_key=None,
    limit=SEARCH_PAGE_SIZE,
    session=requests,
):
    """Fetch one SkillsMP page. This is called only after explicit user input."""
    query = _required_string(query.strip(), "query", 200)
    if not isinstance(page, int) or page < 1:
        raise SkillsCatalogError("page is invalid")
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise SkillsCatalogError("page size is invalid")
    if sort_by not in {"stars", "recent"}:
        raise SkillsCatalogError("sort order is invalid")

    headers = dict(REQUEST_HEADERS)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    api_url = os.environ.get("NEWELLE_SKILLSMP_API_URL", SKILLSMP_API_URL)
    _https_url(api_url, "SkillsMP API URL")

    with session.get(
        api_url,
        params={
            "q": query,
            "page": page,
            "limit": limit,
            "sortBy": sort_by,
        },
        headers=headers,
        timeout=(5, 20),
        stream=True,
    ) as response:
        payload = _read_json_response(response)
        if response.status_code >= 400:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message") if isinstance(error, dict) else None
            if response.status_code == 429:
                raise SkillsCatalogError(
                    message or "SkillsMP search quota reached. Try again later."
                )
            raise SkillsCatalogError(
                message or f"SkillsMP request failed ({response.status_code})"
            )
        result = validate_search_response(payload)
        remaining = response.headers.get("X-RateLimit-Daily-Remaining")
        result["remaining"] = (
            int(remaining) if remaining and remaining.isdigit() else None
        )
        return result


def parse_github_tree_url(url):
    """Return owner, repository, ref and directory from a GitHub tree URL."""
    _https_url(url, "GitHub URL", {"github.com", "www.github.com"})
    parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
    if len(parts) < 5 or parts[2] != "tree":
        raise SkillsCatalogError("The skill source must be a GitHub directory URL")
    owner, repository, _tree, ref, *directory = parts
    repository = repository.removesuffix(".git")
    for segment in (owner, repository, ref, *directory):
        if not GITHUB_SEGMENT_RE.fullmatch(segment) or segment in {".", ".."}:
            raise SkillsCatalogError("The GitHub skill path is invalid")
    return owner, repository, ref, "/".join(directory)


def _github_api_json(url, session):
    with session.get(
        url,
        headers={**REQUEST_HEADERS, "Accept": "application/vnd.github+json"},
        timeout=(5, 20),
        stream=True,
    ) as response:
        payload = _read_json_response(response)
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise SkillsCatalogError(
                message or f"GitHub request failed ({response.status_code})"
            )
        return payload


def download_github_skill(github_url, target_dir, session=requests, task=None):
    """Download one GitHub directory with strict traversal and size limits."""
    task = task or current_download_task()
    owner, repository, ref, root_path = parse_github_tree_url(github_url)
    os.makedirs(target_dir, exist_ok=False)
    pending = [(root_path, 0)]
    file_count = 0
    total_bytes = 0

    while pending:
        if task is not None:
            task.check_cancelled()
        current_path, depth = pending.pop()
        if depth > MAX_SKILL_DEPTH:
            raise SkillsCatalogError("The skill directory is nested too deeply")
        encoded_path = quote(current_path, safe="/")
        api_url = (
            f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}"
            f"/contents/{encoded_path}?ref={quote(ref)}"
        )
        entries = _github_api_json(api_url, session)
        if not isinstance(entries, list):
            raise SkillsCatalogError("The GitHub source is not a directory")

        for entry in entries:
            if not isinstance(entry, dict):
                raise SkillsCatalogError("GitHub returned an invalid directory entry")
            entry_type = entry.get("type")
            entry_path = entry.get("path")
            if not isinstance(entry_path, str):
                raise SkillsCatalogError("GitHub returned an invalid file path")
            try:
                relative = PurePosixPath(entry_path).relative_to(
                    PurePosixPath(root_path)
                )
            except ValueError as exc:
                raise SkillsCatalogError(
                    "GitHub returned a file outside the skill directory"
                ) from exc
            if not relative.parts or any(
                part in {"", ".", ".."} for part in relative.parts
            ):
                raise SkillsCatalogError("GitHub returned an unsafe file path")

            if entry_type == "dir":
                pending.append((entry_path, depth + 1))
                continue
            if entry_type != "file":
                raise SkillsCatalogError(
                    "Skills containing links or submodules are not supported"
                )

            declared_size = entry.get("size")
            if not isinstance(declared_size, int) or declared_size < 0:
                raise SkillsCatalogError("GitHub returned an invalid file size")
            if declared_size > MAX_SKILL_FILE_BYTES:
                raise SkillsCatalogError("A skill file exceeds the 2 MB limit")
            file_count += 1
            total_bytes += declared_size
            if file_count > MAX_SKILL_FILES or total_bytes > MAX_SKILL_TOTAL_BYTES:
                raise SkillsCatalogError(
                    "The skill exceeds the installation size limits"
                )

            download_url = _https_url(
                entry.get("download_url"),
                "GitHub download URL",
                {"raw.githubusercontent.com"},
            )
            destination = os.path.join(target_dir, *relative.parts)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            actual_size = 0
            if task is not None:
                task.update(
                    phase=_("Downloading {name}").format(name=relative.name),
                    transferred_bytes=max(0, total_bytes - declared_size),
                    reset_progress=False,
                    cancellable=True,
                )
            with session.get(
                download_url,
                headers=REQUEST_HEADERS,
                timeout=(5, 30),
                stream=True,
            ) as response:
                response.raise_for_status()
                with open(destination, "wb") as output:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        if task is not None:
                            task.check_cancelled()
                        actual_size += len(chunk)
                        if actual_size > MAX_SKILL_FILE_BYTES:
                            raise SkillsCatalogError(
                                "A skill file exceeds the 2 MB limit"
                            )
                        output.write(chunk)
                        if task is not None:
                            task.update(
                                transferred_bytes=max(
                                    0, total_bytes - declared_size + actual_size
                                )
                            )
            total_bytes += actual_size - declared_size
            if total_bytes > MAX_SKILL_TOTAL_BYTES:
                raise SkillsCatalogError("The skill exceeds the 10 MB limit")

    if not os.path.isfile(os.path.join(target_dir, "SKILL.md")):
        raise SkillsCatalogError("The selected GitHub directory has no SKILL.md")


def _format_stars(stars):
    if stars >= 1_000_000:
        return f"{stars / 1_000_000:.1f}M"
    if stars >= 1_000:
        return f"{stars / 1_000:.1f}k"
    return str(stars)


class SkillsCatalogView(Gtk.Box):
    """Search SkillsMP without doing network or bulk rendering work at startup."""

    def __init__(self, parent, controller, on_installed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.parent_window = parent
        self.controller = controller
        self.on_installed = on_installed
        self.api_key = os.environ.get("SKILLSMP_API_KEY")
        self.cache = OrderedDict()
        self.result_cards = []
        self.current_query = ""
        self.current_limit = SEARCH_PAGE_SIZE
        self.current_page = 0
        self.has_next = False
        self.search_generation = 0
        self.debounce_source = None
        self.loading = False
        self.closed = False
        self.detail_page = None
        self.selected_skill = None
        self.install_button = None
        self.install_spinner = None
        self.installing_names = set()

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
            margin_top=20,
            margin_bottom=20,
            margin_start=18,
            margin_end=18,
            valign=Gtk.Align.CENTER,
        )
        if spinner:
            indicator = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        else:
            indicator = Gtk.Image(
                icon_name=icon_name,
                pixel_size=32,
                valign=Gtk.Align.CENTER,
            )
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
            placeholder_text=_("Search community skills"),
            hexpand=True,
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda _entry: self._search_now())
        controls.append(self.search_entry)

        self.popular_button = Gtk.ToggleButton(label=_("Trending"), active=True)
        self.popular_button.add_css_class("flat")
        self.recent_button = Gtk.ToggleButton(
            label=_("Recent"), group=self.popular_button
        )
        self.recent_button.add_css_class("flat")
        self.popular_button.connect("toggled", self._on_sort_changed)
        self.recent_button.connect("toggled", self._on_sort_changed)
        controls.append(self.popular_button)
        controls.append(self.recent_button)
        page.append(controls)

        self.results_stack = Gtk.Stack(vhomogeneous=False)
        self.initial_page, self.initial_description = self._build_status(
            "system-search-symbolic",
            _("Keep typing"),
            _("Enter at least two characters to search SkillsMP."),
        )
        self.results_stack.add_named(self.initial_page, "initial")
        loading, _loading_description = self._build_status(
            None,
            _("Loading skills"),
            _("Fetching a small page from SkillsMP…"),
            spinner=True,
        )
        self.results_stack.add_named(loading, "loading")
        self.empty_page, _empty_description = self._build_status(
            "system-search-symbolic",
            _("No skills found"),
            _("Try a broader search."),
        )
        self.results_stack.add_named(self.empty_page, "empty")
        self.error_page, self.error_description = self._build_status(
            "dialog-warning-symbolic",
            _("Could not load SkillsMP"),
            _("Check your connection and try again."),
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
        self.load_more_button = Gtk.Button(
            label=_("Load more"),
            halign=Gtk.Align.CENTER,
            css_classes=["pill"],
        )
        self.load_more_button.connect("clicked", lambda _button: self._load_more())
        results.append(self.load_more_button)
        self.results_stack.add_named(results, "results")
        self.results_stack.set_visible_child_name("initial")
        page.append(self.results_stack)
        self.stack.add_named(page, "search")
        self.stack.set_visible_child_name("search")

    def _on_unrealize(self, _widget):
        self.closed = True
        self.search_generation += 1
        if self.debounce_source is not None:
            GLib.source_remove(self.debounce_source)
            self.debounce_source = None

    def _on_search_changed(self, _entry):
        if self.debounce_source is not None:
            GLib.source_remove(self.debounce_source)
            self.debounce_source = None
        query = self.search_entry.get_text().strip()
        if not query:
            self.search_generation += 1
            self.loading = False
            self.debounce_source = GLib.timeout_add(150, self._debounced_default_feed)
            return
        if len(query) < 2:
            self.search_generation += 1
            self.loading = False
            self.results_stack.set_visible_child_name("initial")
            return
        self.debounce_source = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS, self._debounced_search
        )

    def _debounced_search(self):
        self.debounce_source = None
        self._search_now()
        return False

    def _debounced_default_feed(self):
        self.debounce_source = None
        self._load_default_feed()
        return False

    def _on_sort_changed(self, button):
        if not button.get_active():
            return
        if self.search_entry.get_text().strip():
            self._search_now()
        else:
            self._load_default_feed()

    def _sort_by(self):
        return "stars" if self.popular_button.get_active() else "recent"

    def _load_default_feed(self):
        if self.closed:
            return False
        if self.debounce_source is not None:
            GLib.source_remove(self.debounce_source)
            self.debounce_source = None
        self.search_generation += 1
        self.loading = False
        self.current_query = DEFAULT_FEED_QUERY
        self.current_limit = DEFAULT_FEED_SIZE
        self.current_page = 0
        self.has_next = False
        self._clear_results()
        self.results_stack.set_visible_child_name("loading")
        self._request_page(1, self.search_generation)
        return False

    def _search_now(self):
        if self.debounce_source is not None:
            GLib.source_remove(self.debounce_source)
            self.debounce_source = None
        query = self.search_entry.get_text().strip()
        if len(query) < 2:
            return
        self.search_generation += 1
        # A superseded request may still be in flight. Its generation guard will
        # discard it, while the new query must be allowed to start immediately.
        self.loading = False
        self.current_query = query
        self.current_limit = SEARCH_PAGE_SIZE
        self.current_page = 0
        self.has_next = False
        self._clear_results()
        self.results_stack.set_visible_child_name("loading")
        self._request_page(1, self.search_generation)

    def _load_more(self):
        if self.loading or not self.has_next:
            return
        self._request_page(self.current_page + 1, self.search_generation)

    def _request_page(self, page, generation):
        if self.loading:
            return
        cache_key = (
            self.current_query.casefold(),
            self._sort_by(),
            self.current_limit,
            page,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.cache.move_to_end(cache_key)
            self._finish_search(cached, None, page, generation)
            return

        self.loading = True
        self.load_more_button.set_sensitive(False)
        query = self.current_query
        sort_by = self._sort_by()
        limit = self.current_limit

        def worker():
            try:
                result = search_skills(query, page, sort_by, self.api_key, limit)
                error = None
            except (SkillsCatalogError, requests.RequestException, OSError) as exc:
                result = None
                error = str(exc)
            GLib.idle_add(self._finish_search, result, error, page, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_search(self, result, error, page, generation):
        if self.closed or generation != self.search_generation:
            return False
        self.loading = False
        self.load_more_button.set_sensitive(True)
        if error is not None or result is None:
            if self.current_page == 0:
                self.error_description.set_label(error or _("The request failed."))
                self.results_stack.set_visible_child_name("error")
            else:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("Could not load more skills"))
                )
            return False

        cache_key = (
            self.current_query.casefold(),
            self._sort_by(),
            self.current_limit,
            page,
        )
        self.cache[cache_key] = result
        self.cache.move_to_end(cache_key)
        while len(self.cache) > SEARCH_CACHE_SIZE:
            self.cache.popitem(last=False)

        if page == 1 and not result["skills"]:
            self.results_stack.set_visible_child_name("empty")
            return False
        for skill in result["skills"]:
            self._append_skill_card(skill)
        self.current_page = page
        self.has_next = (
            result["has_next"] and len(self.result_cards) < MAX_RENDERED_RESULTS
        )
        if not self.search_entry.get_text().strip():
            self.results_summary.set_label(
                _("Trending skills from SkillsMP · {shown} loaded").format(
                    shown=len(self.result_cards)
                )
            )
        else:
            total_suffix = "" if result["total_is_exact"] else "+"
            self.results_summary.set_label(
                _("Showing {shown} of {total}{suffix} results").format(
                    shown=len(self.result_cards),
                    total=result["total"],
                    suffix=total_suffix,
                )
            )
        self.load_more_button.set_visible(self.has_next)
        self.results_stack.set_visible_child_name("results")
        return False

    def _clear_results(self):
        child = self.catalog_flow.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.catalog_flow.remove(child)
            child = next_child
        self.result_cards = []

    def _append_skill_card(self, skill):
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
        heading.append(Gtk.Image(icon_name="skills-symbolic", valign=Gtk.Align.START))
        title_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True
        )
        name = Gtk.Label(label=skill["name"], xalign=0, wrap=True)
        name.add_css_class("heading")
        title_box.append(name)
        author = Gtk.Label(label=skill["author"], xalign=0, ellipsize=3)
        author.add_css_class("dim-label")
        title_box.append(author)
        heading.append(title_box)
        if self._is_installed(skill):
            installed = Gtk.Image(
                icon_name="emblem-default-symbolic",
                tooltip_text=_("Installed"),
                valign=Gtk.Align.CENTER,
            )
            installed.add_css_class("success")
            heading.append(installed)
        heading.append(Gtk.Image(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER))
        content.append(heading)

        description = Gtk.Label(
            label=skill["description"],
            xalign=0,
            yalign=0,
            wrap=True,
            lines=4,
            ellipsize=3,
            max_width_chars=36,
        )
        description.add_css_class("dim-label")
        content.append(description)
        metadata = Gtk.Label(
            label=_("★ {stars}  ·  {language}").format(
                stars=_format_stars(skill["stars"]),
                language=(
                    skill.get("contentLanguage") or _("Unknown language")
                ).upper(),
            ),
            xalign=0,
        )
        metadata.add_css_class("caption")
        content.append(metadata)
        card.set_child(content)
        card.connect("clicked", self._show_detail, skill)
        self.catalog_flow.append(card)
        self.result_cards.append((skill, card))

    def _is_installed(self, skill):
        return skill["name"] in self.controller.skill_manager.skills

    def _show_detail(self, _button, skill):
        self.selected_skill = skill
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
        back.connect(
            "clicked", lambda _button: self.stack.set_visible_child_name("search")
        )
        header.append(back)
        title = Gtk.Label(label=skill["name"], xalign=0, hexpand=True, wrap=True)
        title.add_css_class("title-2")
        header.append(title)
        self.install_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        header.append(self.install_spinner)
        self.install_button = Gtk.Button(
            label=_("Installed") if self._is_installed(skill) else _("Install"),
            css_classes=["suggested-action"],
            valign=Gtk.Align.CENTER,
            sensitive=not self._is_installed(skill),
        )
        self.install_button.connect("clicked", self._install_selected_skill)
        header.append(self.install_button)
        self.detail_page.append(header)

        group = Adw.PreferencesGroup(
            title=skill["name"], description=skill["description"]
        )
        group.set_margin_start(18)
        group.set_margin_end(18)
        group.set_margin_bottom(18)
        group.add(
            Adw.ActionRow(
                title=_("Creator"),
                subtitle=skill["author"],
                icon_name="avatar-symbolic",
            )
        )
        group.add(
            Adw.ActionRow(
                title=_("Popularity"),
                subtitle=_("{stars} GitHub stars").format(stars=f"{skill['stars']:,}"),
                icon_name="star-filled-rounded-symbolic",
            )
        )
        warning = Adw.ActionRow(
            title=_("Review before installing"),
            subtitle=_(
                "Community skills can contain instructions, scripts, and other files."
            ),
            icon_name="dialog-warning-symbolic",
        )
        group.add(warning)
        source_row = Adw.ActionRow(title=_("Source code"), subtitle=skill["githubUrl"])
        source_button = Gtk.Button(
            icon_name="internet-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        source_button.connect(
            "clicked", lambda _button: open_website(skill["githubUrl"])
        )
        source_row.add_suffix(source_button)
        group.add(source_row)
        listing_row = Adw.ActionRow(title="SkillsMP", subtitle=skill["skillUrl"])
        listing_button = Gtk.Button(
            icon_name="internet-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        listing_button.connect(
            "clicked", lambda _button: open_website(skill["skillUrl"])
        )
        listing_row.add_suffix(listing_button)
        group.add(listing_row)
        self.detail_page.append(group)
        self.stack.add_named(self.detail_page, "detail")
        self.stack.set_visible_child_name("detail")

    def _install_selected_skill(self, _button):
        skill = self.selected_skill
        if skill is None or self._is_installed(skill):
            return
        if skill["name"] in self.installing_names:
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("This skill is already being installed"))
            )
            return
        self.installing_names.add(skill["name"])
        self.install_button.set_sensitive(False)
        self.install_spinner.start()
        github_url = skill["githubUrl"]
        install_button = self.install_button
        install_spinner = self.install_spinner

        def worker():
            installed = None
            error = None
            try:
                manager = get_download_manager()
                with manager.operation(
                    _("Install skill {name}").format(name=skill["name"]),
                    kind=DownloadKind.SKILL,
                    source_id=f"skill:{skill['name']}",
                    phase=_("Downloading skill"),
                    cancellable=True,
                ) as task:
                    _owner, _repository, _ref, source_path = parse_github_tree_url(
                        github_url
                    )
                    directory_name = source_path.rsplit("/", 1)[-1]
                    with tempfile.TemporaryDirectory(prefix="newelle-skill-") as temporary:
                        source_dir = os.path.join(temporary, directory_name)
                        download_github_skill(github_url, source_dir, task=task)
                        task.update(
                            phase=_("Installing skill"),
                            reset_progress=True,
                            cancellable=False,
                        )
                        installed = self.controller.skill_manager.add_skill_from_path(
                            source_dir
                        )
                        if installed is None:
                            raise SkillsCatalogError("The downloaded SKILL.md is invalid")
            except DownloadCancelled:
                error = _("Installation cancelled")
            except (SkillsCatalogError, requests.RequestException, OSError) as exc:
                error = str(exc)
            GLib.idle_add(
                self._finish_install,
                installed,
                error,
                skill,
                install_button,
                install_spinner,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_install(
        self, installed, error, requested_skill, install_button, install_spinner
    ):
        self.installing_names.discard(requested_skill["name"])
        if self.closed:
            return False
        install_spinner.stop()
        if error is not None or installed is None:
            install_button.set_sensitive(True)
            self.toast_overlay.add_toast(
                Adw.Toast(
                    title=_("Could not install skill: {}").format(
                        error or _("Unknown error")
                    )
                )
            )
            return False
        install_button.set_label(_("Installed"))
        install_button.set_sensitive(False)
        self.toast_overlay.add_toast(
            Adw.Toast(title=_("Skill '{}' installed").format(installed.name))
        )
        if self.on_installed is not None:
            self.on_installed()
        self._refresh_installed_cards(requested_skill["name"])
        return False

    def _refresh_installed_cards(self, skill_name):
        for skill, card in self.result_cards:
            if skill["name"] != skill_name:
                continue
            content = card.get_child()
            heading = content.get_first_child()
            installed = Gtk.Image(
                icon_name="emblem-default-symbolic",
                tooltip_text=_("Installed"),
                valign=Gtk.Align.CENTER,
            )
            installed.add_css_class("success")
            heading.insert_child_after(
                installed, heading.get_last_child().get_prev_sibling()
            )

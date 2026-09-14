"""Normalize MCP server configurations pasted from other applications."""

from __future__ import annotations

import json
import gettext
import os
import shlex


_ = gettext.gettext


class MCPConfigError(ValueError):
    """Raised when pasted JSON cannot be converted to MCP server settings."""


_WRAPPER_KEYS = ("mcpServers", "mcp_servers", "servers")
_SERVER_MARKER_KEYS = {
    "command",
    "cmd",
    "url",
    "endpoint",
    "serverUrl",
    "server_url",
    "transport",
}
_HTTP_TYPES = {
    "http",
    "streamable-http",
    "streamable_http",
    "streamablehttp",
    "remote",
}
_STDIO_TYPES = {"stdio", "local", "command"}
_MAX_SERVERS = 100


def parse_mcp_servers_json(text: str) -> list[dict]:
    """Parse common MCP JSON formats into Newelle's internal server format.

    Accepted inputs include ``{"mcpServers": {"name": {...}}}``, the direct
    ``{"name": {...}}`` map, a single server object, and lists of server
    objects. ``servers`` and ``mcp_servers`` are also recognized as wrapper
    keys, and a surrounding ``mcp`` object is accepted.
    """
    if not isinstance(text, str) or not text.strip():
        raise MCPConfigError(_("Paste a JSON object or array."))

    source = _strip_markdown_fence(text)
    try:
        payload = json.loads(source)
    except json.JSONDecodeError as exc:
        raise MCPConfigError(
            _("Invalid JSON at line {line}, column {column}: {message}").format(
                line=exc.lineno,
                column=exc.colno,
                message=exc.msg,
            )
        ) from exc

    collection = _unwrap_server_collection(payload)
    entries = _server_entries(collection)
    if not entries:
        raise MCPConfigError(_("No MCP server configurations were found."))
    if len(entries) > _MAX_SERVERS:
        raise MCPConfigError(
            _("A maximum of {} servers can be imported at once.").format(
                _MAX_SERVERS
            )
        )

    servers = []
    for index, (name, config) in enumerate(entries, start=1):
        label = name or f"server {index}"
        try:
            servers.append(_normalize_server(config, name))
        except MCPConfigError as exc:
            raise MCPConfigError(
                _("Invalid configuration for {name}: {error}").format(
                    name=label,
                    error=exc,
                )
            ) from exc
    return servers


def _strip_markdown_fence(text: str) -> str:
    source = text.strip().lstrip("\ufeff")
    lines = source.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().startswith("```")
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip()
    return source


def _unwrap_server_collection(payload):
    current = payload
    if isinstance(current, dict) and "mcp" in current:
        nested = current["mcp"]
        if isinstance(nested, (dict, list)):
            current = nested

    if isinstance(current, dict):
        for key in _WRAPPER_KEYS:
            if key in current:
                wrapped = current[key]
                if not isinstance(wrapped, (dict, list, str)):
                    raise MCPConfigError(
                        _("{} must contain an object or array.").format(key)
                    )
                return wrapped
    return current


def _looks_like_server(config) -> bool:
    if isinstance(config, str):
        return True
    return isinstance(config, dict) and bool(_SERVER_MARKER_KEYS.intersection(config))


def _server_entries(collection) -> list[tuple[str | None, object]]:
    if isinstance(collection, list):
        return [(_entry_name(item), item) for item in collection]
    if _looks_like_server(collection):
        return [(_entry_name(collection), collection)]
    if isinstance(collection, dict):
        entries = []
        for name, config in collection.items():
            if not isinstance(name, str) or not name.strip():
                raise MCPConfigError(_("Server names must be non-empty strings."))
            if not _looks_like_server(config):
                raise MCPConfigError(
                    _("{} must contain a command or URL configuration.").format(name)
                )
            entries.append((name.strip(), config))
        return entries
    raise MCPConfigError(_("Expected an MCP server object, map, or array."))


def _entry_name(config) -> str | None:
    if not isinstance(config, dict):
        return None
    value = config.get("title") or config.get("name")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first(config: dict, *keys):
    for key in keys:
        if key in config and config[key] is not None:
            return config[key]
    return None


def _normalize_server(config, name: str | None) -> dict:
    if isinstance(config, str):
        url = config.strip()
        if not url:
            raise MCPConfigError(_("The server URL cannot be empty."))
        return {"type": "http", "url": url, "title": name}
    if not isinstance(config, dict):
        raise MCPConfigError(_("Each server must be an object or URL string."))

    values = dict(config)
    transport = values.get("transport")
    if isinstance(transport, dict):
        values = {**transport, **values}

    type_hint = values.get("type")
    if type_hint is None and isinstance(transport, str):
        type_hint = transport
    if type_hint is not None and not isinstance(type_hint, str):
        raise MCPConfigError(_("The server type must be a string."))
    normalized_type = type_hint.strip().lower() if type_hint else None

    command = _first(values, "command", "cmd")
    url = _first(values, "url", "endpoint", "serverUrl", "server_url")
    if normalized_type in _STDIO_TYPES:
        server_type = "stdio"
    elif normalized_type in _HTTP_TYPES:
        server_type = "http"
    elif normalized_type:
        raise MCPConfigError(_("Unsupported server type: {}").format(type_hint))
    elif command is not None and url is None:
        server_type = "stdio"
    elif url is not None and command is None:
        server_type = "http"
    elif command is not None and url is not None:
        raise MCPConfigError(
            _("Specify a server type when both command and URL are present.")
        )
    else:
        raise MCPConfigError(_("A command or URL is required."))

    title = _first(values, "title", "name") or name
    if title is not None and not isinstance(title, str):
        raise MCPConfigError(_("The server title must be a string."))
    title = title.strip() if title and title.strip() else None

    if server_type == "stdio":
        return _normalize_stdio_server(values, title, command)
    return _normalize_http_server(values, title, url)


def _normalize_stdio_server(values: dict, title: str | None, command) -> dict:
    if not isinstance(command, str) or not command.strip():
        raise MCPConfigError(_("A non-empty command is required for stdio servers."))

    args = values.get("args", [])
    if isinstance(args, str):
        try:
            args = shlex.split(args)
        except ValueError as exc:
            raise MCPConfigError(_("Arguments could not be parsed: {}").format(exc)) from exc
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise MCPConfigError(
            _("args must be an array of strings or a command-line string.")
        )

    env = _first(values, "env", "environment", "environmentVariables")
    if env is not None:
        _validate_string_map(env, "env")

    return {
        "type": "stdio",
        "title": title,
        "command": os.path.expanduser(command.strip()),
        "args": args,
        "env": env,
    }


def _normalize_http_server(values: dict, title: str | None, url) -> dict:
    if not isinstance(url, str) or not url.strip():
        raise MCPConfigError(_("A non-empty URL is required for HTTP servers."))

    headers = _first(values, "custom_headers", "customHeaders", "headers")
    if headers is not None:
        _validate_string_map(headers, "headers")

    bearer_token = _first(values, "bearer_token", "bearerToken", "token")
    client_id = _first(values, "client_id", "clientId")
    oauth_mode = _first(values, "oauth_mode", "oauthMode")
    auth = values.get("auth")
    if isinstance(auth, str):
        auth_type = auth.strip().lower()
        if auth_type == "oauth":
            oauth_mode = True
        elif auth_type not in {"", "none", "bearer"}:
            raise MCPConfigError(
                _("Unsupported authentication type: {}").format(auth)
            )
    elif auth is not None:
        if not isinstance(auth, dict):
            raise MCPConfigError(_("auth must be a string or object."))
        auth_type = str(auth.get("type", "none")).strip().lower()
        if auth_type == "oauth":
            oauth_mode = True
            client_id = client_id or _first(auth, "client_id", "clientId")
        elif auth_type == "bearer":
            bearer_token = bearer_token or auth.get("token")
        elif auth_type not in {"", "none"}:
            raise MCPConfigError(
                _("Unsupported authentication type: {}").format(auth_type)
            )

    if bearer_token is not None and not isinstance(bearer_token, str):
        raise MCPConfigError(_("The bearer token must be a string."))
    if client_id is not None and not isinstance(client_id, str):
        raise MCPConfigError(_("The client ID must be a string."))
    if oauth_mode is not None and not isinstance(oauth_mode, bool):
        raise MCPConfigError(_("oauth_mode must be a boolean."))

    return {
        "type": "http",
        "url": url.strip(),
        "title": title,
        "bearer_token": bearer_token or None,
        "client_id": client_id or None,
        "custom_headers": headers,
        "oauth_mode": bool(oauth_mode),
    }


def _validate_string_map(value, label: str) -> None:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise MCPConfigError(
            _("{} must be an object containing string values.").format(label)
        )

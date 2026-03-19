"""
OAuth 2.0 Dynamic Client Registration (RFC 7591) and Authorization Code + PKCE flow
for MCP HTTP servers. Implements discovery, DCR, and token management per MCP spec.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import urllib.request
import urllib.error

# Use httpx if available (from MCP), else fallback to urllib
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("utf-8")
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return code_verifier, code_challenge


def _get_credentials_path(config_dir: str) -> str:
    """Return path to MCP OAuth credentials file."""
    return os.path.join(config_dir, "mcp_oauth_credentials.json")


def _load_credentials(config_dir: str) -> dict[str, Any]:
    """Load stored OAuth credentials."""
    path = _get_credentials_path(config_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_credentials(config_dir: str, credentials: dict[str, Any]) -> None:
    """Persist OAuth credentials."""
    path = _get_credentials_path(config_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(credentials, f, indent=2)


def _credential_key(mcp_url: str) -> str:
    """Generate a stable key for credential lookup."""
    parsed = urlparse(mcp_url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return base


def _canonical_mcp_url(mcp_url: str) -> str:
    """Return the canonical MCP URL (no OAuth-only query params like ?login)."""
    parsed = urlparse(mcp_url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _parse_www_authenticate(header_value: str) -> dict[str, str]:
    """Parse WWW-Authenticate header (Bearer scheme with params)."""
    result = {}
    if not header_value or "Bearer" not in header_value:
        return result
    parts = header_value.split("Bearer", 1)[-1].strip().strip(",")
    for part in parts.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            v = v.strip('"').strip("'")
            result[k.strip()] = v
    return result


def _fetch_json(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> tuple[dict | None, int]:
    """Fetch JSON from URL. Returns (data, status_code)."""
    req_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if HAS_HTTPX:
        try:
            with httpx.Client(timeout=30.0) as client:
                if method == "GET":
                    r = client.get(url, headers=req_headers)
                else:
                    r = client.request(method, url, json=body or {}, headers=req_headers)
                if r.status_code >= 200 and r.status_code < 300:
                    return (r.json(), r.status_code)
                return (r.json() if r.headers.get("content-type", "").startswith("application/json") else None, r.status_code)
        except Exception:
            return (None, 0)
    else:
        req = urllib.request.Request(url, method=method, headers=req_headers)
        if body and method != "GET":
            req.data = json.dumps(body).encode("utf-8")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode()) if resp.length else {}
                return (data, resp.status)
        except Exception:
            return (None, 0)


def _post_form(url: str, form: dict[str, Any], headers: dict | None = None) -> tuple[dict | None, int]:
    """POST x-www-form-urlencoded and parse JSON response."""
    req_headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        req_headers.update(headers)

    data = urlencode(form).encode("utf-8")
    if HAS_HTTPX:
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(url, content=data, headers=req_headers)
                if r.status_code >= 200 and r.status_code < 300:
                    return (r.json(), r.status_code)
                return (r.json() if "application/json" in (r.headers.get("content-type") or "") else None, r.status_code)
        except Exception:
            return (None, 0)

    req = urllib.request.Request(url, data=data, method="POST", headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode()
            return (json.loads(payload) if payload else {}, resp.status)
    except Exception:
        return (None, 0)


def _fetch_json_async(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None):
    """Async fetch JSON. Used when running in asyncio context."""
    import asyncio
    if HAS_HTTPX:
        async def _do():
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    r = await client.get(url, headers=headers or {})
                else:
                    r = await client.request(method, url, json=body or {}, headers=headers or {})
                if r.status_code >= 200 and r.status_code < 300:
                    return (r.json(), r.status_code)
                return (r.json() if "application/json" in (r.headers.get("content-type") or "") else None, r.status_code)
        return asyncio.get_event_loop().run_until_complete(_do())
    return _fetch_json(url, method, body, headers)


def discover_auth(mcp_url: str) -> tuple[dict | None, str | None]:
    """
    Discover OAuth metadata from MCP server.
    Returns (auth_server_metadata, resource_metadata_url) or (None, None) on failure.
    """
    www_auth = ""
    if HAS_HTTPX:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for method, url, body in [
                ("GET", mcp_url, None),
                ("POST", mcp_url, {"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}}),
            ]:
                if method == "GET":
                    r = client.get(url, headers={"Accept": "application/json, text/event-stream"})
                else:
                    r = client.post(url, json=body, headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
                if r.status_code == 401:
                    www_auth = r.headers.get("WWW-Authenticate", "")
                    break
            if not www_auth:
                return (None, None)
    else:
        for method, url, body in [
            ("GET", mcp_url, None),
            ("POST", mcp_url, json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}})),
        ]:
            req = urllib.request.Request(url, data=body.encode() if body else None, method=method,
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"} if body else {"Accept": "application/json, text/event-stream"})
            try:
                urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    www_auth = e.headers.get("WWW-Authenticate", "")
                    break
            except Exception:
                pass
        if not www_auth:
            return (None, None)

    params = _parse_www_authenticate(www_auth)
    resource_metadata_url = params.get("resource_metadata")
    if not resource_metadata_url:
        parsed = urlparse(mcp_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/") or "/"
        for candidate in [
            f"{base}/.well-known/oauth-protected-resource{path}",
            f"{base}/.well-known/oauth-protected-resource",
        ]:
            data, status = _fetch_json(candidate)
            if data and status == 200:
                resource_metadata_url = candidate
                break
        if not resource_metadata_url:
            return (None, None)

    if not resource_metadata_url and params.get("resource_metadata"):
        resource_metadata_url = params["resource_metadata"]

    if not resource_metadata_url:
        parsed = urlparse(mcp_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        resource_metadata_url = f"{base}/.well-known/oauth-protected-resource"

    rs_meta, _ = _fetch_json(resource_metadata_url)
    if not rs_meta:
        return (None, resource_metadata_url)

    auth_servers = rs_meta.get("authorization_servers", [])
    if not auth_servers:
        return (None, resource_metadata_url)

    issuer = auth_servers[0] if isinstance(auth_servers[0], str) else auth_servers[0].get("url", "")
    if not issuer:
        return (None, resource_metadata_url)

    parsed_issuer = urlparse(issuer)
    path_part = parsed_issuer.path.strip("/") or ""
    candidates = []
    if path_part:
        candidates = [
            f"{parsed_issuer.scheme}://{parsed_issuer.netloc}/{path_part}/.well-known/openid-configuration",
            f"{parsed_issuer.scheme}://{parsed_issuer.netloc}/.well-known/openid-configuration/{path_part}",
            f"{parsed_issuer.scheme}://{parsed_issuer.netloc}/.well-known/oauth-authorization-server/{path_part}",
        ]
    else:
        candidates = [
            f"{issuer.rstrip('/')}/.well-known/openid-configuration",
            f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server",
        ]

    for candidate in candidates:
        as_meta, status = _fetch_json(candidate)
        if as_meta and status == 200:
            return (as_meta, resource_metadata_url)
    return (None, resource_metadata_url)


def register_client(
    registration_endpoint: str,
    redirect_uri: str,
    client_name: str = "Newelle MCP Client",
) -> dict | None:
    """
    Dynamic Client Registration (RFC 7591).
    Returns client registration response or None on failure.
    """
    body = {
        "redirect_uris": [redirect_uri],
        "client_name": client_name,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "openid",
    }
    data, status = _fetch_json(registration_endpoint, "POST", body)
    if status in (200, 201) and data:
        return data
    return None


def run_oauth_flow(
    mcp_url: str,
    config_dir: str,
    client_name: str = "Newelle MCP Client",
) -> tuple[bool, str]:
    """
    Run full OAuth flow: discovery -> DCR (if supported) -> Authorization Code + PKCE.
    Returns (success, error_message).
    """
    as_meta, resource_metadata_url = discover_auth(mcp_url)
    if not as_meta:
        return (False, "Could not discover OAuth metadata from MCP server")

    registration_endpoint = as_meta.get("registration_endpoint")
    authorization_endpoint = as_meta.get("authorization_endpoint")
    token_endpoint = as_meta.get("token_endpoint")
    scopes_supported = as_meta.get("scopes_supported", [])
    code_challenge_methods = as_meta.get("code_challenge_methods_supported", [])

    if "S256" not in code_challenge_methods:
        return (False, "Authorization server does not support PKCE S256")

    if not authorization_endpoint or not token_endpoint:
        return (False, "Invalid authorization server metadata")

    server = HTTPServer(("127.0.0.1", 0), lambda *a, **k: None)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    server.server_close()

    client_id = None
    client_secret = None
    if registration_endpoint:
        reg = register_client(registration_endpoint, redirect_uri, client_name)
        if reg:
            client_id = reg.get("client_id")
            client_secret = reg.get("client_secret")
        if not client_id:
            return (False, "Dynamic client registration failed")
    else:
        return (False, "Server does not support Dynamic Client Registration (no registration_endpoint)")

    code_verifier, code_challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    resource = _canonical_mcp_url(mcp_url)
    scope = " ".join(scopes_supported) if scopes_supported else "openid"
    if resource_metadata_url:
        rs_meta, _ = _fetch_json(resource_metadata_url)
        if rs_meta:
            if rs_meta.get("resource"):
                resource = rs_meta["resource"]
            rs_scopes = rs_meta.get("scopes_supported", [])
            if rs_scopes:
                scope = " ".join(rs_scopes)

    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    auth_url = f"{authorization_endpoint}?{urlencode(auth_params)}"

    result: dict[str, Any] = {"code": None, "state": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                qs = parse_qs(parsed.query)
                result["code"] = (qs.get("code") or [None])[0]
                result["state"] = (qs.get("state") or [None])[0]
                result["error"] = (qs.get("error") or [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><p>Authorization complete. You can close this window.</p></body></html>"
                )
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", port), CallbackHandler)
    try:
        try:
            from ..utility.system import open_website
            open_website(auth_url)
        except ImportError:
            import webbrowser
            webbrowser.open(auth_url)
        httpd.handle_request()
    finally:
        httpd.server_close()

    if result["error"]:
        return (False, f"Authorization error: {result['error']}")

    if result["state"] != state:
        return (False, "State mismatch - possible CSRF")

    code = result["code"]
    if not code:
        return (False, "No authorization code received")

    token_body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    token_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_secret:
        creds_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        token_headers["Authorization"] = f"Basic {creds_b64}"

    token_data = urlencode(token_body)
    if HAS_HTTPX:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                token_endpoint,
                content=token_data,
                headers={**token_headers, "Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                return (False, f"Token exchange failed: {r.status_code}")
            tok = r.json()
    else:
        req = urllib.request.Request(
            token_endpoint,
            data=token_data.encode("utf-8"),
            method="POST",
            headers={**token_headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                tok = json.loads(resp.read().decode())
        except Exception as e:
            return (False, str(e))

    access_token = tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    expires_in = tok.get("expires_in", 3600)

    if not access_token:
        return (False, "No access token in response")

    canonical_url = _canonical_mcp_url(mcp_url)
    key = _credential_key(mcp_url)
    creds = _load_credentials(config_dir)
    creds[key] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "token_endpoint": token_endpoint,
        "resource": resource,
        "canonical_url": canonical_url,
    }
    import time
    creds[key]["expires_at"] = time.time() + expires_in
    _save_credentials(config_dir, creds)
    return (True, "")


def get_valid_token(mcp_url: str, config_dir: str) -> str | None:
    """
    Get a valid access token for the MCP server, refreshing if needed.
    Returns token or None.
    """
    import time
    key = _credential_key(mcp_url)
    creds = _load_credentials(config_dir)
    entry = creds.get(key)
    if not entry:
        return None
    access_token = entry.get("access_token")
    expires_at = entry.get("expires_at", 0)
    if not access_token:
        return None
    if time.time() < expires_at - 60:
        return access_token
    refresh_token = entry.get("refresh_token")
    token_endpoint = entry.get("token_endpoint")
    if not refresh_token or not token_endpoint:
        return access_token
    client_id = entry.get("client_id")
    client_secret = entry.get("client_secret")
    resource = entry.get("resource", mcp_url.rstrip("/"))
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    }
    headers = {}
    if client_secret:
        cred = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    data, status = _post_form(token_endpoint, body, headers)
    if status != 200 or not data:
        return access_token
    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)
    if new_access:
        entry["access_token"] = new_access
        entry["refresh_token"] = new_refresh
        entry["expires_at"] = time.time() + expires_in
        _save_credentials(config_dir, creds)
        return new_access
    return access_token


def clear_oauth_credentials(mcp_url: str, config_dir: str) -> None:
    """Remove stored credentials for an MCP server."""
    key = _credential_key(mcp_url)
    creds = _load_credentials(config_dir)
    if key in creds:
        del creds[key]
        _save_credentials(config_dir, creds)

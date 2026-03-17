from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult 
import threading 
import json 
import os
import time


class _CachedTool:
    """Lightweight stand-in for an MCP SDK tool object loaded from the cache."""

    __slots__ = ("name", "description", "inputSchema")

    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class MCPIntegration(NewelleExtension):
    id = "mcp"
    name = "MCP"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.mcp_servers = json.loads(self.settings.get_string("mcp-servers"))
        self.tools = []
        self.tools_dict = {}  # Maps tool_name -> server_info dict
        self.stdio_sessions = {}  # Keep stdio sessions alive
        self._cache_path = os.path.join(extension_path, "mcp_tool_cache.json")

        if self._load_from_cache():
            # Cache hit -- refresh in background so next startup stays fresh
            threading.Thread(target=self._background_refresh, daemon=True).start()
        else:
            self.update_tools()

    def _get_config_dir(self):
        """Config dir is parent of extension_path (extension_path is config_dir/extensions)."""
        return os.path.dirname(self.extension_path)

    def _get_server_info(self, server):
        """Extract server info from both old (string) and new (dict) formats"""
        if isinstance(server, str):
            return {
                "type": "http",
                "url": server,
                "title": None,
                "bearer_token": None,
                "client_id": None,
                "custom_headers": None,
                "oauth_mode": False,
                "command": None,
                "args": None,
                "env": None
            }
        return {
            "type": server.get("type", "http"),
            "url": server.get("url", ""),
            "title": server.get("title"),
            "bearer_token": server.get("bearer_token"),
            "client_id": server.get("client_id"),
            "custom_headers": server.get("custom_headers"),
            "oauth_mode": server.get("oauth_mode", False),
            "command": server.get("command"),
            "args": server.get("args"),
            "env": server.get("env")
        }

    def _get_mcp_url_for_request(self, server_info):
        """Return the URL to use for MCP requests. For OAuth servers, use canonical URL (no ?login)."""
        if not server_info:
            return ""
        url = server_info.get("url", "")
        if server_info.get("oauth_mode") and url:
            from .mcp_oauth import _canonical_mcp_url
            return _canonical_mcp_url(url)
        return url

    def _build_headers(self, bearer_token=None, custom_headers=None, server_info=None):
        """Build headers dict combining bearer token and custom headers.
        When server_info has oauth_mode=True, resolve token from OAuth credentials store.
        """
        headers = {}
        token = bearer_token
        if server_info and server_info.get("oauth_mode"):
            from .mcp_oauth import get_valid_token
            url = server_info.get("url", "")
            if url:
                token = get_valid_token(url, self._get_config_dir())
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if custom_headers and isinstance(custom_headers, dict):
            headers.update(custom_headers)
        return headers

    def _get_server_identifier(self, server_info):
        """Get a unique identifier for a server"""
        if server_info.get("type") == "stdio":
            return f"stdio:{server_info.get('command')}:{':'.join(server_info.get('args', []))}"
        return server_info.get("url", "")

    # --- Tool metadata cache ---

    def _load_from_cache(self) -> bool:
        """Populate self.tools and self.tools_dict from the on-disk cache.

        Returns True if at least one server's tools were restored.
        """
        if not os.path.exists(self._cache_path):
            return False
        try:
            with open(self._cache_path, "r") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False

        loaded_any = False
        for server in self.mcp_servers:
            server_info = self._get_server_info(server)
            identifier = self._get_server_identifier(server_info)
            entry = cache.get(identifier)
            if not entry or "tools" not in entry:
                continue
            for td in entry["tools"]:
                stub = _CachedTool(td["name"], td.get("description", ""), td.get("inputSchema", {}))
                self.tools.append(stub)
                self.tools_dict[stub.name] = server_info
            loaded_any = True

        return loaded_any

    def _save_cache(self):
        """Persist current tool metadata so future startups can skip connections."""
        cache: dict = {}
        # Group tools by server identifier
        for tool in self.tools:
            server_info = self.tools_dict.get(tool.name, {})
            identifier = self._get_server_identifier(server_info)
            if identifier not in cache:
                cache[identifier] = {"tools": [], "cached_at": time.time()}
            schema = tool.inputSchema if hasattr(tool, "inputSchema") else {}
            cache[identifier]["tools"].append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": schema,
            })
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, "w") as f:
                json.dump(cache, f)
        except OSError as e:
            print(f"MCP cache write error: {e}")

    def _background_refresh(self):
        """Re-fetch tools from all servers and update the cache."""
        old_tools = self.tools
        old_dict = self.tools_dict
        self.tools = []
        self.tools_dict = {}
        self.async_get_tools()
        if not self.tools:
            # Restore from previous state if refresh fails entirely
            self.tools = old_tools
            self.tools_dict = old_dict

    def add_mcp_server(self, url=None, title=None, bearer_token=None, client_id=None, custom_headers=None,
                       server_type="http", command=None, args=None, env=None, oauth_mode=False):
        try:
            if server_type == "stdio":
                if not command:
                    return False
                tools = self.sync_get_tools_stdio(command, args or [], env)
                server_info = {
                    "type": "stdio",
                    "title": title,
                    "command": command,
                    "args": args or [],
                    "env": env
                }
            else:
                if not url:
                    return False
                server_info = {
                    "type": "http",
                    "url": url,
                    "title": title,
                    "bearer_token": bearer_token,
                    "client_id": client_id,
                    "custom_headers": custom_headers,
                    "oauth_mode": oauth_mode
                }
                tools = self.sync_get_tools(url, server_info=server_info, client_id=client_id)
            
            self.tools.extend(tools)
            for tool in tools:
                self.tools_dict[tool.name] = server_info
            self.ui_controller.require_tool_update()
        except Exception as e:
            raise
        
        self.mcp_servers.append(server_info)
        return True

    def remove_mcp_server(self, identifier):
        """Remove server by URL (http) or command identifier (stdio)"""
        server_to_remove = None
        for server in self.mcp_servers:
            if isinstance(server, str):
                server_url = server
            else:
                server_info = self._get_server_info(server)
                server_url = self._get_server_identifier(server_info)
            if server_url == identifier:
                server_to_remove = server
                if isinstance(server, dict) and server.get("oauth_mode"):
                    from .mcp_oauth import clear_oauth_credentials
                    clear_oauth_credentials(server.get("url", ""), self._get_config_dir())
                break
        if server_to_remove:
            self.mcp_servers.remove(server_to_remove)
        self.tools = []
        self.tools_dict = {}
        self.update_tools()
        self._save_cache()
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return True

    def update_tools(self):
        t = threading.Thread(target=self.async_get_tools)
        t.start()

    def async_get_tools(self) -> list:
        for server in self.mcp_servers:
            server_info = self._get_server_info(server)
            identifier = self._get_server_identifier(server_info)
            print(f"Loading tools from: {identifier}")
            try:
                if server_info.get("type") == "stdio":
                    tools = self.sync_get_tools_stdio(
                        server_info["command"], 
                        server_info.get("args") or [], 
                        server_info.get("env")
                    )
                else:
                    tools = self.sync_get_tools(
                        server_info["url"],
                        server_info=server_info,
                        client_id=server_info.get("client_id")
                    )
                print(tools)
                self.tools.extend(tools)
                for tool in tools:
                    self.tools_dict[tool.name] = server_info
            except Exception as e:
                print(f"Error fetching tools from {identifier}: {e}")
        self._save_cache()
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return self.tools

    def execute_tool(self, name) -> str:
        return lambda **arguments : self.execute_tool_name(name, **arguments)

    def execute_tool_name(self, tool_name: str, **arguments) -> str:
        result = ToolResult()
        def get_answer():
            server_info = self.tools_dict.get(tool_name, {})
            if not server_info:
                result.set_output("Error: Tool server not found")
                return
            
            if server_info.get("type") == "stdio":
                command = server_info.get("command")
                args = server_info.get("args") or []
                env = server_info.get("env")
                if not command:
                    result.set_output("Error: Stdio server command not found")
                    return
                out = self.sync_call_tool_stdio(command, args, env, tool_name, arguments)
            else:
                url = server_info.get("url")
                if not url:
                    result.set_output("Error: HTTP server URL not found")
                    return
                out = self.sync_call_tool(
                    url, tool_name, arguments,
                    server_info=server_info,
                    client_id=server_info.get("client_id")
                )
            result.set_output(out)
        t = threading.Thread(target=get_answer)
        t.start()
        return result

    def _tool_search(self, tool_name: str) -> ToolResult:
        """Meta-tool: return the full parameter schema for a given tool."""
        result = ToolResult()
        if hasattr(self, "ui_controller") and self.ui_controller is not None:
            controller = self.ui_controller.window.controller
            result.set_output(controller.tools.get_tool_schema(tool_name))
        else:
            result.set_output(json.dumps({"error": "Controller not available"}))
        return result

    def get_tools(self) -> list:
        tools = []
        for tool in self.tools:
            server_info = self.tools_dict.get(tool.name, {})
            tools_group = server_info.get("title") or server_info.get("url", "MCP")
            tools.append(Tool(
                tool.name, tool.description, self.execute_tool(tool.name),
                tool.inputSchema, tools_group=tools_group, default_lazy_load=True,
            ))
        if tools:
            tool_search = Tool(
                "tool_search",
                "Get the full parameter schema for a tool. Call this before using any tool that is listed without parameters.",
                lambda tool_name: self._tool_search(tool_name),
                schema={
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "The name of the tool to look up",
                        }
                    },
                    "required": ["tool_name"],
                },
                tools_group="MCP",
                default_lazy_load=False,
                icon_name="system-search-symbolic",
            )
            tools.append(tool_search)
        return tools

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        print(codeblock) 
        js = codeblock
        print(js)
        call = json.loads(js)
        print(call)
        if "tool" not in call:
            return "Missing tool name"
        tool_name = call["tool"]
        args = call["arguments"]
        result = self.sync_call_tool(tool_name, args)
        return result

    def sync_get_tools(self, url, headers=None, client_id=None, server_info=None):
        """Synchronous wrapper to get available tools (HTTP)"""
        import asyncio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        
        if headers is None:
            headers = {}
        resolved_headers = self._build_headers(
            server_info.get("bearer_token") if server_info else None,
            server_info.get("custom_headers") if server_info else headers,
            server_info
        )
        request_url = (self._get_mcp_url_for_request(server_info) or url) if server_info else url
        
        async def _async_get_tools():
            client_kwargs = {"url": request_url, "headers": resolved_headers}
            async with streamablehttp_client(**client_kwargs) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools.tools
        return asyncio.run(_async_get_tools())

    def sync_call_tool(self, url, tool_name, arguments, headers=None, client_id=None, server_info=None):
        """Synchronous wrapper to call a tool"""
        import asyncio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        
        if headers is None:
            headers = {}
        resolved_headers = self._build_headers(
            server_info.get("bearer_token") if server_info else None,
            server_info.get("custom_headers") if server_info else headers,
            server_info
        )
        request_url = (self._get_mcp_url_for_request(server_info) or url) if server_info else url
        
        async def _async_call_tool():
            client_kwargs = {"url": request_url, "headers": resolved_headers}
            async with streamablehttp_client(**client_kwargs) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return result
        return asyncio.run(_async_call_tool())

    def sync_get_tools_stdio(self, command, args=None, env=None):
        """Synchronous wrapper to get available tools from stdio server"""
        import asyncio
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp import ClientSession
        
        if args is None:
            args = []
        
        async def _async_get_tools():
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env
            )
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools.tools
        
        return asyncio.run(_async_get_tools())

    def sync_call_tool_stdio(self, command, args, env, tool_name, arguments):
        """Synchronous wrapper to call a tool on stdio server"""
        import asyncio
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp import ClientSession
        
        if args is None:
            args = []
        
        async def _async_call_tool():
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env
            )
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return result
        
        return asyncio.run(_async_call_tool())

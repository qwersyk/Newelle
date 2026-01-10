from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult 
import threading 
import json 

class MCPIntegration(NewelleExtension):
    id = "mcp"
    name = "MCP"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.mcp_servers = json.loads(self.settings.get_string("mcp-servers"))
        self.tools = []
        self.tools_dict = {}  # Maps tool_name -> server_info dict
        self.update_tools()

    def _get_server_info(self, server):
        """Extract server info from both old (string) and new (dict) formats"""
        if isinstance(server, str):
            return {"url": server, "title": None, "bearer_token": None, "client_id": None, "custom_headers": None}
        return {
            "url": server.get("url", ""),
            "title": server.get("title"),
            "bearer_token": server.get("bearer_token"),
            "client_id": server.get("client_id"),
            "custom_headers": server.get("custom_headers")
        }

    def _build_headers(self, bearer_token=None, custom_headers=None):
        """Build headers dict combining bearer token and custom headers"""
        headers = {}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        if custom_headers and isinstance(custom_headers, dict):
            headers.update(custom_headers)
        return headers

    def add_mcp_server(self, url, title=None, bearer_token=None, client_id=None, custom_headers=None):
        try:
            headers = self._build_headers(bearer_token, custom_headers)
            tools = self.sync_get_tools(url, headers=headers, client_id=client_id)
            server_info = {"url": url, "title": title, "bearer_token": bearer_token, "client_id": client_id, "custom_headers": custom_headers}
            self.tools.extend(tools)
            for tool in tools:
                self.tools_dict[tool.name] = server_info
            self.ui_controller.require_tool_update()
        except Exception as e:
            print(e)
            return False
        # Store as new format dict
        self.mcp_servers.append({"url": url, "title": title, "bearer_token": bearer_token, "client_id": client_id, "custom_headers": custom_headers})
        return True

    def remove_mcp_server(self, url):
        # Find and remove server by URL (handle both formats)
        server_to_remove = None
        for server in self.mcp_servers:
            server_url = server if isinstance(server, str) else server.get("url", "")
            if server_url == url:
                server_to_remove = server
                break
        if server_to_remove:
            self.mcp_servers.remove(server_to_remove)
        self.tools = []
        self.tools_dict = {}
        self.update_tools()
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return True

    def update_tools(self):
        t = threading.Thread(target=self.async_get_tools)
        t.start()

    def async_get_tools(self) -> list:
        for server in self.mcp_servers:
            server_info = self._get_server_info(server)
            print(server_info["url"])
            try:
                headers = self._build_headers(server_info["bearer_token"], server_info["custom_headers"])
                tools = self.sync_get_tools(server_info["url"], headers=headers, client_id=server_info["client_id"])
                print(tools)
                self.tools.extend(tools)
                for tool in tools:
                    self.tools_dict[tool.name] = server_info
            except Exception as e:
                print(f"Error fetching tools from {server_info['url']}: {e}")
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return self.tools

    def execute_tool(self, name) -> str:
        return lambda **arguments : self.execute_tool_name(name, **arguments)

    def execute_tool_name(self, tool_name: str, **arguments) -> str:
        result = ToolResult()
        def get_answer():
            server_info = self.tools_dict.get(tool_name, {})
            url = server_info.get("url") if server_info else None
            if not url:
                result.set_output("Error: Tool server not found")
                return
            headers = self._build_headers(server_info.get("bearer_token"), server_info.get("custom_headers"))
            client_id = server_info.get("client_id")
            out = self.sync_call_tool(url, tool_name, arguments, headers=headers, client_id=client_id)
            result.set_output(out)
        t = threading.Thread(target=get_answer)
        t.start()
        return result

    def get_tools(self) -> list:
        tools = []
        for tool in self.tools:
            # Get server info to use title as tools_group
            server_info = self.tools_dict.get(tool.name, {})
            tools_group = server_info.get("title") or server_info.get("url", "MCP")
            tools.append(Tool(tool.name, tool.description, self.execute_tool(tool.name), tool.inputSchema, tools_group=tools_group))
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

    def sync_get_tools(self, url, headers=None, client_id=None):
        """Synchronous wrapper to get available tools"""
        import asyncio
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        
        if headers is None:
            headers = {}
        
        async def _async_get_tools():
            client_kwargs = {"url": url, "headers": headers}
            if client_id:
                client_kwargs["client_id"] = client_id
            async with streamablehttp_client(**client_kwargs) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools.tools 
        return asyncio.run(_async_get_tools())

    def sync_call_tool(self, url, tool_name, arguments, headers=None, client_id=None):
        """Synchronous wrapper to call a tool"""
        import asyncio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        
        if headers is None:
            headers = {}
        
        async def _async_call_tool():
            client_kwargs = {"url": url, "headers": headers}
            if client_id:
                client_kwargs["client_id"] = client_id
            async with streamablehttp_client(**client_kwargs) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return result
        
        return asyncio.run(_async_call_tool())

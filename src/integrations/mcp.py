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
        self.tools_dict = {}
        self.update_tools()

    def add_mcp_server(self, url):
        try:
            tools = self.sync_get_tools(url)
            self.tools.extend(tools)
            for tool in tools:
                self.tools_dict[tool.name] = url
            self.ui_controller.require_tool_update()
        except Exception as e:
            print(e)
            return False
        self.mcp_servers.append(url)
        return True

    def remove_mcp_server(self, url):
        self.mcp_servers.remove(url)
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
        for url in self.mcp_servers:
            print(url)
            tools = self.sync_get_tools(url)
            print(tools)
            self.tools.extend(tools)
            for tool in tools:
                self.tools_dict[tool.name] = url
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return self.tools

    def execute_tool(self, name) -> str:
        return lambda **arguments : self.execute_tool_name(name, **arguments)

    def execute_tool_name(self, tool_name: str, **arguments) -> str:
        result = ToolResult()
        def get_answer():
            out = self.sync_call_tool(self.mcp_servers[0], tool_name, arguments)
            result.set_output(out)
        t = threading.Thread(target=get_answer)
        t.start()
        return result

    def get_tools(self) -> list:
        tools = []
        for tool in self.tools:
            tools.append(Tool(tool.name, tool.description, self.execute_tool(tool.name), tool.inputSchema))
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

    def sync_get_tools(self, url):
        """Synchronous wrapper to get available tools"""
        import asyncio
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async def _async_get_tools():
            async with streamablehttp_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools.tools 
        return asyncio.run(_async_get_tools())

    def sync_call_tool(self,url, tool_name, arguments):
        """Synchronous wrapper to call a tool"""
        import asyncio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async def _async_call_tool():
            async with streamablehttp_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return result
        
        return asyncio.run(_async_call_tool())

from typing import Any, Callable, Dict, List, Optional
import inspect
import threading
import json
from gi.repository import GLib


class InteractionOption:
    def __init__(self, title:str, callback) -> None:
        self.title = title 
        self.callback = callback 

class ToolResult:
    """
    Result returned by a tool execution.
    
    Attributes:
        output (Any): The textual/data output to be returned to the LLM (and displayed in Console).
        widget (Any): Optional GTK Widget to be displayed in the chat UI.
        requires_interaction (bool): Flag indicating if this tool requires user interaction.
    """
    output: Any = None
    widget: Any = None
    is_cancelled: bool = False
    requires_interaction: bool = False
    interaction_options : list
    display_text : str | None 
    output_semaphore : threading.Semaphore

    def __init__(self, output=None, widget=None, requires_interaction=False, interaction_options: list=[], display_text: str | None = None) -> None:
        self.output = output 
        self.widget = widget
        self.display_text = display_text
        self.is_cancelled = False
        self.requires_interaction = requires_interaction
        self.output_semaphore = threading.Semaphore()
        self.output_semaphore.acquire()
        self.interaction_options = interaction_options

    def get_output(self):
        self.output_semaphore.acquire()
        self.output_semaphore.release()
        return self.output

    def cancel(self):
        self.is_cancelled = True
        self.set_output(None)

    def set_widget(self, widget):
        self.widget = widget
    
    def set_output(self, output):
        self.output = output
        try:
            self.output_semaphore.release()
        except ValueError:
            # Semaphore already released
            pass
    
    def set_display_text(self, text:str|None):
        self.display_text = text

    def set_intreaction_options(self, interaction_options : list = []):
        self.interaction_options = interaction_options


class Command:
    """Represents a slash command that can be executed from the chat input."""
    
    def __init__(
        self,
        name: str,
        description: str,
        func: Callable,
        icon_name: str = None,
        schema: Dict[str, Any] = None,
        restore_func: Callable = None,
    ):
        self.name = name
        self.description = description
        self.func = func
        self.icon_name = icon_name or "applications-utilities-symbolic"
        self.schema = schema or self._generate_schema_from_func(func)
        self.command = name.lower()
        self.restore_func = restore_func

    def _generate_schema_from_func(self, func: Callable) -> Dict[str, Any]:
        sig = inspect.signature(func)
        params = {}
        required = []
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            param_type = "string"
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == bool:
                param_type = "boolean"
            elif param.annotation == float:
                param_type = "number"
            elif param.annotation == list:
                param_type = "array"
            elif param.annotation == dict:
                param_type = "object"
            
            params[name] = {"type": param_type}
            if param.default == inspect.Parameter.empty:
                required.append(name)
        
        return {
            "type": "object",
            "properties": params,
            "required": required
        }

    def execute(self, **kwargs):
        sig = inspect.signature(self.func)
        for param in ['msg_uuid', 'tool_uuid', 'chat_id']:
            if param not in sig.parameters and 'kwargs' not in str(sig.parameters):
                kwargs.pop(param, None)
        return self.func(**kwargs)

    def restore(self, **kwargs):
        func_to_call = self.restore_func if self.restore_func is not None else self.func
        sig = inspect.signature(func_to_call)
        for param in ['msg_uuid', 'tool_uuid', 'chat_id']:
            if param not in sig.parameters and 'kwargs' not in str(sig.parameters):
                kwargs.pop(param, None)
        return func_to_call(**kwargs)

class Tool:
    def __init__(self, name: str, description: str, func: Callable, schema: Dict[str, Any] = None, run_on_main_thread: bool = False, title: str = None, prompt_editable: bool = True, restore_func: Callable = None, default_on: bool = True, tools_group: str = None, icon_name: str = None, default_lazy_load: bool = False):
        self.name = name
        self.description = description
        self.func = func
        self.schema = schema or self._generate_schema_from_func(func)
        self.run_on_main_thread = run_on_main_thread
        self.title = title or name.replace("_", " ").title()
        self.prompt_editable = prompt_editable
        self.restore_func = restore_func
        self.default_on = default_on
        self.tools_group = tools_group
        self.icon_name = icon_name
        self.default_lazy_load = default_lazy_load

    def restore(self, **kwargs):
        if self.restore_func is not None:
            # Filter out internal parameters if restore_func doesn't accept them
            sig = inspect.signature(self.restore_func)
            for param in ['msg_uuid', 'tool_uuid', 'chat_id']:
                if param not in sig.parameters and 'kwargs' not in str(sig.parameters):
                    kwargs.pop(param, None)
            return self.restore_func(**kwargs)
        t = ToolResult()
        t.set_output(None)
        return t

    def _generate_schema_from_func(self, func: Callable) -> Dict[str, Any]:
        # Basic schema generation (can be improved)
        sig = inspect.signature(func)
        params = {}
        required = []
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            param_type = "string"
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == bool:
                param_type = "boolean"
            elif param.annotation == float:
                param_type = "number"
            elif param.annotation == list:
                param_type = "array"
            elif param.annotation == dict:
                param_type = "object"
            
            params[name] = {"type": param_type}
            if param.default == inspect.Parameter.empty:
                required.append(name)
        
        return {
            "type": "object",
            "properties": params,
            "required": required
        }

    def execute(self, **kwargs):
        sig = inspect.signature(self.func)
        # Filter out internal parameters if function doesn't accept them
        for param in ['msg_uuid', 'tool_uuid', 'chat_id']:
            if param not in sig.parameters:
                kwargs.pop(param, None)
        return self.func(**kwargs)

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register_tool(self, tool: Tool):
        self._tools[tool.name] = tool
    
    def remove_tool(self, tool_name):
        del self._tools[tool_name]

    def get_tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def get_all_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Tool '{name}' not found")
        return tool.execute(**arguments)
    
    def get_tool_schema(self, tool_name: str) -> str:
        """Return the full JSON schema definition for a single tool.

        Args:
            tool_name: Name of the tool to look up.

        Returns:
            JSON string with the tool's name, description and parameters,
            or an error message if the tool is not found.
        """
        tool_obj = self._tools.get(tool_name)
        if not tool_obj:
            return json.dumps({"error": f"Tool '{tool_name}' not found"})
        tool_def = {
            "name": tool_obj.name,
            "description": tool_obj.description,
            "parameters": tool_obj.schema,
        }
        return json.dumps(tool_def, indent=2)

    def get_tools_prompt(self, enabled_tools_dict: dict[str, bool] = None, tools_settings: dict = None) -> str:
        """
        Generates the system prompt instructions for using the available tools.
        
        Tools with lazy loading enabled (per-tool setting or default_lazy_load)
        are emitted in compact form (name + description only, no parameters).
        The LLM should call ``tool_search`` to retrieve the full schema before
        invoking a compact tool.

        Args:
            enabled_tools_dict: Dictionary mapping tool names to boolean enabled state. 
                                If None, all registered tools are considered enabled.
            tools_settings: Dictionary containing tool settings (including custom prompts
                            and per-tool lazy_load overrides).
        """
        
        available_tools = []
        for tool_name, tool_obj in self._tools.items():
            is_enabled = tool_obj.default_on
            if enabled_tools_dict is not None:
                is_enabled = enabled_tools_dict.get(tool_name, tool_obj.default_on)
            
            if is_enabled:
                tool_def = None
                if tools_settings and tool_name in tools_settings and tools_settings[tool_name].get("custom_prompt"):
                     try:
                         tool_def = json.loads(tools_settings[tool_name]["custom_prompt"])
                     except:
                         pass

                if not tool_def:
                    is_lazy = tool_obj.default_lazy_load
                    if tools_settings and tool_name in tools_settings and "lazy_load" in tools_settings[tool_name]:
                        is_lazy = tools_settings[tool_name]["lazy_load"]

                    if is_lazy:
                        tool_def = {
                            "name": tool_obj.name,
                            "description": tool_obj.description,
                        }
                    else:
                        tool_def = {
                            "name": tool_obj.name,
                            "description": tool_obj.description,
                            "parameters": tool_obj.schema,
                        }
                available_tools.append(tool_def)
        
        if not available_tools:
            return ""

        tools_json = json.dumps(available_tools, indent=2)
        return f"<tools>\n{tools_json}\n</tools>"


def tool(name: str, description: str, run_on_main_thread: bool = False, title: str = None, prompt_editable: bool = True, restore_func: Callable = None, default_on: bool = True, tools_group: str = None, icon_name: str = None):
    """Decorator to register a function as a tool."""
    def decorator(func):
        t = Tool(name, description, func, run_on_main_thread=run_on_main_thread, title=title, prompt_editable=prompt_editable, restore_func=restore_func, default_on=default_on, tools_group=tools_group, icon_name=icon_name)
        return t
    return decorator

def create_io_tool(name: str, description: str, func: Callable, title: str = None, create_separate_process=False, default_on: bool = True, tools_group: str = None, icon_name: str = None, default_lazy_load: bool = False) -> Tool:
    def wrapper(**kwargs):
        result = ToolResult()
        def th():
            result.set_output(func(**kwargs))
        t = threading.Thread(target=th)
        GLib.idle_add(t.start)
        return result

    t = Tool(name, description, wrapper, title=title, default_on=default_on, tools_group=tools_group, icon_name=icon_name, restore_func=None, default_lazy_load=default_lazy_load)
    schema = t._generate_schema_from_func(func)
    t.schema = schema
    return t

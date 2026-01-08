from typing import Any, Callable, Dict, List, Optional
import inspect
import threading
import json
from gi.repository import GLib

class ToolResult:
    """
    Result returned by a tool execution.
    
    Attributes:
        output (Any): The textual/data output to be returned to the LLM (and displayed in Console).
        widget (Any): Optional GTK Widget to be displayed in the chat UI.
    """
    output: Any = None
    widget: Any = None
    output_semaphore : threading.Semaphore

    def __init__(self, output=None, widget=None) -> None:
        self.output = output 
        self.widget = output
        self.output_semaphore = threading.Semaphore()
        self.output_semaphore.acquire()

    def get_output(self):
        self.output_semaphore.acquire()
        self.output_semaphore.release()
        return self.output

    def set_widget(self, widget):
        self.widget = widget

    def set_output(self, output):
        self.output = output
        self.output_semaphore.release() 


class Tool:
    def __init__(self, name: str, description: str, func: Callable, schema: Dict[str, Any] = None, run_on_main_thread: bool = False, title: str = None, prompt_editable: bool = True, restore_func: Callable = None, default_on: bool = True):
        self.name = name
        self.description = description
        self.func = func
        self.schema = schema or self._generate_schema_from_func(func)
        self.run_on_main_thread = run_on_main_thread
        self.title = title or name.replace("_", " ").title()
        self.prompt_editable = prompt_editable
        self.restore_func = restore_func
        self.default_on = default_on

    def restore(self, **kwargs):
        if self.restore_func is not None:
            # Filter out internal parameters if restore_func doesn't accept them
            sig = inspect.signature(self.restore_func)
            for param in ['msg_id', 'tool_uuid']:
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
        for param in ['msg_id', 'tool_uuid']:
            if param not in sig.parameters and 'kwargs' not in str(sig.parameters):
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
    
    def get_tools_prompt(self, enabled_tools_dict: dict[str, bool] = None, tools_settings: dict = None) -> str:
        """
        Generates the system prompt instructions for using the available tools.
        
        Args:
            enabled_tools_dict: Dictionary mapping tool names to boolean enabled state. 
                                If None, all registered tools are considered enabled.
            tools_prompt_template: The template string for the tool instructions. 
                                   Must contain {TOOLS_JSON}.
            tools_settings: Dictionary containing tool settings (including custom prompts).
        """
        
        available_tools = []
        for tool_name, tool_obj in self._tools.items():
            # If enabled_tools_dict is provided, check if the tool is explicitly disabled (False)
            # Default to tool's default_on value if not in the dictionary
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
                    tool_def = {
                        "name": tool_obj.name,
                        "description": tool_obj.description,
                        "parameters": tool_obj.schema
                    }
                available_tools.append(tool_def)
        
        if not available_tools:
            return ""

        tools_json = json.dumps(available_tools, indent=2)
        return tools_json


def tool(name: str, description: str, run_on_main_thread: bool = False, title: str = None, prompt_editable: bool = True, restore_func: Callable = None, default_on: bool = True):
    """Decorator to register a function as a tool."""
    def decorator(func):
        t = Tool(name, description, func, run_on_main_thread=run_on_main_thread, title=title, prompt_editable=prompt_editable, restore_func=restore_func, default_on=default_on)
        return t
    return decorator

def create_io_tool(name: str, description: str, func: Callable, title: str = None, create_separate_process=False, default_on: bool = True) -> Tool:
    def wrapper(**kwargs):
        result = ToolResult()
        def th():
            result.set_output(func(**kwargs))
        t = threading.Thread(target=th)
        GLib.idle_add(t.start)
        return result

    t = Tool(name, description, wrapper, title=title, default_on=default_on)
    schema = t._generate_schema_from_func(func)
    t.schema = schema
    return t

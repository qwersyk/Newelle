from typing import Any, Callable, Dict, List, Optional
import inspect
from dataclasses import dataclass
import threading

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
    def __init__(self, name: str, description: str, func: Callable, schema: Dict[str, Any] = None, run_on_main_thread: bool = False, title: str = None, prompt_editable: bool = True, restore_func: Callable = None):
        self.name = name
        self.description = description
        self.func = func
        self.schema = schema or self._generate_schema_from_func(func)
        self.run_on_main_thread = run_on_main_thread
        self.title = title or name.replace("_", " ").title()
        self.prompt_editable = prompt_editable
        self.restore_func = restore_func

    def restore(self, **kwargs):
        if self.restore_func:
            return self.restore_func(**kwargs)
        # Fallback to func, removing internal keys
        clean_kwargs = {k: v for k, v in kwargs.items() if k != 'msg_id'}
        return self.func(**clean_kwargs)

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
        if 'msg_id' not in sig.parameters and 'kwargs' not in sig.parameters:
            if 'msg_id' in kwargs:
                del kwargs['msg_id']
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
    
    def get_tools_prompt(self, enabled_tools_dict: dict[str, bool] = None, tools_prompt_template: str = "") -> str:
        """
        Generates the system prompt instructions for using the available tools.
        
        Args:
            enabled_tools_dict: Dictionary mapping tool names to boolean enabled state. 
                                If None, all registered tools are considered enabled.
            tools_prompt_template: The template string for the tool instructions. 
                                   Must contain {TOOLS_JSON}.
        """
        
        available_tools = []
        for tool_name, tool_obj in self._tools.items():
            # If enabled_tools_dict is provided, check if the tool is explicitly disabled (False)
            # Default to True if not in the dictionary (new tools are enabled by default)
            is_enabled = True
            if enabled_tools_dict is not None:
                is_enabled = enabled_tools_dict.get(tool_name, True)
            
            if is_enabled:
                tool_def = {
                    "name": tool_obj.name,
                    "description": tool_obj.description,
                    "parameters": tool_obj.schema
                }
                available_tools.append(tool_def)
        
        if not available_tools:
            return ""

        import json
        tools_json = json.dumps(available_tools, indent=2)
        
        if not tools_prompt_template or "{TOOLS_JSON}" not in tools_prompt_template:
             # Fallback default prompt if template is missing or invalid
             prompt = """\n\n# Tools\n\nYou have access to the following tools. To use a tool, you MUST use the following JSON format:\n\n{"tool": "tool_name", "arguments": {"arg_name": "arg_value"}}\n\nAvailable Tools:\n\n"""
             prompt += tools_json
             prompt += "\n\nWhen you use a tool, the system will execute it and provide the result in the next message."
             return prompt
        
        return tools_prompt_template.replace("{TOOLS_JSON}", tools_json)

# Global registry instance
global_tool_registry = ToolRegistry()

def tool(name: str, description: str, run_on_main_thread: bool = False, title: str = None, prompt_editable: bool = True, restore_func: Callable = None):
    """Decorator to register a function as a tool."""
    def decorator(func):
        t = Tool(name, description, func, run_on_main_thread=run_on_main_thread, title=title, prompt_editable=prompt_editable, restore_func=restore_func)
        global_tool_registry.register_tool(t)
        return func
    return decorator

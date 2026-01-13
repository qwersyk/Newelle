import re
from unittest import result
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult 
import threading 
import json 
from ..ui.widgets import CopyBox
import subprocess
from ..utility.system import is_flatpak


class DefaultToolsIntegration(NewelleExtension):
    id = "default_tools"
    name = "Default Tools"

    def _truncate(self, text: str) -> str:
        maxlength = 4000
        if len(text) > maxlength:
            return text[:maxlength] + f"\n... (Output truncated to {maxlength} characters)"
        return text
    
    def execute_command(self, command: str):
        if command is None:
            return "The user skipped the command execution."
        if is_flatpak() and not self.settings.get_boolean("virtualization"):
            cmd = ["flatpak-spawn", "--host", "bash", "-c", command]
        else:
            cmd = ["bash", "-c", command]
        
        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                check=False
            )
            return f"Stdout:\n{self._truncate(result.stdout)}\nStderr:\n{self._truncate(result.stderr)}\nExit Code: {result.returncode}"
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def execute_command_widget(self, command: str):
        result = ToolResult()
        def execute_callback(command):
            output = self.execute_command(command)
            result.set_output(output)
            return output

        widget = CopyBox(command, "console", execution_request=True, run_callback=execute_callback)
        if self.settings.get_boolean("auto-run"):
            widget._on_execution_run_clicked(None)
        result.set_widget(widget)
        return result

    def execute_command_restore(self, tool_uuid: str, command: str):
        widget = CopyBox(command, "console", execution_request=True)
        output = self.ui_controller.get_tool_result_by_id(tool_uuid)
        if output is None or "skipped" in output.lower():
            output = None
        widget.complete_execution(output)
        result = ToolResult()
        result.set_widget(widget)
        result.set_output(output)
        return result

    def get_tools(self) -> list:
        return [
            Tool(
                name="execute_command",
                description="Execute a command and return the output on the user computer.",
                func=self.execute_command_widget,
                title="Execute Command",
                restore_func=self.execute_command_restore,
                default_on=True,
                icon_name="gnome-terminal-symbolic",
            )
        ]
import re
from unittest import result
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult 
import threading 
import json 
from ..ui.widgets import CopyBox
import subprocess
from ..utility.system import is_flatpak
from gi.repository import Gtk, Gio
from ..ui import load_image_with_callback

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

    def show_image(self, image_path_or_url: str):
        image_path = image_path_or_url
        image = Gtk.Image(css_classes=["image"])
        if image_path.startswith("http"):
            img = image
            load_image_with_callback(
                img,
                lambda pixbuf_loader, i=img: i.set_from_pixbuf(pixbuf_loader.get_pixbuf())
            )
        else:
            image.set_from_file(image_path)

        result = ToolResult()
        result.set_widget(image)
        result.set_output(None)
        return result
    
    def show_video(self, video_path: str):
            result = ToolResult() 
            video = Gtk.Video(css_classes=["video"], vexpand=True, hexpand=True)
            video.set_size_request(-1, 400)
            video.set_file(Gio.File.new_for_path(video_path))
            result.set_widget(video)
            result.set_output(None)
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
            ),
            Tool(
                name="show_image",
                description="Show an image from a given file path or URL.",
                func=self.show_image,
                title="Show Image",
                default_on=True,
                icon_name="image-x-generic-symbolic",
                restore_func=self.show_image,
            ),
            Tool(
                name="show_video",
                description="Show a video from a given file path.",
                func=self.show_video,
                title="Show Video",
                default_on=True,
                icon_name="video-x-generic-symbolic",
                restore_func=self.show_video,
            ),
        ]
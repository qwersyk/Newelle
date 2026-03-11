import os
from typing import Optional
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
from ..ui.widgets.file_read import ReadFileWidget


class FileEditingIntegration(NewelleExtension):
    id = "file_editing"
    name = "File Editing"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    def _get_open_in_editor_callback(self, file_path: str):
        """Return callback to open file in internal editor, or None if unavailable."""
        if not hasattr(self, 'ui_controller') or self.ui_controller is None:
            return None

        def _open():
            self.ui_controller.new_editor_tab(file_path)

        return _open

    def _read_file_content(self, absolute_path: str, offset: int = 0, limit: Optional[int] = None) -> tuple[str, str, bool]:
        """
        Read file content and return (content, error_message, success).
        
        Args:
            absolute_path: Absolute path to the file
            offset: 0-based line number to start reading from
            limit: Maximum number of lines to read
            
        Returns:
            Tuple of (content_or_error, display_info, success)
        """
        # Validate path is absolute
        if not os.path.isabs(absolute_path):
            return "", f"Error: Path must be absolute. Relative paths are not supported: {absolute_path}", False
        
        # Check if file exists
        if not os.path.exists(absolute_path):
            return "", f"Error: File does not exist: {absolute_path}", False
        
        # Check if it's a file (not a directory)
        if not os.path.isfile(absolute_path):
            return "", f"Error: Path is not a file: {absolute_path}", False
        
        # Check if file is readable
        if not os.access(absolute_path, os.R_OK):
            return "", f"Error: Permission denied reading file: {absolute_path}", False
        
        try:
            # Get file stats
            file_size = os.path.getsize(absolute_path)
            
            # For binary files, limit size more strictly
            # Detect if binary by reading first chunk
            with open(absolute_path, 'rb') as f:
                chunk = f.read(8192)
                # Check for null bytes (common in binary files)
                if b'\x00' in chunk:
                    return "", f"Error: Binary file detected (contains null bytes): {absolute_path}", False
            
            # Read the file as text
            with open(absolute_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            
            # Handle offset
            if offset < 0:
                offset = 0
            if offset >= total_lines:
                return "", f"Error: Offset ({offset}) exceeds total line count ({total_lines})", False
            
            # Slice lines based on offset and limit
            start_idx = offset
            end_idx = total_lines if limit is None else min(offset + limit, total_lines)
            selected_lines = lines[start_idx:end_idx]
            
            # Join lines back into content
            content = ''.join(selected_lines)
            
            # Remove trailing newline if present for cleaner display
            if content.endswith('\n'):
                content = content[:-1]
            
            # Build display info
            lines_info = f"lines {offset + 1}-{end_idx} of {total_lines}" if limit else f"{total_lines} lines"
            info = f"File: {absolute_path} ({file_size} bytes, {lines_info})"
            
            return content, info, True
            
        except UnicodeDecodeError:
            return "", f"Error: File appears to be binary (cannot decode as UTF-8): {absolute_path}", False
        except Exception as e:
            return "", f"Error reading file {absolute_path}: {str(e)}", False

    def read_file(self, absolute_path: str, offset: int = 0, limit: Optional[int] = None):
        """
        Read the contents of a text file at the given absolute path.
        
        Args:
            absolute_path: The absolute path to the file (e.g., '/home/user/project/file.txt')
            offset: Optional 0-based line number to start reading from (for pagination)
            limit: Optional maximum number of lines to read (for pagination)
            
        Returns:
            ToolResult with file content and widget for display
        """
        result = ToolResult()
        
        # Read the file
        content, info, success = self._read_file_content(absolute_path, offset, limit)
        
        if not success:
            # Error case - return error message
            result.set_output(info)
            return result
        
        # Success - create widget and set output
        # Calculate max lines for display (to avoid overwhelming the UI)
        max_display_lines = 500
        if limit and limit > 0:
            max_display_lines = min(limit, max_display_lines)
        
        # Create the widget
        widget = ReadFileWidget(
            file_path=absolute_path,
            content=content,
            offset=offset,
            max_content_lines=max_display_lines,
            open_in_editor_callback=self._get_open_in_editor_callback(absolute_path)
        )
        
        # Set output for LLM
        # Truncate if too large for LLM context
        max_output_chars = 10000
        if len(content) > max_output_chars:
            truncated_content = content[:max_output_chars]
            output = f"{info}\n\n{truncated_content}\n\n... (content truncated to {max_output_chars} characters for LLM context)"
        else:
            output = f"{info}\n\n{content}"
        
        result.set_output(output)
        result.set_widget(widget)
        
        return result
    
    def read_file_restore(self, tool_uuid: str, absolute_path: str, offset: int = 0, limit: Optional[int] = None):
        """
        Restore the read_file widget from chat history.
        
        Args:
            tool_uuid: UUID of the tool call
            absolute_path: Path that was read
            offset: Offset that was used
            limit: Limit that was used
            
        Returns:
            ToolResult with restored widget
        """
        result = ToolResult()
        
        # Read the file again to get content
        content, info, success = self._read_file_content(absolute_path, offset, limit)
        
        if not success:
            result.set_output(info)
            return result
        
        # Recreate the widget
        max_display_lines = 500
        if limit and limit > 0:
            max_display_lines = min(limit, max_display_lines)
        
        widget = ReadFileWidget(
            file_path=absolute_path,
            content=content,
            offset=offset,
            max_content_lines=max_display_lines,
            open_in_editor_callback=self._get_open_in_editor_callback(absolute_path)
        )
        
        # Set output
        max_output_chars = 10000
        if len(content) > max_output_chars:
            truncated_content = content[:max_output_chars]
            output = f"{info}\n\n{truncated_content}\n\n... (content truncated)"
        else:
            output = f"{info}\n\n{content}"
        
        result.set_output(output)
        result.set_widget(widget)
        
        return result

    def get_tools(self):
        return [
            Tool(
                name="read_file",
                description="Read the contents of a text file at the given absolute path. Supports pagination with offset and limit for large files.",
                func=self.read_file,
                title="Read File",
                restore_func=self.read_file_restore,
                default_on=True,
                icon_name="document-open-symbolic",
                tools_group="File Operations"
            ),
        ]

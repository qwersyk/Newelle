import os
from typing import Optional
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
from ..ui.widgets.file_read import ReadFileWidget
from ..ui.widgets.file_edit import FileEditWidget


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

    def write_file(self, file_path: str, content: str):
        """
        Write content to a file at the given absolute path.
        Creates a new file or overwrites an existing file.

        Args:
            file_path: The absolute path to the file (e.g., '/home/user/project/file.txt')
            content: The content to write to the file

        Returns:
            ToolResult with diff widget and status message
        """
        result = ToolResult()

        # Validate path is absolute
        if not os.path.isabs(file_path):
            result.set_output(f"Error: Path must be absolute. Relative paths are not supported: {file_path}")
            return result

        # Check if parent directory exists
        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            result.set_output(f"Error: Parent directory does not exist: {parent_dir}")
            return result

        # Check if parent is a directory
        if parent_dir and not os.path.isdir(parent_dir):
            result.set_output(f"Error: Parent path is not a directory: {parent_dir}")
            return result

        # Check if file exists and is a directory
        if os.path.exists(file_path) and os.path.isdir(file_path):
            result.set_output(f"Error: Path is a directory, not a file: {file_path}")
            return result

        # Get old content if file exists (for diff)
        old_content = ""
        edit_type = "write"
        if os.path.exists(file_path):
            # File exists - read it for diff
            old_content_raw, _, success = self._read_file_content(file_path)
            if success:
                old_content = old_content_raw
                edit_type = "edit"

            # Check write permissions for existing file
            if not os.access(file_path, os.W_OK):
                result.set_output(f"Error: Permission denied writing to file: {file_path}")
                return result
        else:
            # New file - check directory write permissions
            if parent_dir and not os.access(parent_dir, os.W_OK):
                result.set_output(f"Error: Permission denied creating file in directory: {parent_dir}")
                return result

        try:
            # Write the file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            # Get file stats
            file_size = os.path.getsize(file_path)
            lines_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)

            # Create the diff widget
            widget = FileEditWidget(
                file_path=file_path,
                old_content=old_content,
                new_content=content,
                edit_type=edit_type,
                open_in_editor_callback=self._get_open_in_editor_callback(file_path)
            )

            # Set output for LLM
            if edit_type == "write":
                output = f"Successfully created new file: {file_path}\nSize: {file_size} bytes, {lines_count} lines"
            else:
                output = f"Successfully wrote to file: {file_path}\nSize: {file_size} bytes, {lines_count} lines"

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Error writing file {file_path}: {str(e)}")

        return result

    def write_file_restore(self, tool_uuid: str, file_path: str, content: str):
        """
        Restore the write_file widget from chat history.

        Args:
            tool_uuid: UUID of the tool call
            file_path: Path that was written
            content: Content that was written

        Returns:
            ToolResult with restored widget
        """
        result = ToolResult()

        # Read current file content for diff
        old_content = ""
        edit_type = "write"
        if os.path.exists(file_path) and os.path.isfile(file_path):
            old_content_raw, _, success = self._read_file_content(file_path)
            if success:
                old_content = old_content_raw
                edit_type = "edit"

        # Create the widget
        widget = FileEditWidget(
            file_path=file_path,
            old_content=old_content,
            new_content=content,
            edit_type=edit_type,
            open_in_editor_callback=self._get_open_in_editor_callback(file_path)
        )

        # Set output
        lines_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        if edit_type == "write":
            output = f"File: {file_path} ({lines_count} lines) - Created"
        else:
            output = f"File: {file_path} ({lines_count} lines) - Modified"

        result.set_output(output)
        result.set_widget(widget)

        return result

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        """
        Edit a file by replacing old_string with new_string.

        Args:
            file_path: The absolute path to the target file
            old_string: The exact literal text to replace (empty = create new file)
            new_string: The exact literal text to write in its place
            replace_all: If True, replace all occurrences instead of requiring a unique match

        Returns:
            ToolResult with diff widget and status message
        """
        result = ToolResult()

        # Validate path is absolute
        if not os.path.isabs(file_path):
            result.set_output(f"Error: Path must be absolute. Relative paths are not supported: {file_path}")
            return result

        # If old_string is empty, treat as write_file (create/overwrite)
        if not old_string:
            return self.write_file(file_path, new_string)

        # Check if file exists
        if not os.path.exists(file_path):
            result.set_output(f"Error: File does not exist: {file_path}")
            return result

        # Check if it's a file
        if not os.path.isfile(file_path):
            result.set_output(f"Error: Path is not a file: {file_path}")
            return result

        # Check read permissions
        if not os.access(file_path, os.R_OK):
            result.set_output(f"Error: Permission denied reading file: {file_path}")
            return result

        # Check write permissions
        if not os.access(file_path, os.W_OK):
            result.set_output(f"Error: Permission denied writing to file: {file_path}")
            return result

        # Read the file
        old_content, info, success = self._read_file_content(file_path)
        if not success:
            result.set_output(old_content)  # Error message
            return result

        # Check for occurrences of old_string
        occurrences = old_content.count(old_string)

        if occurrences == 0:
            result.set_output(f"Error: Could not find the text to replace in {file_path}\nThe specified 'old_string' was not found in the file.")
            return result

        if occurrences > 1 and not replace_all:
            result.set_output(f"Error: Found {occurrences} occurrences of the text to replace in {file_path}.\nUse replace_all=True to replace all occurrences, or provide a more specific old_string that matches uniquely.")
            return result

        # Perform the replacement
        if replace_all:
            new_content = old_content.replace(old_string, new_string)
        else:
            new_content = old_content.replace(old_string, new_string, 1)

        try:
            # Write the modified content
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            # Get file stats
            file_size = os.path.getsize(file_path)

            # Create the diff widget
            widget = FileEditWidget(
                file_path=file_path,
                old_content=old_content,
                new_content=new_content,
                edit_type="edit",
                open_in_editor_callback=self._get_open_in_editor_callback(file_path)
            )

            # Set output for LLM
            replacement_count = occurrences if replace_all else 1
            output = f"Successfully edited file: {file_path}\nMade {replacement_count} replacement(s)\nNew size: {file_size} bytes"

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Error editing file {file_path}: {str(e)}")

        return result

    def edit_restore(self, tool_uuid: str, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        """
        Restore the edit widget from chat history.

        Args:
            tool_uuid: UUID of the tool call
            file_path: Path that was edited
            old_string: The text that was replaced
            new_string: The text that was inserted
            replace_all: Whether all occurrences were replaced

        Returns:
            ToolResult with restored widget
        """
        result = ToolResult()

        # Read current file content
        current_content, _, success = self._read_file_content(file_path)
        if not success:
            result.set_output(current_content)
            return result

        # Reconstruct the original content
        if old_string:
            if replace_all:
                original_content = current_content.replace(new_string, old_string)
            else:
                original_content = current_content.replace(new_string, old_string, 1)
        else:
            # This was a write operation
            original_content = ""

        # Create the widget
        widget = FileEditWidget(
            file_path=file_path,
            old_content=original_content,
            new_content=current_content,
            edit_type="edit",
            open_in_editor_callback=self._get_open_in_editor_callback(file_path)
        )

        # Set output
        result.set_output(f"File: {file_path} - Edited")
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
            Tool(
                name="write_file",
                description="Write content to a file at the given absolute path. Creates a new file or overwrites an existing file. Shows a diff view of the changes.",
                func=self.write_file,
                title="Write File",
                restore_func=self.write_file_restore,
                default_on=True,
                icon_name="document-save-symbolic",
                tools_group="File Operations"
            ),
            Tool(
                name="edit",
                description="Edit a file by replacing old_string with new_string. The old_string must match exactly. If multiple occurrences exist, use replace_all=True to replace all. Shows a diff view of the changes.",
                func=self.edit,
                title="Edit File",
                restore_func=self.edit_restore,
                default_on=True,
                icon_name="document-edit-symbolic",
                tools_group="File Operations"
            ),
        ]

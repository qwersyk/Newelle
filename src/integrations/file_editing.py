import fnmatch
import glob as glob_module
import os
import re
from typing import Optional
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
from ..ui.widgets.file_read import ReadFileWidget
from ..ui.widgets.file_edit import FileEditWidget
from ..ui.widgets.glob import GlobWidget
from ..ui.widgets.grep import GrepWidget
from ..ui.widgets.list_directory import ListDirectoryWidget


class FileEditingIntegration(NewelleExtension):
    id = "file_editing"
    name = "File Editing"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    def _get_open_in_editor_callback(self, file_path: str = None):
        """Return callback to open file in internal editor, or None if unavailable.

        Args:
            file_path: If provided, returns a callback that opens this specific file.
                      If None, returns a callback that accepts a file_path argument.
        """
        if not hasattr(self, 'ui_controller') or self.ui_controller is None:
            return None

        if file_path is not None:
            # Return a callback for a specific file
            def _open():
                self.ui_controller.new_editor_tab(file_path)
            return _open
        else:
            # Return a callback that accepts any file path
            def _open_any(path: str):
                self.ui_controller.new_editor_tab(path)
            return _open_any

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

    def glob(self, pattern: str, path: Optional[str] = None):
        """
        Search for files matching a glob pattern.

        Args:
            pattern: The glob pattern to match files against (e.g., '*.py', '**/*.txt')
            path: The directory to search in. If not specified, the current working directory will be used.

        Returns:
            ToolResult with list of matching files and a widget for display
        """
        result = ToolResult()

        # Validate pattern
        if not pattern or not pattern.strip():
            result.set_output("Error: Pattern cannot be empty or whitespace-only")
            return result

        # Determine search directory
        if path:
            # Validate path if provided
            if not os.path.isabs(path):
                result.set_output(f"Error: Path must be absolute: {path}")
                return result

            if not os.path.exists(path):
                result.set_output(f"Error: Directory does not exist: {path}")
                return result

            if not os.path.isdir(path):
                result.set_output(f"Error: Path is not a directory: {path}")
                return result

            search_dir = path
        else:
            # Use current working directory
            search_dir = os.getcwd()

        # Normalize the pattern
        pattern = pattern.strip()

        try:
            # Perform glob search
            # Use recursive=True to support ** patterns
            full_pattern = os.path.join(search_dir, pattern)
            matches = glob_module.glob(full_pattern, recursive=True)

            # Sort matches for consistent output
            matches = sorted(matches)

            # Build output for LLM
            if not matches:
                output = f"No files match the pattern '{pattern}' in {search_dir}"
            else:
                output_lines = [f"Found {len(matches)} file(s) matching '{pattern}' in {search_dir}:", ""]
                for match in matches:
                    # Show relative paths for cleaner output
                    try:
                        rel_path = os.path.relpath(match, search_dir)
                        output_lines.append(rel_path)
                    except ValueError:
                        output_lines.append(match)
                output = "\n".join(output_lines)

            # Create the widget
            open_callback = self._get_open_in_editor_callback() if matches else None
            widget = GlobWidget(
                pattern=pattern,
                search_path=search_dir,
                matches=matches,
                open_in_editor_callback=open_callback
            )

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Error performing glob search: {str(e)}")

        return result

    def glob_restore(self, tool_uuid: str, pattern: str, path: Optional[str] = None):
        """
        Restore the glob widget from chat history.

        Args:
            tool_uuid: UUID of the tool call
            pattern: The glob pattern that was used
            path: The path that was searched (optional)

        Returns:
            ToolResult with restored widget
        """
        result = ToolResult()

        # Determine search directory
        search_dir = path if path else os.getcwd()

        # Re-run the glob search
        try:
            full_pattern = os.path.join(search_dir, pattern)
            matches = glob_module.glob(full_pattern, recursive=True)
            matches = sorted(matches)

            # Build output
            if not matches:
                output = f"Glob: '{pattern}' in {search_dir} - No matches"
            else:
                file_count = sum(1 for m in matches if os.path.isfile(m))
                dir_count = len(matches) - file_count
                output = f"Glob: '{pattern}' in {search_dir} - {len(matches)} matches ({file_count} files, {dir_count} directories)"

            # Recreate the widget
            open_callback = self._get_open_in_editor_callback() if matches else None
            widget = GlobWidget(
                pattern=pattern,
                search_path=search_dir,
                matches=matches,
                open_in_editor_callback=open_callback
            )

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Glob: '{pattern}' in {search_dir} - Error: {str(e)}")

        return result

    def _should_ignore(self, entry_name: str, ignore_patterns: Optional[list[str]]) -> bool:
        """Check if an entry matches any of the ignore patterns."""
        if not ignore_patterns:
            return False
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(entry_name, pattern):
                return True
        return False

    def list_directory(self, path: str, ignore: Optional[list[str]] = None):
        """
        List the contents of a directory.

        Args:
            path: The absolute path to the directory to list (must be absolute, not relative)
            ignore: Optional list of glob patterns to ignore (e.g., ['*.pyc', '.git', '__pycache__'])

        Returns:
            ToolResult with list of entries and a widget for display
        """
        result = ToolResult()

        # Validate path is absolute
        if not os.path.isabs(path):
            result.set_output(f"Error: Path must be absolute. Relative paths are not supported: {path}")
            return result

        # Validate path exists
        if not os.path.exists(path):
            result.set_output(f"Error: Directory does not exist: {path}")
            return result

        # Validate path is a directory
        if not os.path.isdir(path):
            result.set_output(f"Error: Path is not a directory: {path}")
            return result

        # Validate read permission
        if not os.access(path, os.R_OK):
            result.set_output(f"Error: Permission denied reading directory: {path}")
            return result

        try:
            entries = []
            for entry in os.listdir(path):
                if self._should_ignore(entry, ignore):
                    continue
                entries.append(entry)

            entries = sorted(entries)

            # Build output for LLM
            if not entries:
                output = f"Directory {path} is empty"
            else:
                output_lines = [f"Contents of {path} ({len(entries)} entries):", ""]
                for entry in entries:
                    full_path = os.path.join(path, entry)
                    suffix = "/" if os.path.isdir(full_path) else ""
                    output_lines.append(f"{entry}{suffix}")
                output = "\n".join(output_lines)

            # Create the widget
            open_callback = self._get_open_in_editor_callback() if entries else None
            widget = ListDirectoryWidget(
                dir_path=path,
                entries=entries,
                open_in_editor_callback=open_callback
            )

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Error listing directory {path}: {str(e)}")

        return result

    def list_directory_restore(self, tool_uuid: str, path: str, ignore: Optional[list[str]] = None):
        """
        Restore the list_directory widget from chat history.

        Args:
            tool_uuid: UUID of the tool call
            path: The directory that was listed
            ignore: The ignore patterns that were used (optional)

        Returns:
            ToolResult with restored widget
        """
        result = ToolResult()

        if not os.path.isabs(path) or not os.path.exists(path) or not os.path.isdir(path):
            result.set_output(f"Directory: {path} - Cannot restore (path invalid or inaccessible)")
            return result

        try:
            entries = []
            for entry in os.listdir(path):
                if self._should_ignore(entry, ignore):
                    continue
                entries.append(entry)

            entries = sorted(entries)

            file_count = sum(1 for e in entries if os.path.isfile(os.path.join(path, e)))
            dir_count = len(entries) - file_count
            output = f"Directory: {path} - {len(entries)} entries ({file_count} files, {dir_count} directories)"

            open_callback = self._get_open_in_editor_callback() if entries else None
            widget = ListDirectoryWidget(
                dir_path=path,
                entries=entries,
                open_in_editor_callback=open_callback
            )

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Directory: {path} - Error: {str(e)}")

        return result

    def _expand_glob_pattern(self, glob_pattern: str) -> list[str]:
        """Expand brace patterns like '.{ts,tsx}' to ['*.ts', '*.tsx']."""
        import itertools
        # Simple brace expansion: {a,b,c} -> [a, b, c]
        match = re.search(r'\{([^{}]+)\}', glob_pattern)
        if not match:
            # No braces - ensure we have * for fnmatch
            pattern = glob_pattern.strip()
            if pattern and not pattern.startswith('*'):
                pattern = '*' + pattern
            return [pattern] if pattern else []

        before, brace_content, after = glob_pattern[:match.start()], match.group(1), glob_pattern[match.end():]
        parts = [p.strip() for p in brace_content.split(',')]
        results = []
        for part in parts:
            expanded = before + part + after
            if not expanded.startswith('*'):
                expanded = '*' + expanded
            results.append(expanded)
        return results

    def _file_matches_glob(self, filename: str, glob_patterns: list[str]) -> bool:
        """Check if filename matches any of the glob patterns."""
        if not glob_patterns:
            return True
        for pattern in glob_patterns:
            if fnmatch.fnmatch(filename, pattern):
                return True
        return False

    def grep_search(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        limit: Optional[int] = None
    ):
        """
        Search for a regular expression pattern in file contents.

        Args:
            pattern: The regular expression pattern to search for
            path: File or directory to search in (defaults to current working directory)
            glob: Glob pattern to filter files (e.g., "*.py", ".{ts,tsx}")
            limit: Limit output to first N matches (optional)

        Returns:
            ToolResult with matching lines and a widget for display
        """
        result = ToolResult()

        # Validate pattern
        if not pattern or not pattern.strip():
            result.set_output("Error: Pattern cannot be empty or whitespace-only")
            return result

        try:
            regex = re.compile(pattern)
        except re.error as e:
            result.set_output(f"Error: Invalid regular expression: {e}")
            return result

        # Determine search path
        if path:
            if not os.path.isabs(path):
                result.set_output(f"Error: Path must be absolute: {path}")
                return result
            if not os.path.exists(path):
                result.set_output(f"Error: Path does not exist: {path}")
                return result
            search_path = path
        else:
            search_path = os.getcwd()

        # Expand glob patterns
        glob_patterns = self._expand_glob_pattern(glob) if glob else []

        matches = []
        match_count = 0

        def search_file(file_path: str) -> bool:
            """Search a single file. Returns True if limit reached."""
            nonlocal match_count
            try:
                # Skip binary files
                with open(file_path, 'rb') as f:
                    if b'\x00' in f.read(8192):
                        return False
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append((file_path, line_num, line))
                            match_count += 1
                            if limit and match_count >= limit:
                                return True
            except (OSError, UnicodeDecodeError):
                pass
            return False

        try:
            if os.path.isfile(search_path):
                # Single file
                if not glob_patterns or self._file_matches_glob(os.path.basename(search_path), glob_patterns):
                    search_file(search_path)
            else:
                # Directory - walk recursively
                for root, dirs, files in os.walk(search_path):
                    # Skip hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for filename in files:
                        if limit and match_count >= limit:
                            break
                        if glob_patterns and not self._file_matches_glob(filename, glob_patterns):
                            continue
                        file_path = os.path.join(root, filename)
                        if search_file(file_path):
                            break
                    if limit and match_count >= limit:
                        break

            # Build output for LLM
            if not matches:
                output = f"No matches for '{pattern}' in {search_path}"
            else:
                output_lines = [f"Found {len(matches)} match(es) for '{pattern}' in {search_path}:", ""]
                for file_path, line_num, content in matches:
                    try:
                        rel_path = os.path.relpath(file_path, search_path)
                    except ValueError:
                        rel_path = file_path
                    output_lines.append(f"{rel_path}:{line_num}:{content.rstrip()}")
                output = "\n".join(output_lines)

            open_callback = self._get_open_in_editor_callback() if matches else None
            widget = GrepWidget(
                pattern=pattern,
                search_path=search_path,
                matches=matches,
                open_in_editor_callback=open_callback
            )

            result.set_output(output)
            result.set_widget(widget)

        except Exception as e:
            result.set_output(f"Error during grep search: {str(e)}")

        return result

    def grep_search_restore(
        self,
        tool_uuid: str,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        limit: Optional[int] = None
    ):
        """
        Restore the grep_search widget from chat history.
        """
        result = ToolResult()
        # Re-run the search to restore
        return self.grep_search(pattern=pattern, path=path, glob=glob, limit=limit)

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
            Tool(
                name="glob",
                description="Search for files matching a glob pattern. Supports standard glob patterns like '*.py' and recursive patterns like '**/*.txt'. If path is not specified, searches in the current working directory.",
                func=self.glob,
                title="Glob Search",
                restore_func=self.glob_restore,
                default_on=True,
                icon_name="folder-saved-search-symbolic",
                tools_group="File Operations"
            ),
            Tool(
                name="list_directory",
                description="List the contents of a directory. Use the ignore parameter to exclude files matching glob patterns (e.g., ['*.pyc', '.git', '__pycache__']).",
                func=self.list_directory,
                title="List Directory",
                restore_func=self.list_directory_restore,
                default_on=True,
                icon_name="folder-open-symbolic",
                tools_group="File Operations",
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The absolute path to the directory to list (must be absolute, not relative)"
                        },
                        "ignore": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of glob patterns to ignore (e.g., ['*.pyc', '.git', '__pycache__'])"
                        }
                    },
                    "required": ["path"]
                }
            ),
            Tool(
                name="grep_search",
                description="Search for a regular expression pattern in file contents. Supports path (file or directory), glob filter (e.g., '*.py', '.{ts,tsx}'), and optional limit on number of matches.",
                func=self.grep_search,
                title="Grep Search",
                restore_func=self.grep_search_restore,
                default_on=True,
                icon_name="edit-find-symbolic",
                tools_group="File Operations",
                schema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "The regular expression pattern to search for in file contents"
                        },
                        "path": {
                            "type": "string",
                            "description": "File or directory to search in (defaults to current working directory if omitted)"
                        },
                        "glob": {
                            "type": "string",
                            "description": "Glob pattern to filter files (e.g., '*.py', '.{ts,tsx}')"
                        },
                        "limit": {
                            "type": "number",
                            "description": "Limit output to first N matches (optional)"
                        }
                    },
                    "required": ["pattern"]
                }
            ),
        ]

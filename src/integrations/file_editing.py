import fnmatch
import glob as glob_module
import json
import os
import re
import threading
from gettext import gettext as _
from typing import Optional
from gi.repository import GLib
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult, InteractionOption
from ..ui.widgets.file_read import ReadFileWidget
from ..ui.widgets.file_edit import FileEditWidget
from ..ui.widgets.file_permission_confirm import FilePermissionConfirmWidget
from ..ui.widgets.glob import GlobWidget
from ..ui.widgets.grep import GrepWidget
from ..ui.widgets.list_directory import ListDirectoryWidget


class FileEditingIntegration(NewelleExtension):
    id = "file_editing"
    name = "File Editing"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    def _get_permission_rules(self):
        """Load the file-permissions rules from settings."""
        try:
            raw = self.settings.get_string("file-permissions")
            return json.loads(raw)
        except Exception:
            return [
                {"path": "*", "read": "allow", "write": "ask"},
                {"path": "{{main_path}}", "read": "allow", "write": "ask"},
            ]

    def _resolve_rule_path(self, rule_path: str) -> str:
        """Resolve special tokens in a rule path to an absolute directory."""
        if rule_path == "*":
            return "*"
        if rule_path == "{{main_path}}":
            main_path = self.settings.get_string("path")
            return os.path.realpath(os.path.expanduser(main_path))
        return os.path.realpath(os.path.expanduser(rule_path))

    def _get_permission_mode(self, file_path: str, operation: str) -> str:
        """Determine the permission mode for *file_path* and *operation*.

        Args:
            file_path: Absolute path to the target file or directory.
            operation: ``"read"`` or ``"write"``.

        Returns:
            ``"allow"``, ``"ask"``, or ``"block"``.
        """
        rules = self._get_permission_rules()
        abs_path = os.path.realpath(os.path.expanduser(file_path))

        best_match_len = -1
        best_mode = "allow"

        for rule in rules:
            resolved = self._resolve_rule_path(rule.get("path", "*"))
            mode = rule.get(operation, "allow")

            if resolved == "*":
                if best_match_len < 0:
                    best_match_len = 0
                    best_mode = mode
            else:
                resolved_dir = resolved if resolved.endswith("/") else resolved + "/"
                if abs_path == resolved or abs_path.startswith(resolved_dir):
                    if len(resolved) > best_match_len:
                        best_match_len = len(resolved)
                        best_mode = mode

        return best_mode

    def _check_and_execute(self, file_path: str, operation: str, execute_fn):
        """Check permission for *file_path* and either execute, block, or ask.

        Args:
            file_path: Absolute path being operated on.
            operation: ``"read"`` or ``"write"``.
            execute_fn: Zero-argument callable that performs the real work
                        and returns a ``ToolResult``.

        Returns:
            A ``ToolResult``.
        """
        mode = self._get_permission_mode(file_path, operation)

        if mode == "allow":
            return execute_fn()

        if mode == "block":
            result = ToolResult()
            result.set_output(
                f"Error: {operation.capitalize()} access to {file_path} is blocked by file permission settings."
            )
            return result

        # mode == "ask" -- return immediately; ToolResult semaphore blocks
        # the LLM thread at get_output() until the user responds.
        result = ToolResult(requires_interaction=True)

        def on_accepted():
            # execute_fn creates GTK widgets, so it must run on the main
            # thread.  Schedule it via idle_add and wait for completion.
            inner_holder = {}
            done = threading.Event()

            def _run_on_main():
                try:
                    inner = execute_fn()
                    inner_holder["result"] = inner
                except Exception as exc:
                    inner_holder["error"] = exc
                finally:
                    done.set()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_run_on_main)
            done.wait()

            if "error" in inner_holder:
                result.set_output(f"Error: {inner_holder['error']}")
                return

            inner = inner_holder["result"]
            if inner.widget is not None:
                GLib.idle_add(box.append, inner.widget)
            result.set_output(inner.get_output())

        def on_rejected(_widget=None):
            result.set_output(
                f"The user rejected the {operation} operation on {file_path}."
            )

        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        confirm = FilePermissionConfirmWidget(
            file_path=file_path,
            operation=operation,
            run_callback=on_accepted,
        )
        confirm.connect("rejected", on_rejected)
        box.append(confirm)

        result.set_widget(box)
        result.set_intreaction_options([
            InteractionOption(_("Accept"), on_accepted),
            InteractionOption(_("Reject"), lambda: on_rejected())
        ])
        return result

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
        """Read the contents of a text file with permission checking."""
        return self._check_and_execute(
            absolute_path, "read",
            lambda: self._read_file_impl(absolute_path, offset, limit),
        )

    def _read_file_impl(self, absolute_path: str, offset: int = 0, limit: Optional[int] = None):
        result = ToolResult()
        content, info, success = self._read_file_content(absolute_path, offset, limit)
        if not success:
            result.set_output(info)
            return result
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
        """Write content to a file with permission checking."""
        return self._check_and_execute(
            file_path, "write",
            lambda: self._write_file_impl(file_path, content),
        )

    def _write_file_impl(self, file_path: str, content: str):
        result = ToolResult()

        if not os.path.isabs(file_path):
            result.set_output(f"Error: Path must be absolute. Relative paths are not supported: {file_path}")
            return result

        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            result.set_output(f"Error: Parent directory does not exist: {parent_dir}")
            return result

        if parent_dir and not os.path.isdir(parent_dir):
            result.set_output(f"Error: Parent path is not a directory: {parent_dir}")
            return result

        if os.path.exists(file_path) and os.path.isdir(file_path):
            result.set_output(f"Error: Path is a directory, not a file: {file_path}")
            return result

        old_content = ""
        edit_type = "write"
        if os.path.exists(file_path):
            old_content_raw, _, success = self._read_file_content(file_path)
            if success:
                old_content = old_content_raw
                edit_type = "edit"

            if not os.access(file_path, os.W_OK):
                result.set_output(f"Error: Permission denied writing to file: {file_path}")
                return result
        else:
            if parent_dir and not os.access(parent_dir, os.W_OK):
                result.set_output(f"Error: Permission denied creating file in directory: {parent_dir}")
                return result

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            file_size = os.path.getsize(file_path)
            lines_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)

            widget = FileEditWidget(
                file_path=file_path,
                old_content=old_content,
                new_content=content,
                edit_type=edit_type,
                open_in_editor_callback=self._get_open_in_editor_callback(file_path),
                undo_callback=self.undo_file_edit,
                redo_callback=self.redo_file_edit
            )

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
            open_in_editor_callback=self._get_open_in_editor_callback(file_path),
            undo_callback=self.undo_file_edit,
            redo_callback=self.redo_file_edit
        )

        # Set output with same format as original write
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        lines_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        if edit_type == "write":
            output = f"Successfully created new file: {file_path}\nSize: {file_size} bytes, {lines_count} lines"
        else:
            output = f"Successfully wrote to file: {file_path}\nSize: {file_size} bytes, {lines_count} lines"

        result.set_output(output)
        result.set_widget(widget)

        return result

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        """Edit a file by replacing old_string with new_string, with permission checking."""
        return self._check_and_execute(
            file_path, "write",
            lambda: self._edit_impl(file_path, old_string, new_string, replace_all),
        )

    def _edit_impl(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        result = ToolResult()

        if not os.path.isabs(file_path):
            result.set_output(f"Error: Path must be absolute. Relative paths are not supported: {file_path}")
            return result

        if not old_string:
            return self._write_file_impl(file_path, new_string)

        if not os.path.exists(file_path):
            result.set_output(f"Error: File does not exist: {file_path}")
            return result

        if not os.path.isfile(file_path):
            result.set_output(f"Error: Path is not a file: {file_path}")
            return result

        if not os.access(file_path, os.R_OK):
            result.set_output(f"Error: Permission denied reading file: {file_path}")
            return result

        if not os.access(file_path, os.W_OK):
            result.set_output(f"Error: Permission denied writing to file: {file_path}")
            return result

        old_content, info, success = self._read_file_content(file_path)
        if not success:
            result.set_output(old_content)
            return result

        occurrences = old_content.count(old_string)

        if occurrences == 0:
            result.set_output(f"Error: Could not find the text to replace in {file_path}\nThe specified 'old_string' was not found in the file.")
            return result

        if occurrences > 1 and not replace_all:
            result.set_output(f"Error: Found {occurrences} occurrences of the text to replace in {file_path}.\nUse replace_all=True to replace all occurrences, or provide a more specific old_string that matches uniquely.")
            return result

        if replace_all:
            new_content = old_content.replace(old_string, new_string)
        else:
            new_content = old_content.replace(old_string, new_string, 1)

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            file_size = os.path.getsize(file_path)

            widget = FileEditWidget(
                file_path=file_path,
                old_content=old_content,
                new_content=new_content,
                edit_type="edit",
                open_in_editor_callback=self._get_open_in_editor_callback(file_path),
                undo_callback=self.undo_file_edit,
                redo_callback=self.redo_file_edit
            )

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
            open_in_editor_callback=self._get_open_in_editor_callback(file_path),
            undo_callback=self.undo_file_edit,
            redo_callback=self.redo_file_edit
        )

        # Set output with same format as original edit
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        replacement_count = original_content.count(old_string) if old_string else 1
        output = f"Successfully edited file: {file_path}\nMade {replacement_count} replacement(s)\nNew size: {file_size} bytes"
        result.set_output(output)
        result.set_widget(widget)

        return result

    def undo_file_edit(self, file_path: str, old_content: str, edit_type: str) -> bool:
        """
        Undo a file edit by restoring the old content.

        Args:
            file_path: Path to the file to undo
            old_content: The original content to restore
            edit_type: Type of edit - "write" (new file) or "edit" (modification)

        Returns:
            True if successful, False otherwise
        """
        try:
            mode = self._get_permission_mode(file_path, "write")
            if mode == "block":
                return False

            if edit_type == "write" and not old_content:
                if os.path.exists(file_path):
                    os.remove(file_path)
            else:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(old_content)
            return True
        except Exception:
            return False

    def redo_file_edit(self, file_path: str, new_content: str, edit_type: str) -> bool:
        """
        Redo a file edit by restoring the new content.

        Args:
            file_path: Path to the file to redo
            new_content: The new content to restore
            edit_type: Type of edit - "write" (new file) or "edit" (modification)

        Returns:
            True if successful, False otherwise
        """
        try:
            mode = self._get_permission_mode(file_path, "write")
            if mode == "block":
                return False

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
        except Exception:
            return False

    def glob(self, pattern: str, path: Optional[str] = None):
        """Search for files matching a glob pattern, with permission checking."""
        search_dir = path if path else os.getcwd()
        return self._check_and_execute(
            search_dir, "read",
            lambda: self._glob_impl(pattern, path),
        )

    def _glob_impl(self, pattern: str, path: Optional[str] = None):
        result = ToolResult()

        if not pattern or not pattern.strip():
            result.set_output("Error: Pattern cannot be empty or whitespace-only")
            return result

        if path:
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
            search_dir = os.getcwd()

        pattern = pattern.strip()

        def _do_glob_search():
            try:
                full_pattern = os.path.join(search_dir, pattern)
                matches = glob_module.glob(full_pattern, recursive=True)
                matches = sorted(matches)
                return matches, None
            except Exception as e:
                return None, str(e)

        def _update_widget(matches, error):
            if error:
                result.set_output(f"Error performing glob search: {error}")
                return

            if not matches:
                output = f"No files match the pattern '{pattern}' in {search_dir}"
            else:
                output_lines = [f"Found {len(matches)} file(s) matching '{pattern}' in {search_dir}:", ""]
                for match in matches:
                    try:
                        rel_path = os.path.relpath(match, search_dir)
                        output_lines.append(rel_path)
                    except ValueError:
                        output_lines.append(match)
                output = "\n".join(output_lines)

            widget.set_matches(matches)
            open_callback = self._get_open_in_editor_callback() if matches else None
            widget.open_in_editor_callback = open_callback

            result.set_output(output)

        open_callback = self._get_open_in_editor_callback()
        widget = GlobWidget(
            pattern=pattern,
            search_path=search_dir,
            matches=[],
            open_in_editor_callback=None
        )
        result.set_widget(widget)

        def _run_search_thread():
            matches, error = _do_glob_search()
            GLib.idle_add(lambda: _update_widget(matches, error))

        thread = threading.Thread(target=_run_search_thread, daemon=True)
        thread.start()

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

        def _do_glob_search():
            try:
                full_pattern = os.path.join(search_dir, pattern)
                matches = glob_module.glob(full_pattern, recursive=True)
                matches = sorted(matches)
                return matches, None
            except Exception as e:
                return None, str(e)

        def _update_widget(matches, error):
            if error:
                result.set_output(f"Glob: '{pattern}' in {search_dir} - Error: {error}")
                return

            # Build output
            if not matches:
                output = f"Glob: '{pattern}' in {search_dir} - No matches"
            else:
                file_count = sum(1 for m in matches if os.path.isfile(m))
                dir_count = len(matches) - file_count
                output = f"Glob: '{pattern}' in {search_dir} - {len(matches)} matches ({file_count} files, {dir_count} directories)"

            widget.set_matches(matches)
            open_callback = self._get_open_in_editor_callback() if matches else None
            widget.open_in_editor_callback = open_callback

            result.set_output(output)

        open_callback = self._get_open_in_editor_callback()
        widget = GlobWidget(
            pattern=pattern,
            search_path=search_dir,
            matches=[],
            open_in_editor_callback=None
        )
        result.set_widget(widget)

        def _run_search_thread():
            matches, error = _do_glob_search()
            GLib.idle_add(lambda: _update_widget(matches, error))

        thread = threading.Thread(target=_run_search_thread, daemon=True)
        thread.start()

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
        """List directory contents with permission checking."""
        return self._check_and_execute(
            path, "read",
            lambda: self._list_directory_impl(path, ignore),
        )

    def _list_directory_impl(self, path: str, ignore: Optional[list[str]] = None):
        result = ToolResult()

        if not os.path.isabs(path):
            result.set_output(f"Error: Path must be absolute. Relative paths are not supported: {path}")
            return result

        if not os.path.exists(path):
            result.set_output(f"Error: Directory does not exist: {path}")
            return result

        if not os.path.isdir(path):
            result.set_output(f"Error: Path is not a directory: {path}")
            return result

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

            if not entries:
                output = f"Directory {path} is empty"
            else:
                output_lines = [f"Contents of {path} ({len(entries)} entries):", ""]
                for entry in entries:
                    full_path = os.path.join(path, entry)
                    suffix = "/" if os.path.isdir(full_path) else ""
                    output_lines.append(f"{entry}{suffix}")
                output = "\n".join(output_lines)

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
        """Search for a regex pattern in file contents, with permission checking."""
        search_path = path if path else os.getcwd()
        return self._check_and_execute(
            search_path, "read",
            lambda: self._grep_search_impl(pattern, path, glob, limit),
        )

    def _grep_search_impl(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        limit: Optional[int] = None
    ):
        result = ToolResult()

        if not pattern or not pattern.strip():
            result.set_output("Error: Pattern cannot be empty or whitespace-only")
            return result

        try:
            regex = re.compile(pattern)
        except re.error as e:
            result.set_output(f"Error: Invalid regular expression: {e}")
            return result

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

        glob_patterns = self._expand_glob_pattern(glob) if glob else []

        def _do_grep_search():
            matches = []
            match_count = 0

            def search_file(file_path: str) -> bool:
                nonlocal match_count
                try:
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
                    if not glob_patterns or self._file_matches_glob(os.path.basename(search_path), glob_patterns):
                        search_file(search_path)
                else:
                    for root, dirs, files in os.walk(search_path):
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
                return matches, None
            except Exception as e:
                return None, str(e)

        def _update_widget(matches, error):
            if error:
                result.set_output(f"Error during grep search: {error}")
                return

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

            widget.set_matches(matches)
            open_callback = self._get_open_in_editor_callback() if matches else None
            widget.open_in_editor_callback = open_callback

            result.set_output(output)

        open_callback = self._get_open_in_editor_callback()
        widget = GrepWidget(
            pattern=pattern,
            search_path=search_path,
            matches=[],
            open_in_editor_callback=None
        )
        result.set_widget(widget)

        def _run_search_thread():
            matches, error = _do_grep_search()
            GLib.idle_add(lambda: _update_widget(matches, error))

        thread = threading.Thread(target=_run_search_thread, daemon=True)
        thread.start()

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

        if not pattern or not pattern.strip():
            result.set_output("Error: Pattern cannot be empty or whitespace-only")
            return result

        try:
            regex = re.compile(pattern)
        except re.error as e:
            result.set_output(f"Error: Invalid regular expression: {e}")
            return result

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

        glob_patterns = self._expand_glob_pattern(glob) if glob else []

        def _do_grep_search():
            matches = []
            match_count = 0

            def search_file(file_path: str) -> bool:
                nonlocal match_count
                try:
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
                    if not glob_patterns or self._file_matches_glob(os.path.basename(search_path), glob_patterns):
                        search_file(search_path)
                else:
                    for root, dirs, files in os.walk(search_path):
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
                return matches, None
            except Exception as e:
                return None, str(e)

        def _update_widget(matches, error):
            if error:
                result.set_output(f"Error during grep search: {error}")
                return

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

            widget.set_matches(matches)
            open_callback = self._get_open_in_editor_callback() if matches else None
            widget.open_in_editor_callback = open_callback

            result.set_output(output)

        open_callback = self._get_open_in_editor_callback()
        widget = GrepWidget(
            pattern=pattern,
            search_path=search_path,
            matches=[],
            open_in_editor_callback=None
        )
        result.set_widget(widget)

        def _run_search_thread():
            matches, error = _do_grep_search()
            GLib.idle_add(lambda: _update_widget(matches, error))

        thread = threading.Thread(target=_run_search_thread, daemon=True)
        thread.start()

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

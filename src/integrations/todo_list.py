import json
import os
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
from gi.repository import Gtk, Pango

TODO_PROMPT = """The todo tool hasn't been used recently. If you're working on tasks that would benefit from tracking progress, consider using the todo tool to track progress. Also consider cleaning up the todo list if has become stale and no longer matches what you are working on. Only use it if it's relevant to the current work. This is just a gentle reminder - ignore if not applicable. Make sure that you NEVER mention this reminder to the user"""

class TodoListWidget(Gtk.Box):
    """Modern todo list widget with interactive checkboxes"""
    
    def __init__(self, todos: list, on_toggle_callback=None, title: str = "Tasks"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_size_request(-1, 200)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        
        self.on_toggle = on_toggle_callback
        
        # Custom CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .todo-header {
                padding: 12px 16px;
                background: alpha(@accent_bg_color, 0.08);
            }
            .todo-item {
                padding: 8px 16px;
                border-bottom: 1px solid alpha(@borders, 0.3);
            }
            .todo-item:last-child {
                border-bottom: none;
            }
            .todo-completed .todo-text {
                text-decoration: line-through;
                opacity: 0.5;
            }
            .phase-header {
                background: alpha(@view_bg_color, 0.5);
                padding: 6px 16px;
                font-weight: 600;
                font-size: 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Convert todos format: 'status' -> 'completed', 'title' -> 'text'
        converted_todos = []
        for todo in todos:
            status = todo.get("status", "not-started")
            completed = status == "completed"
            converted_todos.append({
                'text': todo.get('title', 'No title'),
                'completed': completed,
                'phase': todo.get('phase', 'Other')
            })
        
        completed_count = sum(1 for t in converted_todos if t.get('completed', False))
        total = len(converted_todos)
        
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("todo-header")
        
        icon = Gtk.Image.new_from_icon_name("checkbox-checked-symbolic")
        icon.set_pixel_size(18)
        icon.add_css_class("accent")
        header.append(icon)
        
        title_label = Gtk.Label(label=title, xalign=0, hexpand=True)
        title_label.add_css_class("heading")
        header.append(title_label)
        
        count_label = Gtk.Label(label=f"{completed_count}/{total}")
        count_label.add_css_class("caption")
        count_label.add_css_class("dim-label")
        header.append(count_label)
        
        self.append(header)
        
        # Group by phase
        phases = {}
        for todo in converted_todos:
            phase = todo.get('phase', 'Other')
            if phase not in phases:
                phases[phase] = []
            phases[phase].append(todo)
        
        # Create todo items
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        for phase, phase_todos in phases.items():
            if len(phases) > 1 and phase:
                # Phase header
                phase_header = Gtk.Label(label=phase, xalign=0)
                phase_header.add_css_class("phase-header")
                list_box.append(phase_header)
            
            for todo in phase_todos:
                row = self._create_todo_row(todo)
                list_box.append(row)
        
        # Scrolled container for long lists
        if len(converted_todos) > 5:
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_max_content_height(300)
            scrolled.set_propagate_natural_height(True)
            scrolled.set_child(list_box)
            self.append(scrolled)
        else:
            self.append(list_box)
    
    def _create_todo_row(self, todo: dict) -> Gtk.Box:
        """Create a single todo item row"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("todo-item")
        if todo.get('completed'):
            row.add_css_class("todo-completed")
        
        # Checkbox
        check = Gtk.CheckButton()
        check.set_active(todo.get('completed', False))
        check.set_valign(Gtk.Align.CENTER)
        check.set_sensitive(False)
        if self.on_toggle:
            check.connect("toggled", lambda btn, txt=todo.get('text', ''): self.on_toggle(txt, btn.get_active()))
        row.append(check)
        
        # Text
        text_label = Gtk.Label(
            label=todo.get('text', ''),
            xalign=0,
            hexpand=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        text_label.add_css_class("todo-text")
        row.append(text_label)
        
        return row


class TodoListIntegration(NewelleExtension):
    """Todo list integration for task management."""
    name = "Todo List"
    id = "todo_list"

    def __init__(self, pip_path: str, extension_path: str, settings, **kwargs):
        super().__init__(pip_path, extension_path, settings)
        self.todos = []
        self.all_chat_todos = {}
        self.cleared_chats = set()
        self.current_chat_id = "default"
        self._load_todos()

    def _get_cache_file_path(self) -> str:
        """Get the path to the todo cache file"""
        cache_dir = os.path.join(self.extension_path, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, "todos.json")

    def _load_todos(self):
        """Load todos from cache file"""
        try:
            cache_file = self._get_cache_file_path()
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    self.all_chat_todos = data.get('chat_todos', {})
                    self.cleared_chats = set(data.get('cleared_chats', []))
                self.todos = self._get_chat_todos(self.current_chat_id)
        except Exception as e:
            print(f"Error loading todos: {e}")
            self.all_chat_todos = {}
            self.cleared_chats = set()
            self.todos = []

    def _save_todos(self):
        """Save todos to cache file"""
        try:
            cache_file = self._get_cache_file_path()
            with open(cache_file, 'w') as f:
                json.dump({
                    'chat_todos': self.all_chat_todos,
                    'cleared_chats': list(self.cleared_chats)
                }, f, indent=2)
        except Exception as e:
            print(f"Error saving todos: {e}")

    def _set_chat_todos(self, chat_id: str, todos: list):
        """Set todos for a specific chat"""
        if not todos:
            self.cleared_chats.add(chat_id)
        else:
            self.all_chat_todos[chat_id] = todos
            if chat_id in self.cleared_chats:
                self.cleared_chats.remove(chat_id)
        self._save_todos()
        if chat_id == self.current_chat_id:
            self.todos = todos

    def _get_chat_todos(self, chat_id: str) -> list:
        """Get todos for a specific chat"""
        return self.all_chat_todos.get(chat_id, [])

    def _is_chat_cleared(self, chat_id: str) -> bool:
        """Check if a chat's todo list has been intentionally cleared"""
        return chat_id in self.cleared_chats


    def todo(self, todos: list):
        """Manage a todo list for the current session. Each todo should have a 'title' and 'status' (not-started, in-progress, completed)."""
        self.todos = todos
        res = "Current Todo List:\n"
        for i, t in enumerate(self.todos):
            status = t.get("status", "not-started")
            icon = "[ ]"
            if status == "in-progress":
                icon = "[/]"
            elif status == "completed":
                icon = "[x]"
            res += f"{i+1}. {icon} {t.get('title', 'No title')}\n"
        return res
    
    def _tool_todo(self, todos: list) -> ToolResult:
        """Tool wrapper for todo that returns both output and widget"""
        result = ToolResult()
        
        # Run operation synchronously
        output = self.todo(todos)
        result.set_output(output)
        
        # Save todos for current chat
        self._set_chat_todos(self.current_chat_id, todos)
        
        # Create widget after operation completes
        if self.todos:
            widget = TodoListWidget(self.todos, title="Tasks")
            result.set_widget(widget)
        
        return result
    
    def _restore_todo(self, todos: list, tool_uuid: str = None) -> ToolResult:
        """Restore todo widget on chat loading"""
        result = ToolResult()
        output = None
        
        # Try to get original output if tool_uuid is provided
        if tool_uuid and hasattr(self, 'ui_controller') and self.ui_controller:
            output = self.ui_controller.get_tool_result_by_id(tool_uuid)
        
        # Update internal state for current chat
        if todos:
            self.todos = todos
            self._set_chat_todos(self.current_chat_id, todos)
            widget = TodoListWidget(todos, title="Tasks")
            result.set_widget(widget)
        else:
            self._set_chat_todos(self.current_chat_id, [])
        
        result.set_output(output)
        return result

    def get_tools(self) -> list:
        return [
            Tool(
                name="todo",
                description="""Update the session's todo list. 
                Arguments:
                - todos: list of dicts. Each dict must have:
                    - title: Concise description of the task.
                    - status: 'not-started', 'in-progress', or 'completed'.""",
                func=self._tool_todo,
                title="Todo List",
                restore_func=self._restore_todo,
                tools_group="Coding"
            )
        ]

    def preprocess_history(self, history: list, prompts: list) -> tuple[list, list]:
        print(history)
        for i, prompt in enumerate(prompts):
            if "{TODOLIST}" in prompt:
                todolist_text = self._format_todolist_for_prompt()
                prompts[i] = prompt.replace("{TODOLIST}", todolist_text)
        has_tool_execution = False 
        last_tool_execution = 0
        for h in history:
            if "Tool: todo," in h["Message"]:
                has_tool_execution = True
                last_tool_execution = 0
            if has_tool_execution:
                last_tool_execution += 1
        # Only add the TODO prompt if the list is not cleared
        if has_tool_execution and last_tool_execution > 10 and not self._is_chat_cleared(self.current_chat_id):
            prompts.append(TODO_PROMPT)     
         
        return history, prompts

    def _format_todolist_for_prompt(self) -> str:
        """Format the current todo list for inclusion in prompts"""
        if not self.todos:
            return "No active tasks"
        
        todolist_str = "Current Tasks:\n"
        for i, t in enumerate(self.todos):
            status = t.get("status", "not-started")
            icon = "[ ]"
            if status == "in-progress":
                icon = "[/]"
            elif status == "completed":
                icon = "[x]"
            todolist_str += f"{i+1}. {icon} {t.get('title', 'No title')}\n"
        
        return todolist_str

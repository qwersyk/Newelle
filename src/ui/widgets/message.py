import threading
import uuid
import inspect
import base64
import json
import os
import re
from gi.repository import Gtk, GLib, Pango, GdkPixbuf, Gio, Gdk

from ...utility.message_chunk import get_message_chunks, MessageChunk
from ...utility.strings import markwon_to_pango, remove_thinking_blocks, simple_markdown_to_pango
from pylatexenc.latex2text import LatexNodes2Text

from .copybox import CopyBox
from .thinking import ThinkingWidget
from .latex import DisplayLatex, InlineLatex
from .barchart import BarChartBox
from .markuptextview import MarkupTextView
from .tool import ToolWidget
from ...ui import apply_css_to_widget, load_image_with_callback

class Message(Gtk.Box):
    def __init__(self, message: str, is_user: bool, parent_window, id_message: int = -1, chunk_uuid = None, restore=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.message = message
        self.is_user = is_user
        self.parent_window = parent_window
        self.controller = parent_window.controller
        self.id_message = id_message
        self.chunk_uuid = chunk_uuid if chunk_uuid else (uuid.uuid4().int if not restore else 0)
        self.restore = restore
        self.thinking_widget = None
        # State tracking
        self.widgets_map = [] # List of tuples (chunk_type, widget, chunk_data)
        self.streaming = False
        self.state = {
            "codeblock_id": -1,
            "id_message": id_message,
            "original_id": id_message,
            "editable": True,
            "has_terminal_command": False,
            "running_threads": [],
            "tool_call_counter": 0,
            "should_continue": False,
        }
        
        # Styling
        self.set_margin_top(10)
        self.set_margin_start(10)
        self.set_margin_bottom(10)
        self.set_margin_end(10)
        
        if is_user:
            self.add_css_class("user-message")
        else:
            self.add_css_class("assistant-message")
            
        # Initial render
        self.update_content(message)

    def update_content(self, message: str, is_streaming: bool = False):
        """Update the message content, intelligently updating widgets."""
        self.message = message
        self.streaming = is_streaming
        
        chunks = get_message_chunks(message, allow_latex=self.controller.newelle_settings.display_latex)
        
        current_widget_idx = 0
        
        # Make a copy of state to simulate processing
        temp_state = self.state.copy()
        temp_state["codeblock_id"] = -1 # Reset for this pass
        
        for i, chunk in enumerate(chunks):
            # matches existing widget?
            if current_widget_idx < len(self.widgets_map):
                w_type, widget, w_data = self.widgets_map[current_widget_idx]
                
                # Check if we can update
                if self._can_update_widget(w_type, widget, chunk):
                    self._update_widget(widget, w_type, chunk)
                    self.widgets_map[current_widget_idx] = (chunk.type, widget, chunk)
                    
                    # Update state based on this chunk (accumulate side effects)
                    self._simulate_state_update(chunk, temp_state)
                    
                    current_widget_idx += 1
                    continue
                else:
                    # Remove mismatch and following
                    self._remove_widgets_from(current_widget_idx)
            
            # Create new widget
            self._process_chunk(chunk, self, temp_state, self.restore, self.is_user, self.chunk_uuid)
            
            current_widget_idx = len(self.widgets_map)
        
        # Update state
        self.state = temp_state
        
        # Remove any remaining widgets if chunk list shorter
        if current_widget_idx < len(self.widgets_map):
             self._remove_widgets_from(current_widget_idx)

    def append(self, widget):
        super().append(widget)

    def _remove_widgets_from(self, start_index):
        while len(self.widgets_map) > start_index:
            w_type, widget_or_list, _ = self.widgets_map.pop()
            if isinstance(widget_or_list, list):
                for w in widget_or_list:
                    self.remove(w)
            else:
                self.remove(widget_or_list)

    def _can_update_widget(self, w_type, widget_or_list, new_chunk):
        if w_type == "complex": return False
        
        widget = widget_or_list
        if w_type != new_chunk.type: return False
        if w_type == "text": return True
        if w_type == "codeblock":
            if isinstance(widget, CopyBox):
                if new_chunk.lang in ["video", "image", "chart", "file", "folder"]: return False
                return True
            return False
        if w_type == "thinking": return True
        return False

    def _update_widget(self, widget, w_type, new_chunk):
        if w_type == "text":
            if widget.get_label() != new_chunk.text:
                widget.set_markup(markwon_to_pango(new_chunk.text))
        elif w_type == "codeblock":
            if isinstance(widget, CopyBox):
                widget.update_code(new_chunk.text)
                widget.set_language(new_chunk.lang)
        elif w_type == "thinking":
            widget.set_thinking(new_chunk.text)

    def _simulate_state_update(self, chunk, state):
        if chunk.type == "codeblock":
            state["codeblock_id"] += 1

    def _process_chunk(self, chunk, box, state, restore, is_user, msg_uuid):
        start_children = self.observe_children()
        
        # Real logic
        if chunk.type == "codeblock":
            self._process_codeblock(chunk, box, state, restore, is_user, msg_uuid)
        elif chunk.type == "tool_call":
            self._process_tool_call(chunk, box, state, restore)
        elif chunk.type == "table":
            self._process_table(chunk, box)
        elif chunk.type == "inline_chunks":
            self._process_inline_chunks(chunk, box)
        elif chunk.type in ("latex", "latex_inline"):
            self._process_latex(chunk, box)
        elif chunk.type == "thinking":
            think = ThinkingWidget()
            self.thinking_widget = think
            think.start_thinking(chunk.text)
            box.append(think)
            self._queue_execution(lambda: think.stop_thinking())
        elif chunk.type == "text":
            self._process_text(chunk, box)
            
        # Capture added widgets
        end_children = self.observe_children()
        new_widgets = [c for c in end_children if c not in start_children]
        
        if len(new_widgets) == 1:
             self.widgets_map.append((chunk.type, new_widgets[0], chunk))
        elif len(new_widgets) > 1:
            self.widgets_map.append(("complex", new_widgets, chunk))
        else:
            pass # Nothing added
        
    def observe_children(self):
        children = []
        child = self.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        return children

    # --- Process Methods (Copied & Adapted from window.py) ---

    def _process_text(self, chunk, box):
        if chunk.text == ".": return
        box.append(Gtk.Label(
            label=markwon_to_pango(chunk.text),
            wrap=True,
            halign=Gtk.Align.START,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            width_chars=1,
            selectable=True,
            use_markup=True,
        ))

    def _process_codeblock(self, chunk, box, state, restore, is_user, msg_uuid):
        state["codeblock_id"] += 1
        codeblock_id = state["codeblock_id"]
        lang = chunk.lang
        text = chunk.text
        
        codeblocks = {**self.controller.extensionloader.codeblocks, **self.controller.integrationsloader.codeblocks}
        
        if lang in codeblocks:
            self._process_extension_codeblock(chunk, box, state, restore, msg_uuid, codeblocks[lang])
        elif lang == "think":
            think = ThinkingWidget()
            think.set_thinking(text)
            box.append(think)
        elif lang == "image":
            self._process_image_codeblock(text, box)
        elif lang == "video":
            self._process_video_codeblock(text, box)
        elif lang == "console" and not is_user:
            self._process_console_codeblock(chunk, box, state, restore)
        elif lang in ("file", "folder"):
            for obj in text.split("\n"):
                if obj.strip():
                     box.append(self.parent_window.get_file_button(obj))
        elif lang == "chart" and not is_user:
            self._process_chart_codeblock(chunk, box)
        elif lang == "latex":
            try:
                box.append(DisplayLatex(text, 16, self.controller.cache_dir))
            except Exception:
                box.append(CopyBox(text, lang, parent=self.parent_window, id_message=state["id_message"], id_codeblock=codeblock_id, allow_edit=state["editable"]))
        else:
            box.append(CopyBox(text, lang, parent=self.parent_window, id_message=state["id_message"], id_codeblock=codeblock_id, allow_edit=state["editable"]))

    def _process_image_codeblock(self, text, box):
        for line in text.split("\n"):
            if not line.strip(): continue
            image = Gtk.Image(css_classes=["image"])
            if line.startswith("data:image/jpeg;base64,"):
                try:
                    data = line[len("data:image/jpeg;base64,"):]
                    raw_data = base64.b64decode(data)
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(raw_data)
                    loader.close()
                    image.set_from_pixbuf(loader.get_pixbuf())
                    box.append(image)
                except: pass
            elif line.startswith(("https://", "http://")):
                img = image
                load_image_with_callback(line, lambda pixbuf_loader, i=img: i.set_from_pixbuf(pixbuf_loader.get_pixbuf()))
                box.append(image)
            else:
                image.set_from_file(line)
                box.append(image)

    def _process_video_codeblock(self, text, box):
        for line in text.split("\n"):
            if not line.strip(): continue
            video = Gtk.Video(css_classes=["video"], vexpand=True, hexpand=True)
            video.set_size_request(-1, 400)
            video.set_file(Gio.File.new_for_path(line))
            box.append(video)

    def _process_chart_codeblock(self, chunk, box):
        result = {}
        percentages = False
        for line in chunk.text.split("\n"):
            parts = line.split("-")
            if len(parts) != 2:
                box.append(CopyBox(chunk.text, "chart", parent=self.parent_window))
                return
            key = parts[0].strip()
            percentages = "%" in parts[1]
            value_str = "".join(c for c in parts[1] if c.isdigit() or c == ".")
            try: result[key] = float(value_str)
            except ValueError: result[key] = 0
        if result:
            box.append(BarChartBox(result, percentages))

    def _process_latex(self, chunk, box):
        try:
            box.append(DisplayLatex(chunk.text, 16, self.controller.cache_dir))
        except Exception:
            box.append(CopyBox(chunk.text, "latex", parent=self.parent_window))

    def _process_inline_chunks(self, chunk, box):
        if not chunk.subchunks: return
        overlay = Gtk.Overlay()
        label = Gtk.Label(label=" ".join(ch.text for ch in chunk.subchunks), wrap=True)
        label.set_opacity(0)
        overlay.set_child(label)
        textview = MarkupTextView()
        textview.set_valign(Gtk.Align.START)
        textview.set_hexpand(True)
        overlay.add_overlay(textview)
        overlay.set_measure_overlay(textview, True)
        
        # New logic: Join and process markdown across chunks
        full_markdown = ""
        widgets_dict = {}
        
        for i, subchunk in enumerate(chunk.subchunks):
            if subchunk.type == "text":
                full_markdown += subchunk.text
            elif subchunk.type == "latex_inline":
                placeholder = f"WZIDZW{i}WZIDZW"
                full_markdown += placeholder
                
                try:
                    font_size = int(5 + (self.controller.newelle_settings.zoom / 100 * 4))
                    latex = InlineLatex(subchunk.text, font_size)
                    latex_overlay = Gtk.Overlay()
                    latex_overlay.add_overlay(latex)
                    spacer = Gtk.Box()
                    spacer.set_size_request(latex.picture.dims[0], latex.picture.dims[1] + 1)
                    latex_overlay.set_child(spacer)
                    latex.set_margin_top(5)
                    widgets_dict[str(i)] = latex_overlay
                except Exception:
                    # Fallback if latex fails: use the text representation
                    # We remove the placeholder and just add the text representation
                    full_markdown = full_markdown[:-len(placeholder)]
                    full_markdown += LatexNodes2Text().latex_to_text(subchunk.text)
        
        full_markup = markwon_to_pango(full_markdown)
        
        # Replace placeholders with <widget> tags in the pango markup
        # Note: we use regex to find placeholders because they might be inside tags
        processed_markup = re.sub(r'WZIDZW(\d+)WZIDZW', r'<widget id="\1"/>', full_markup)
        
        buffer = textview.get_buffer()
        text_iter = buffer.get_start_iter()
        textview.add_markup_text(text_iter, processed_markup, widgets=widgets_dict)
        
        box.append(overlay)

    def _process_extension_codeblock(self, chunk, box, state, restore, msg_uuid, extension):
        lang = chunk.lang
        value = chunk.text
        try:
            sig = inspect.signature(extension.get_gtk_widget)
            supports_uuid = len(sig.parameters) == 3
            if restore:
                widget = (extension.restore_gtk_widget(value, lang, msg_uuid) if supports_uuid else extension.restore_gtk_widget(value, lang))
            else:
                widget = (extension.get_gtk_widget(value, lang, msg_uuid) if supports_uuid else extension.get_gtk_widget(value, lang))
            
            if widget: box.append(widget)
            
            if widget is None or extension.provides_both_widget_and_answer(value, lang):
                 self._setup_extension_async_response(chunk, box, state, restore, extension, widget)
        except Exception as e:
            box.append(CopyBox(chunk.text, lang, parent=self.parent_window, id_message=state["id_message"], id_codeblock=state["codeblock_id"], allow_edit=state["editable"]))

    def _process_console_codeblock(self, chunk, box, state, restore):
        # Defer execution if streaming
        state["id_message"] += 1
        command = chunk.text
        dangerous_commands = ["rm ", "apt ", "sudo ", "yum ", "mkfs "]
        can_auto_run = (self.controller.newelle_settings.auto_run and not any(cmd in command for cmd in dangerous_commands) and self.parent_window.auto_run_times < self.controller.newelle_settings.max_run_times)
        
        if can_auto_run:
            state["has_terminal_command"] = True
            text_expander = Gtk.Expander(label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10, margin_end=10)
            text_expander.set_expanded(False)
            box.append(text_expander)
            
            reply_from_console = self.parent_window._get_console_reply(state["id_message"]) # Keep helper in parent?
            
            # Logic for deferred execution
            self._queue_execution(lambda: self._run_console_command(command, restore, reply_from_console, text_expander, state))
            
            if not restore:
                self.parent_window.auto_run_times += 1
        else:
            if not restore:
                 self.controller.chat.append({"User": "Console", "Message": "None"})
            box.append(CopyBox(command, "console", self.parent_window, state["id_message"], id_codeblock=state["codeblock_id"], allow_edit=state["editable"]))

    def _process_tool_call(self, chunk, box, state, restore):
        tool_name = chunk.tool_name
        args = chunk.tool_args
        tool = self.controller.tools.get_tool(tool_name)
        state["id_message"] += 1
        if not restore: self.controller.msgid = state["id_message"]
        
        if not tool:
            box.append(CopyBox(chunk.text, "tool_call", parent=self.parent_window))
            return

        tool_call_id = state.get("tool_call_counter", 0)
        state["tool_call_counter"] = tool_call_id + 1
        
        if not restore:
            tool_uuid = str(uuid.uuid4())[:8]
        else:
            tool_uuid = self.parent_window._get_tool_call_uuid(state["id_message"], tool_name, tool_call_id)
        
        state["has_terminal_command"] = True
        self.controller.current_tool_uuid = tool_uuid
        
        try:
              
             placeholder = ToolWidget(tool.name, chunk.text)
             box.append(placeholder)
             
             # We pass placeholder to runner
             self._queue_execution(lambda: self._run_tool_call_with_placeholder(tool, args, tool_uuid, state, restore, placeholder, chunk))
             
        except Exception as e:
            print(f"Tool error: {e}")

    def _run_tool_call_with_placeholder(self, tool, args, tool_uuid, state, restore, placeholder, chunk):
        state["has_terminal_command"] = True
        self.controller.msgid = state["id_message"]
        
        def run_tool():
            try:
                if restore:
                    result = tool.restore(msg_id=state["id_message"], tool_uuid=tool_uuid, **args)
                else:
                    result = tool.execute(**args)
                
                if not restore:
                    # Append result to active tool results in main thread if needed
                    self.parent_window.active_tool_results.append(result)
                
                widget = result.widget
                if widget:
                    # Tool wants custom widget. Placeholder was ToolWidget.
                    def swap_widget():
                        parent = placeholder.get_parent()
                        if parent and parent.get_display():
                            parent.remove(placeholder)
                            parent.append(widget)
                    GLib.idle_add(swap_widget)
                    
                    # Handle result closure
                    def on_result(code):
                        if not code[0]: 
                            pass # Handle error
                else:
                    # Use placeholder (ToolWidget)
                    def on_result(code):
                        placeholder.set_result(code[0], code[1])
                
                reply_from_console = self.parent_window._get_tool_response(state["id_message"], tool.name, tool_uuid)
                def get_response(reply_from_console):
                    if not restore:
                        response = result.get_output()
                        if not restore:
                            try: self.parent_window.active_tool_results.remove(result)
                            except: pass
                        if result.is_cancelled: return
                        if response is None: code = (True, None)
                        else:
                            state["should_continue"] = True
                            code = (True, response)
                            formatted = f"[Tool: {tool.name}, ID: {tool_uuid}]\n{code[1]}"
                            self.controller.chat.append({"User": "Console", "Message": formatted})
                    else:
                        code = (True, reply_from_console)
                    
                    if not restore or code[1] is not None:
                        GLib.idle_add(on_result, code)
 
                t = threading.Thread(target=get_response, args=(reply_from_console,))
                state["running_threads"].append(t)
                if self.controller.newelle_settings.parallel_tool_execution or restore:
                    t.start()
            except Exception as e:
                print(f"Error running tool: {e}")

        run_tool()

    def _process_table(self, chunk, box):
        try:
            box.append(self.create_table(chunk.text.split("\n")))
        except Exception as e:
            print(e)
            box.append(CopyBox(chunk.text, "table", parent=self.parent_window))

    def create_table(self, table):
        data = []
        for row in table:
            cells = row.strip("|").split("|")
            data.append([cell.strip() for cell in cells])
        model = Gtk.ListStore(*[str] * len(data[0]))
        for row in data[1:]:
            if not all(len(element.replace(":", "").replace(" ", "").replace("-", "").strip()) == 0 for element in row):
                # Ensure row matches number of columns
                num_columns = len(data[0])
                if len(row) < num_columns:
                    row.extend([""] * (num_columns - len(row)))
                elif len(row) > num_columns:
                    row = row[:num_columns]
                
                r = []
                for element in row: 
                    r.append(simple_markdown_to_pango(LatexNodes2Text().latex_to_text(element)))
                model.append(r)
        treeview = Gtk.TreeView(model=model, css_classes=["toolbar", "view", "transparent"])

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, markup=i)
            treeview.append_column(column)
        scroll = Gtk.ScrolledWindow(child=treeview, propagate_natural_height=True, propagate_natural_width=True, vscrollbar_policy=Gtk.PolicyType.NEVER)
        return scroll

    # --- Execution Logic ---

    def _queue_execution(self, func):
        if self.streaming:
            # Queue
            self.state.setdefault("pending_executions", []).append(func)
        else:
            # Run immediately (restore or not streaming)
            func()

    def finish_streaming(self):
        """Called when streaming finishes to execute pending side effects."""
        self.streaming = False
        
        if self.thinking_widget:
            self.thinking_widget.stop_thinking()
            self.thinking_widget = None
        
        if "pending_executions" in self.state:
            for func in self.state["pending_executions"]:
                func()
            self.state["pending_executions"] = []

    def _run_console_command(self, cmd, restore, console_reply, expander, state):
         # Logic from window.py _process_console_codeblock closure
         def run_command():
            if not restore:
                code = self.parent_window.execute_terminal_command(cmd)
                self.controller.chat.append({"User": "Console", "Message": " " + str(code[1])})
            else:
                 code = (True, console_reply)
            
            text = f"[User {self.parent_window.main_path}]:$ {cmd}\n{code[1]}"
            def apply_result():
                 expander.set_child(Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=text[:8000], selectable=True))
                 if not code[0]:
                      # Error
                      pass
            GLib.idle_add(apply_result)
         
         t = threading.Thread(target=run_command)
         if self.controller.newelle_settings.parallel_tool_execution or restore:
             t.start()
         state["running_threads"].append(t)


    def _setup_extension_async_response(self, chunk, box, state, restore, extension, widget):
        lang = chunk.lang
        value = chunk.text
        state["has_terminal_command"] = True
        state["id_message"] += 1
        reply_from_console = self.parent_window._get_console_reply(state["id_message"])
        
        if widget:
             def on_result(code):
                  if not code[0]: pass # Error
        else:
             text_expander = Gtk.Expander(label=lang, css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10, margin_end=10)
             text_expander.set_expanded(False)
             box.append(text_expander)
             def on_result(code):
                  text_expander.set_child(Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=f"{value}\n{code[1]}", selectable=True))

        def get_response():
             if not restore:
                  response = extension.get_answer(value, lang)
                  if response is None: code = (False, _("Stopped"))
                  else:
                       state["should_continue"] = True
                       code = (True, response)
             else:
                  code = (True, reply_from_console)
             
             if not restore or code[1] is not None:
                  GLib.idle_add(on_result, code)
        
        def run_extension():
            t = threading.Thread(target=get_response)
            t.start()
            state["running_threads"].append(t)

        self._queue_execution(run_extension)

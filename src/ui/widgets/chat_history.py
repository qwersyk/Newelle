from gi.repository import Gtk, GObject, Pango, Gio, GLib, Adw, Gdk
import threading
import time
import uuid
import os
import subprocess
import gettext
from ...utility.system import open_website
from ..widgets import TipsCarousel
from ...utility.strings import markwon_to_pango
from ...ui.widgets import Message, MultilineEntry

_ = gettext.gettext
SCHEMA_ID = "io.github.qwersyk.Newelle"


class ChatHistory(Gtk.Box):
    __gsignals__ = {
        "focus-input": (GObject.SignalFlags.RUN_LAST, None, ()),
        "branch-requested": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_INT,)),
        "clear-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "continue-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "regenerate-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "stop-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "files-dropped": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, window, chat, chat_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["background", "view"], vexpand=True)
        self.status = True
        self.window = window
        self.chat_id = chat_id
        self.controller = window.controller
        
        # Lazy loading state
        self.lazy_load_enabled = True
        self.lazy_load_batch_size = 10  # Number of messages to load initially and per batch
        self.lazy_load_threshold = 0.1  # Load more when within 10% of top/bottom
        self.lazy_loaded_start = 0  # First loaded message index
        self.lazy_loaded_end = 0  # Last loaded message index (exclusive)
        self.lazy_loading_in_progress = False
        self.scroll_handler_id = None  # Store scroll handler ID to disconnect when needed

        self.messages_box = []
        self.edit_entries = {}
        self.last_error_box = None
        # Suggestions vars
        self.message_suggestion_buttons_array = []
        self.message_suggestion_buttons_array_placeholder = []
        # Show history/placeholder
        self.history_block = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_DOWN, transition_duration=300)
        self._add_drag_and_drop()

        # Offers
        self.offers_entry_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.FILL,
            margin_bottom=6,
        )

        self.append(self.offers_entry_block)

        # Add history
        self.chat_scroll = Gtk.ScrolledWindow(vexpand=True)
        self.history_block.add_named(self.chat_scroll, "history")
        self.chat_list_block = Gtk.ListBox(
            css_classes=["separators", "background", "view"]
        )
        self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chat_scroll.set_child(self.chat_list_block)
        # Add placeholder
        self.build_placeholder()
        self.history_block.add_named(self.empty_chat_placeholder, "placeholder")
        self.history_block.set_visible_child_name("history" if len(self.chat) > 0 else "placeholder") 
        self.append(self.history_block)
        # Chat controls
        self.chat_controls_entry_block = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.CENTER,
            margin_top=6,
            margin_bottom=6,
        )
        self.append(self.chat_controls_entry_block)

        # Add buttons
        self._build_buttons()

    def show_placeholder(self):
        self.history_block.set_visible_child_name("placeholder")
        self.tips_section.shuffle_tips()

    def hide_placeholder(self):
        self.history_block.set_visible_child_name("history")

    def focus_input(self):
        self.emit("focus-input")

    def set_generating(self, generating: bool):
        self.status = not generating
        self.update_button_text()

    def populate_chat(self):
        if not self.controller.newelle_settings.virtualization:
            self.add_message("WarningNoVirtual")
        else:
            self.add_message("Disclaimer")
        total_messages = len(self.chat)
        if self.scroll_handler_id is not None:
            adjustment = self.chat_scroll.get_vadjustment()
            adjustment.disconnect(self.scroll_handler_id)
        adjustment = self.chat_scroll.get_vadjustment()
        self.scroll_handler_id = adjustment.connect("value-changed", self._on_scroll_changed)
        # Lazy load if
        if self.lazy_load_enabled and total_messages > self.lazy_load_batch_size:
            # Load only the last batch_size messages initially
            # Messages are indexed from 0 (oldest) to len-1 (newest)
            start_idx = max(0, total_messages - self.lazy_load_batch_size)
            self.lazy_loaded_start = start_idx
            self.lazy_loaded_end = total_messages
            self._load_message_range(start_idx, total_messages)
        else:
            self.lazy_loaded_start = 0
            self.lazy_loaded_end = total_messages
            for i in range(len(self.chat)):
                if self.chat[i]["User"] == "User":
                    self.show_message(self.chat[i]["Message"], True, id_message=i, is_user=True)
                elif self.chat[i]["User"] == "Assistant":
                    self.show_message(self.chat[i]["Message"], True, id_message=i)
                elif self.chat[i]["User"] in ["File", "Folder"]:
                    self.add_message(self.chat[i]["User"], self.get_file_button(self.chat[i]["Message"][1 : len(self.chat[i]["Message"])]))
        GLib.idle_add(self.scrolled_chat)
        GLib.idle_add(self.update_button_text)

    def scrolled_chat(self):
        """Scroll at the bottom of the chat"""
        adjustment = self.chat_scroll.get_vadjustment()
        adjustment.set_value(100000)

    def update_button_text(self):
        """Update clear chat, regenerate message and continue buttons, add offers"""
        for btn in self.message_suggestion_buttons_array + self.message_suggestion_buttons_array_placeholder:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
        self.chat_stop_button.set_visible(False)
        if self.status:
            if self.chat != []:
                self.button_clear.set_visible(True)
                if (
                    self.chat[-1]["User"] in ["Assistant", "Console"]
                    or self.last_error_box is not None
                ):
                    self.regenerate_message_button.set_visible(True)
                elif self.chat[-1]["User"] in ["Assistant", "Console", "User"]:
                    self.button_continue.set_visible(True)
        else:
            for btn in self.message_suggestion_buttons_array + self.message_suggestion_buttons_array_placeholder:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.regenerate_message_button.set_visible(False)
            self.chat_stop_button.set_visible(True)
        GLib.idle_add(self.scrolled_chat)

    def _add_drag_and_drop(self):
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.history_block.add_controller(drop_target)
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.history_block.add_controller(drop_target)

    def _on_drop(self, drop_target, value, x, y):
        """Handle drop event and emit files-dropped signal for the window to process."""
        self.emit("files-dropped", value)
        return True

    def build_offers(self):
        """Build offers buttons, called by update_settings to update the number of buttons"""
        for text in range(self.offers):
            def create_button():
                button = Gtk.Button(css_classes=["flat"], margin_start=6, margin_end=6)
                label = Gtk.Label(label=str(text), wrap=True, wrap_mode=Pango.WrapMode.CHAR, ellipsize=Pango.EllipsizeMode.END)
                button.set_child(label)
                button.connect("clicked", self.send_bot_response)
                button.set_visible(False)
                return button
            button = create_button()
            button_placeholder = create_button()
            self.offers_entry_block.append(button)
            self.message_suggestion_buttons_array.append(button)
            self.offers_entry_block_placeholder.append(button_placeholder)
            self.message_suggestion_buttons_array_placeholder.append(button_placeholder)
    
    def _build_buttons(self):
        # Stop chat button
        self.chat_stop_button = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-stop"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Stop"))
        box.append(label)
        self.chat_stop_button.set_child(box)
        self.chat_stop_button.connect("clicked", lambda btn: self.emit("stop-requested"))
        self.chat_stop_button.set_visible(False)

        self.chat_controls_entry_block.append(self.chat_stop_button)
        self.status = True
        # Clear chat button
        self.button_clear = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-clear-all-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Clear"))
        box.append(label)
        self.button_clear.set_child(box)
        self.button_clear.connect("clicked", lambda btn: self.emit("clear-requested"))
        self.button_clear.set_visible(False)
        self.chat_controls_entry_block.append(self.button_clear)

        # Continue button
        self.button_continue = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(
            Gio.ThemedIcon(name="media-seek-forward-symbolic")
        )
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Continue"))
        box.append(label)
        self.button_continue.set_child(box)
        self.button_continue.connect("clicked", lambda btn: self.emit("continue-requested"))
        self.button_continue.set_visible(False)
        self.chat_controls_entry_block.append(self.button_continue)

        # Regenerate message button
        self.regenerate_message_button = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Regenerate"))
        box.append(label)
        self.regenerate_message_button.set_child(box)
        self.regenerate_message_button.connect("clicked", lambda btn: self.emit("regenerate-requested"))
        self.regenerate_message_button.set_visible(False)
        self.chat_controls_entry_block.append(self.regenerate_message_button)

    def build_placeholder(self):
        tips = [
            {"title": _("Ask about a website"), "subtitle": _("Write #https://website.com in chat to ask information about a website"), "on_click": lambda : self.send_bot_response(Gtk.Button(label="#https://github.com/qwersyk/Newelle\nWhat is Newelle?"))},
            {"title": _("Check out our Extensions!"), "subtitle": _("We have a lot of extensions for different things. Check it out!"), "on_click": lambda: self.app.extension_action()},
            {"title": _("Chat with documents!"), "subtitle": _("Add your documents to your documents folder and chat using the information contained in them!"), "on_click": lambda : self.app.settings_action_paged("Memory")},
            {"title": _("Surf the web!"), "subtitle": _("Enable web search to allow the LLM to surf the web and provide up to date answers"), "on_click": lambda : self.app.settings_action_paged("Memory")},
            {"title": _("Mini Window"), "subtitle": _("Ask questions on the fly using the mini window mode"), "on_click": lambda : open_website("https://github.com/qwersyk/Newelle/?tab=readme-ov-file#mini-window-mode")},
            {"title": _("Text to Speech"), "subtitle": _("Newelle supports text-to-speech! Enable it in the settings"), "on_click": lambda : self.app.settings_action_paged("General")},
            {"title": _("Keyboard Shortcuts"), "subtitle": _("Control Newelle using Keyboard Shortcuts"), "on_click": lambda : self.app.on_shortcuts_action()},
            {"title": _("Prompt Control"), "subtitle": _("Newelle gives you 100% prompt control. Tune your prompts for your use."), "on_click": lambda : self.app.settings_action_paged("Prompts")},
            {"title": _("Thread Editing"), "subtitle": _("Check the programs and processes you run from Newelle"), "on_click": lambda : self.app.thread_editing_action()},
            {"title": _("Programmable Prompts"), "subtitle": _("You can add dynamic prompts to Newelle, with conditions and probabilities"), "on_click": lambda : open_website("https://github.com/qwersyk/Newelle/wiki/Prompt-variables")},
        ]
        self.empty_chat_placeholder = Gtk.Box(hexpand=True, vexpand=True, orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, spacing=20, vexpand=True)    
        application_logo = Gtk.Image(icon_name=SCHEMA_ID)
        application_logo.set_pixel_size(128)
        box.append(application_logo)
        title_label = Gtk.Label(label=_("New Chat"), css_classes=["title-1"])
        box.append(title_label)
        self.tips_section = TipsCarousel(tips, 5)
        box.append(self.tips_section)
        self.empty_chat_placeholder.append(box)
        # Placeholder offers 
        self.offers_entry_block_placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.CENTER,
            margin_bottom=6,
        )
        self.offers_entry_block_placeholder.set_size_request(-1, 36*self.controller.newelle_settings.offers)
        self.empty_chat_placeholder.append(self.offers_entry_block_placeholder)



    def _finalize_message_display(self):
        """Update UI state after message display."""
        GLib.idle_add(self.update_button_text)
        self.status = True
        self.chat_stop_button.set_visible(False)
    
    # Message display functions 
    def show_message(
        self,
        message_label,
        restore=False,
        id_message=-1,
        is_user=False,
        return_widget=False,
        newelle_error=False,
        prompt: str | None = None,
    ):
        """Show a message in the chat."""
        if id_message == -1:
            id_message = len(self.chat)
        self.hide_placeholder()
        # Handle empty/whitespace messages
        if message_label == " " * len(message_label) and not is_user:
            if not restore:
                self.chat.append({"User": "Assistant", "Message": message_label})
                self.add_prompt(prompt)
                self._finalize_message_display()
            GLib.idle_add(self.scrolled_chat)
            self.controller.save_chats()
            return None

        # Handle error messages
        if newelle_error:
            if not restore:
                self._finalize_message_display()
            self.last_error_box = self.add_message(
                "Error",
                Gtk.Label(
                    label=markwon_to_pango(message_label),
                    use_markup=True,
                    wrap=True,
                    margin_top=10,
                    margin_end=10,
                    margin_bottom=10,
                    margin_start=10,
                    selectable=True
                ),
            )
            GLib.idle_add(self.scrolled_chat)
            self.controller.save_chats()
            return None

        # Initialize message UUID for assistant messages
        msg_uuid = 0
        if not is_user:
            if not restore:
                msg_uuid = int(uuid.uuid4())
                self.chat.append({"User": "Assistant", "Message": message_label, "UUID": msg_uuid})
                self.add_prompt(prompt)
            else:
                msg_uuid = self.chat[id_message].get("UUID", 0)

        # Create Message widget
        # Note: Message widget acts as the 'box' that was previously built manually
        message_widget = Message(
            message_label, 
            is_user, 
            self, 
            id_message=id_message, 
            chunk_uuid=msg_uuid, 
            restore=restore
        )

        if return_widget:
            return message_widget
            
        self.add_message("User" if is_user else "Assistant", message_widget, id_message=id_message, editable=True)

        if not restore:
            self._finalize_message_display()
            self.controller.save_chats()
            
        return None
    
    
    def add_message(self, user, message=None, id_message=0, editable=False):
        """Add a message to the chat and return the box

        Args:
            user (): if the message is send by a user
            message (): message label
            id_message (): id of the message
            editable (): if the message is editable
        Returns:
           message box
        """
        box = Gtk.Box(
            css_classes=["card"],
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            halign=Gtk.Align.FILL,
        )
        self.messages_box.append(box)

        # Update lazy_loaded_end when a message is displayed beyond the current range
        if False and self.lazy_load_enabled:
            if id_message >= self.lazy_loaded_end:
                self.lazy_loaded_end = id_message + 1

        # Create overlay for branch button positioning
        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        box.append(overlay)

        # Create content box to hold message content (horizontal layout)
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        overlay.set_child(content_box)

        # Create edit controls
        if editable:
            apply_edit_stack, branch_button = self.build_edit_box(box, str(id_message), user == "Assistant")
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message, box, apply_edit_stack)
            evk.set_name(str(id_message))
            evk.set_button(3)
            box.add_controller(evk)
            ev = Gtk.EventControllerMotion.new()

            stack = Gtk.Stack()
            ev.connect("enter", lambda x, y, data: (stack.set_visible_child_name("edit"), branch_button.set_visible(True)))
            ev.connect("leave", lambda data: (stack.set_visible_child_name("label"), branch_button.set_visible(False)))
            box.add_controller(ev)

            # Add branch button to overlay (bottom right positioning)
            branch_button.set_visible(False)
            branch_button.set_halign(Gtk.Align.END)
            branch_button.set_valign(Gtk.Align.END)
            branch_button.set_margin_end(10)
            branch_button.set_margin_bottom(10)
            overlay.add_overlay(branch_button)

        if user == "User":
            label = Gtk.Label(
                label=self.controller.newelle_settings.username + ": ",
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=0,
                css_classes=["accent", "heading"],
            )
            if editable:
                stack.add_named(label, "label")
                stack.add_named(apply_edit_stack, "edit")
                stack.set_visible_child_name("label")
                content_box.append(stack)
            else:
                content_box.append(label)
            box.set_css_classes(["card", "user"])
        if user == "Assistant":
            label = Gtk.Label(
                label=self.controller.newelle_settings.current_profile + ": ",
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=0,
                css_classes=["warning", "heading"],
                wrap=True,
                ellipsize=Pango.EllipsizeMode.END,
            )
            if editable:
                stack.add_named(label, "label")
                stack.add_named(apply_edit_stack, "edit")
                stack.set_visible_child_name("label")
                content_box.append(stack)
            else:
                content_box.append(label)
            box.set_css_classes(["card", "assistant"])
        if user == "Done":
            content_box.append(
                Gtk.Label(
                    label="Assistant: ",
                    margin_top=10,
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=0,
                    css_classes=["success", "heading"],
                )
            )
            box.set_css_classes(["card", "done"])
        if user == "Error":
            content_box.append(
                Gtk.Label(
                    label="Error: ",
                    margin_top=10,
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=0,
                    css_classes=["error", "heading"],
                )
            )
            box.set_css_classes(["card", "failed"])
        if user == "File":
            content_box.append(
                Gtk.Label(
                    label=self.controller.newelle_settings.username + ": ",
                    margin_top=10,
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=0,
                    css_classes=["accent", "heading"],
                )
            )
            box.set_css_classes(["card", "file"])
        if user == "Folder":
            content_box.append(
                Gtk.Label(
                    label=self.controller.newelle_settings.username + ": ",
                    margin_top=10,
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=0,
                    css_classes=["accent", "heading"],
                )
            )
            box.set_css_classes(["card", "folder"])
        if user == "WarningNoVirtual":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-warning"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            icon.set_properties(
                margin_top=10, margin_start=20, margin_bottom=10, margin_end=10
            )
            box_warning = Gtk.Box(
                halign=Gtk.Align.CENTER,
                orientation=Gtk.Orientation.HORIZONTAL,
                css_classes=["warning", "heading"],
            )
            box_warning.append(icon)

            label = Gtk.Label(
                label=_(
                    "The neural network has access to your computer and any data in this chat and can run commands, be careful, we are not responsible for the neural network. Do not share any sensitive information."
                ),
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )

            box_warning.append(label)
            content_box.append(box_warning)
            box.set_halign(Gtk.Align.CENTER)
            box.set_css_classes(["card", "message-warning"])
        elif user == "Disclaimer":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-info-symbolic"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            icon.set_properties(
                margin_top=10, margin_start=20, margin_bottom=10, margin_end=10
            )
            box_warning = Gtk.Box(
                halign=Gtk.Align.CENTER,
                orientation=Gtk.Orientation.HORIZONTAL,
                css_classes=["heading"],
            )
            box_warning.append(icon)

            label = Gtk.Label(
                label=_(
                    "The neural network has access to any data in this chat, be careful, we are not responsible for the neural network. Do not share any sensitive information."
                ),
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )

            box_warning.append(label)
            content_box.append(box_warning)
            box.set_halign(Gtk.Align.CENTER)
            box.set_css_classes(["card"])
        elif message is not None:
            content_box.append(message)
        self.chat_list_block.append(box)
        return box

    def build_edit_box(self, box, id, has_prompt: bool = True):
        """Create the box and the stack with the edit buttons

        Args:
            box (): box of the message
            id (): id of the message

        Returns:
            tuple: (Gtk.Stack, Gtk.Button branch_button)
        """
        edit_box = Gtk.Box()
        buttons_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
        )
        apply_box = Gtk.Box()

        # Apply box
        apply_edit_stack = Gtk.Stack()
        apply_button = Gtk.Button(
            icon_name="check-plain-symbolic",
            css_classes=["flat", "success"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        apply_button.connect("clicked", self.apply_edit_message, box, apply_edit_stack)
        cancel_button = Gtk.Button(
            icon_name="circle-crossed-symbolic",
            css_classes=["flat", "destructive-action"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        cancel_button.connect(
            "clicked", self.cancel_edit_message, box, apply_edit_stack
        )
        apply_box.append(apply_button)
        apply_box.append(cancel_button)

        # Branch button (overlay)
        branch_button = Gtk.Button(
            icon_name="branch-symbolic",
            css_classes=["flat", "warning"],
            valign=Gtk.Align.END,
            halign=Gtk.Align.END,
            name=id,
        )
        branch_button.set_tooltip_text("Branch chat")
        branch_button.connect("clicked", lambda btn: self.emit("branch-requested", int(id)))

        # Edit box
        button = Gtk.Button(
            icon_name="document-edit-symbolic",
            css_classes=["flat", "success"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        button.connect(
            "clicked", self.edit_message, None, None, None, box, apply_edit_stack
        )
        remove_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            valign=Gtk.Align.CENTER,
            name=id,
        )
        remove_button.connect("clicked", self.delete_message, box)
        edit_box.append(button)
        edit_box.append(remove_button)
        buttons_box.append(edit_box)
        if has_prompt:
            prompt_box = Gtk.Box(halign=Gtk.Align.CENTER)
            info_button = Gtk.Button(
                icon_name="question-round-outline-symbolic",
                css_classes=["flat", "accent"],
                valign=Gtk.Align.CENTER,
                halign=Gtk.Align.CENTER,
            )
            info_button.connect("clicked", self.show_prompt, int(id))
            prompt_box.append(info_button)

            copy_button = Gtk.Button(
                icon_name="edit-copy-symbolic",
                css_classes=["flat"],
                valign=Gtk.Align.CENTER,
            )
            copy_button.connect("clicked", self.copy_message, int(id))
            prompt_box.append(copy_button)
            buttons_box.append(prompt_box)

        apply_edit_stack.add_named(apply_box, "apply")
        apply_edit_stack.add_named(buttons_box, "edit")
        apply_edit_stack.set_visible_child_name("edit")
        return apply_edit_stack, branch_button


    def apply_edit_message(self, gesture, box: Gtk.Box, apply_edit_stack: Gtk.Stack):
        """Apply edit for a message

        Args:
            gesture (): widget with the id of the message to edit as name
            box: box of the message
            apply_edit_stack: stack with the edit controls
        """
        entry = self.edit_entries[int(gesture.get_name())]
        self.focus_input()
        # Delete message
        if entry.get_text() == "":
            self.delete_message(gesture, box)
            return

        overlay = box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return

        apply_edit_stack.set_visible_child_name("edit")
        self.chat[int(gesture.get_name())]["Message"] = entry.get_text()
        self.controller.save_chats()
        content_box.remove(entry)
        content_box.append(
            self.show_message(
                entry.get_text(),
                restore=True,
                id_message=int(gesture.get_name()),
                is_user=self.chat[int(gesture.get_name())]["User"] == "User",
                return_widget=True,
            )
        )
        del self.edit_entries[int(gesture.get_name())]


    def cancel_edit_message(self, gesture, box: Gtk.Box, apply_edit_stack: Gtk.Stack):
        """Restore the old message

        Args:
            gesture (): widget with the id of the message to edit as name
            box: box of the message
            apply_edit_stack: stack with the edit controls
        """
        entry = self.edit_entries[int(gesture.get_name())]
        self.focus_input()

        overlay = box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return

        apply_edit_stack.set_visible_child_name("edit")
        content_box.remove(entry)
        content_box.append(
            self.show_message(
                self.chat[int(gesture.get_name())]["Message"],
                restore=True,
                id_message=int(gesture.get_name()),
                is_user=self.chat[int(gesture.get_name())]["User"] == "User",
                return_widget=True,
            )
        )
        del self.edit_entries[int(gesture.get_name())]

    def delete_message(self, gesture, box):
        """Delete a message from the chat

        Args:
            gesture (): widget with the id of the message to edit as name
            box (): box of the message
        """
        idx = int(gesture.get_name())
        if idx < len(self.chat):
            del self.chat[idx]
        
        # Also delete subsequent Console messages
        while idx < len(self.chat) and self.chat[idx].get("User") == "Console":
            del self.chat[idx]

        try:
            self.chat_list_block.remove(box.get_parent())
            self.messages_box.remove(box)
        except Exception:
            pass
        self.controller.save_chats()
        self.show_chat()

    def show_prompt(self, button, id):
        """Show a prompt

        Args:
            id (): id of the prompt to show
        """
        # Retrieve prompt data
        prompt_data = self.chat[id]
        prompt_text = prompt_data.get("Prompt", "")
        input_tokens = prompt_data.get("InputTokens", 0)
        output_tokens = prompt_data.get("OutputTokens", 0)
        elapsed = prompt_data.get("enlapsed", 0.0)

        speed = 0.0
        if elapsed > 0:
            speed = output_tokens / elapsed

        dialog = Adw.Dialog(can_close=True)
        dialog.set_title(_("Prompt Details"))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(
            Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=True)
        )

        scroll = Gtk.ScrolledWindow(propagate_natural_width=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        
        clamp = Adw.Clamp(maximum_size=600, margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
        scroll.set_child(clamp)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        clamp.set_child(inner_box)

        # Statistics
        stats_group = Adw.PreferencesGroup(title=_("Statistics"))
        inner_box.append(stats_group)

        row_input = Adw.ActionRow(title=_("Input Tokens"), subtitle=str(input_tokens))
        stats_group.add(row_input)

        row_output = Adw.ActionRow(title=_("Output Tokens"), subtitle=str(output_tokens))
        stats_group.add(row_output)

        row_speed = Adw.ActionRow(title=_("Generation Speed"), subtitle=f"{speed:.2f} tokens/s")
        stats_group.add(row_speed)

        # Prompt
        prompt_group = Adw.PreferencesGroup(title=_("Prompt"))
        inner_box.append(prompt_group)

        prompt_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        prompt_card.add_css_class("card")
        
        label = Gtk.Label(
            label=prompt_text,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD,
            selectable=True,
            halign=Gtk.Align.START,
            xalign=0,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12
        )
        prompt_card.append(label)
        prompt_group.add(prompt_card)

        content.append(scroll)
        dialog.set_child(content)
        dialog.set_content_width(500)
        dialog.set_content_height(600)
        dialog.present()

    def copy_message(self, button, id):
        """Copy a message

        Args:
            id (): id of the message to copy
        """
        display = Gdk.Display.get_default()
        if display is None or len(self.chat) <= id:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(
            Gdk.ContentProvider.new_for_value(self.chat[id]["Message"])
        )
        button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda: button.set_icon_name("edit-copy-symbolic"))

    def edit_message(
        self, gesture, data, x, y, box: Gtk.Box, apply_edit_stack: Gtk.Stack
    ):
        """Edit message on right click or button click

        Args:
            gesture (): widget with the id of the message to edit as name
            data (): ignored
            x (): ignored
            y (): ignored
            box: box of the message
            apply_edit_stack: stack with the edit controls

        Returns:

        """
        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(
                    title=_("You can't edit a message while the program is running."),
                    timeout=2,
                )
            )
            return False

        overlay = box.get_first_child()
        if overlay is None:
            return
        content_box = overlay.get_child()
        if content_box is None:
            return

        old_message = content_box.get_last_child()
        if old_message is None:
            return

        entry = MultilineEntry(not self.controller.newelle_settings.send_on_enter)
        self.edit_entries[int(gesture.get_name())] = entry
        # Infer size from the size of the old message
        wmax = old_message.get_size(Gtk.Orientation.HORIZONTAL)
        hmax = old_message.get_size(Gtk.Orientation.VERTICAL)
        # Create the entry to edit the message
        entry.set_text(self.chat[int(gesture.get_name())]["Message"])
        entry.set_margin_end(10)
        entry.set_margin_top(10)
        entry.set_margin_start(10)
        entry.set_margin_bottom(10)
        entry.set_size_request(wmax, hmax)
        # Change the stack to edit controls
        apply_edit_stack.set_visible_child_name("apply")
        entry.set_on_enter(
            lambda entry: self.apply_edit_message(gesture, box, apply_edit_stack)
        )
        content_box.remove(old_message)
        content_box.append(entry)

    def _wrap_message_box(self, user_type: str, content_box, id_message: int, editable: bool):
        """Wrap a content box in the message wrapper (same logic as add_message)"""
        wrapper_box = Gtk.Box(
            css_classes=["card"],
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            halign=Gtk.Align.START,
        )

        # Create overlay for branch button positioning
        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        wrapper_box.append(overlay)

        # Create content box to hold message content (horizontal layout)
        inner_content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        overlay.set_child(inner_content_box)

        # Create edit controls if editable
        stack = None
        apply_edit_stack = None
        if editable:
            apply_edit_stack, branch_button = self.build_edit_box(wrapper_box, str(id_message))
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message, wrapper_box, apply_edit_stack)
            evk.set_name(str(id_message))
            evk.set_button(3)
            wrapper_box.add_controller(evk)
            ev = Gtk.EventControllerMotion.new()
            stack = Gtk.Stack()
            ev.connect("enter", lambda x, y, data: (stack.set_visible_child_name("edit"), branch_button.set_visible(True)))
            ev.connect("leave", lambda data: (stack.set_visible_child_name("label"), branch_button.set_visible(False)))
            wrapper_box.add_controller(ev)

            # Add branch button to overlay (bottom right positioning)
            branch_button.set_visible(False)
            branch_button.set_halign(Gtk.Align.END)
            branch_button.set_valign(Gtk.Align.END)
            branch_button.set_margin_end(10)
            branch_button.set_margin_bottom(10)
            overlay.add_overlay(branch_button)

        # Add user label
        if user_type == "User":
            label = Gtk.Label(
                label=self.controller.newelle_settings.username + ": ",
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=0,
                css_classes=["accent", "heading"],
            )
            if editable and stack is not None:
                stack.add_named(label, "label")
                stack.add_named(apply_edit_stack, "edit")
                stack.set_visible_child_name("label")
                inner_content_box.append(stack)
            else:
                inner_content_box.append(label)
            wrapper_box.set_css_classes(["card", "user"])
        elif user_type == "Assistant":
            label = Gtk.Label(
                label=self.controller.newelle_settings.current_profile + ": ",
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=0,
                css_classes=["warning", "heading"],
                wrap=True,
                ellipsize=Pango.EllipsizeMode.END,
            )
            if editable and stack is not None:
                stack.add_named(label, "label")
                stack.add_named(apply_edit_stack, "edit")
                stack.set_visible_child_name("label")
                inner_content_box.append(stack)
            else:
                inner_content_box.append(label)
            wrapper_box.set_css_classes(["card", "assistant"])
        elif user_type == "File":
            inner_content_box.append(
                Gtk.Label(
                    label=self.controller.newelle_settings.username + ": ",
                    margin_top=10,
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=0,
                    css_classes=["accent", "heading"],
                )
            )
            wrapper_box.set_css_classes(["card", "file"])
        elif user_type == "Folder":
            inner_content_box.append(
                Gtk.Label(
                    label=self.controller.newelle_settings.username + ": ",
                    margin_top=10,
                    margin_start=10,
                    margin_bottom=10,
                    margin_end=0,
                    css_classes=["accent", "heading"],
                )
            )
            wrapper_box.set_css_classes(["card", "folder"])

        # Add content
        inner_content_box.append(content_box)

        return wrapper_box
    def _load_message_range(self, start_idx: int, end_idx: int):
        """Load messages in the specified range (start_idx inclusive, end_idx exclusive)"""
        for i in range(start_idx, end_idx):
            if self.chat[i]["User"] == "User":
                self.show_message(
                    self.chat[i]["Message"], True, id_message=i, is_user=True
                )
            elif self.chat[i]["User"] == "Assistant":
                self.show_message(self.chat[i]["Message"], True, id_message=i)
            elif self.chat[i]["User"] in ["File", "Folder"]:
                self.add_message(
                    self.chat[i]["User"],
                    self.get_file_button(
                        self.chat[i]["Message"][1 : len(self.chat[i]["Message"])]
                    ),
                )
    
    def _on_scroll_changed(self, adjustment):
        """Handle scroll events to trigger lazy loading of messages"""
        if not self.lazy_load_enabled or self.lazy_loading_in_progress:
            return
        
        if len(self.chat) <= self.lazy_load_batch_size:
            return  # No lazy loading needed for short chats
        
        value = adjustment.get_value()
        lower = adjustment.get_lower()
        upper = adjustment.get_upper()
        page_size = adjustment.get_page_size()
        
        # Calculate scroll position (0 = top, 1 = bottom)
        if upper - lower - page_size <= 0:
            return
        
        scroll_position = (value - lower) / (upper - lower - page_size)
        
        # Load older messages when scrolling near the top
        if scroll_position < self.lazy_load_threshold and self.lazy_loaded_start > 0:
            self._load_older_messages()
        
        # Load newer messages when scrolling near the bottom (shouldn't happen often since we start at bottom)
        if scroll_position > (1 - self.lazy_load_threshold) and self.lazy_loaded_end < len(self.chat):
            self._load_newer_messages()

    def _load_older_messages(self):
        """Load older messages (lower indices) when user scrolls up"""
        if self.lazy_loading_in_progress or self.lazy_loaded_start <= 0:
            return
        
        self.lazy_loading_in_progress = True
        
        # Calculate how many messages to load
        load_count = min(self.lazy_load_batch_size, self.lazy_loaded_start)
        new_start = max(0, self.lazy_loaded_start - load_count)
        
        # Store current scroll position to restore it after loading
        adjustment = self.chat_scroll.get_vadjustment()
        current_value = adjustment.get_value()
        current_upper = adjustment.get_upper()
        
        # Find the first actual message row (skip disclaimer/warning which are at index 0)
        # We need to insert new messages after the disclaimer/warning but before existing messages
        insert_position = 1  # After disclaimer/warning (index 0)
        
        # Load messages and create widgets
        new_messages_box_items = []
        new_rows = []
        
        for i in range(new_start, self.lazy_loaded_start):
            # Create message content box using show_message with return_widget=True
            if self.chat[i]["User"] == "User":
                content_box = self.show_message(
                    self.chat[i]["Message"], True, id_message=i, is_user=True, return_widget=True
                )
            elif self.chat[i]["User"] == "Assistant":
                content_box = self.show_message(
                    self.chat[i]["Message"], True, id_message=i, return_widget=True
                )
            elif self.chat[i]["User"] in ["File", "Folder"]:
                # For file/folder messages, create the wrapper box manually
                content_box = self._create_file_message_wrapper(i)
            else:
                continue
            
            if content_box is None:
                continue
            
            # Wrap in the message box (same as add_message does)
            wrapper_box = self._wrap_message_box(
                self.chat[i]["User"], content_box, i, editable=True
            )
            
            new_messages_box_items.append(wrapper_box)
            row = Gtk.ListBoxRow()
            row.set_child(wrapper_box)
            new_rows.append(row)
        
        # Insert rows at the correct position
        for idx, row in enumerate(new_rows):
            self.chat_list_block.insert(row, insert_position + idx)
        
        # Prepend to messages_box to maintain order
        for box in reversed(new_messages_box_items):
            self.messages_box.insert(0, box)
        
        self.lazy_loaded_start = new_start
        
        # Restore scroll position (adjust for new content height)
        GLib.idle_add(lambda: self._restore_scroll_position(current_value, current_upper))
        self.lazy_loading_in_progress = False
    
    def _load_newer_messages(self):
        """Load newer messages (higher indices) when user scrolls down"""
        if self.lazy_loading_in_progress or self.lazy_loaded_end >= len(self.chat):
            return
        
        self.lazy_loading_in_progress = True
        
        # Calculate how many messages to load
        load_count = min(self.lazy_load_batch_size, len(self.chat) - self.lazy_loaded_end)
        new_end = min(len(self.chat), self.lazy_loaded_end + load_count)
        
        # Load messages and append to list
        self._load_message_range(self.lazy_loaded_end, new_end)
        
        self.lazy_loaded_end = new_end
        self.lazy_loading_in_progress = False 
    # File button
    def get_file_button(self, path):
        """Get the button for the file

        Args:
            path (): path of the file

        Returns:
           the button for the file
        """
        if path[0:2] == "./":
            path = self.main_path + path[1 : len(path)]
        path = os.path.expanduser(os.path.normpath(path))
        button = Gtk.Button(
            css_classes=["flat"],
            margin_top=5,
            margin_start=5,
            margin_bottom=5,
            margin_end=5,
        )
        button.connect("clicked", self.run_file_on_button_click)
        button.set_name(path)
        box = Gtk.Box()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        file_name = path.split("/")[-1]
        if os.path.exists(path):
            if os.path.isdir(path):
                name = "folder"
            else:
                if file_name[len(file_name) - 4 : len(file_name)] in [".png", ".jpg"]:
                    name = "image-x-generic"
                else:
                    name = "text-x-generic"
        else:
            name = "image-missing"
        icon = Gtk.Image(icon_name=name)
        icon.set_css_classes(["large"])
        box.append(icon)
        box.append(vbox)
        vbox.set_size_request(250, -1)
        vbox.append(
            Gtk.Label(
                label=path.split("/")[-1],
                css_classes=["title-3"],
                halign=Gtk.Align.START,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
        )
        vbox.append(
            Gtk.Label(
                label="/".join(path.split("/")[0:-1]),
                halign=Gtk.Align.START,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
            )
        )
        button.set_child(box)
        return button

    def run_file_on_button_click(self, button, *a):
        """Opens the file when the file button is clicked

        Args:
            button ():
            *a:
        """
        if os.path.exists(button.get_name()):
            if os.path.isdir(
                os.path.join(os.path.expanduser(self.main_path), button.get_name())
            ):
                self.main_path = button.get_name()
                self.ui_controller.new_explorer_tab(self.main_path, False)
            else:
                subprocess.run(["xdg-open", os.path.expanduser(button.get_name())])
        else:
            self.notification_block.add_toast(
                Adw.Toast(title=_("File not found"), timeout=2)
            )

    def _restore_scroll_position(self, old_value: float, old_upper: float):
        """Restore scroll position after loading older messages"""
        adjustment = self.chat_scroll.get_vadjustment()
        new_upper = adjustment.get_upper()
        new_lower = adjustment.get_lower()
        page_size = adjustment.get_page_size()
        
        # Calculate the difference in content height
        height_diff = new_upper - old_upper
        
        # Adjust scroll position to maintain visual position
        new_value = old_value + height_diff
        new_value = max(new_lower, min(new_value, new_upper - page_size))
        
        adjustment.set_value(new_value)

    def _create_file_message_wrapper(self, message_idx: int):
        """Create a file/folder message wrapper box"""
        return self.get_file_button(
            self.chat[message_idx]["Message"][1 : len(self.chat[message_idx]["Message"])]
        )

    def show_chat(self):
        """Reload and display all messages from the chat"""
        # Clear existing messages from UI
        self.chat_list_block.remove_all()
        self.messages_box.clear()
        self.last_error_box = None
        if len(self.chat) == 0:
            self.show_placeholder()
        else:
            self.hide_placeholder()
        # Add warning or disclaimer first (matching populate_chat behavior)
        if not self.controller.newelle_settings.virtualization:
            self.add_message("WarningNoVirtual")
        else:
            self.add_message("Disclaimer")

        # Re-populate the chat with all messages
        for i in range(len(self.chat)):
            if self.chat[i]["User"] == "User":
                self.show_message(self.chat[i]["Message"], True, id_message=i, is_user=True)
            elif self.chat[i]["User"] == "Assistant":
                self.show_message(self.chat[i]["Message"], True, id_message=i)
            elif self.chat[i]["User"] in ["File", "Folder"]:
                self.add_message(self.chat[i]["User"], self.get_file_button(self.chat[i]["Message"][1 : len(self.chat[i]["Message"])]))

        # Reset lazy loading state
        total_messages = len(self.chat)
        if self.lazy_load_enabled and total_messages > self.lazy_load_batch_size:
            self.lazy_loaded_start = max(0, total_messages - self.lazy_load_batch_size)
            self.lazy_loaded_end = total_messages
        else:
            self.lazy_loaded_start = 0
            self.lazy_loaded_end = total_messages

        # Update UI state
        GLib.idle_add(self.scrolled_chat)
        GLib.idle_add(self.update_button_text)

    def update_history(self, chat):
        self.chat = chat

    def update_chat(self, chat, chat_id):
        """Update the chat history to display a different chat.
        
        Args:
            chat: The new chat data
            chat_id: The new chat ID
        """
        self.chat_id = chat_id
        # Clear existing messages
        self._clear_messages()
        # Repopulate with new chat
        self.populate_chat()
        # Update the stack to show history or placeholder
        self.history_block.set_visible_child_name("history" if len(chat) > 0 else "placeholder")
    
    def _clear_messages(self):
        """Clear all message widgets from the chat list."""
        # Remove all children except first (disclaimer/warning)
        while True:
            child = self.chat_list_block.get_last_child()
            if child is None:
                break
            self.chat_list_block.remove(child)
        self.messages_box = []
        self.edit_entries = {}
        self.lazy_loaded_start = 0
        self.lazy_loaded_end = 0

    @property
    def chat(self):
        return self.window.chat

    @chat.setter
    def chat(self, value):
        self.window.chat = value
    
    @property
    def app(self):
        """Get the application instance, works with both MainWindow and ChatTab parent"""
        # If window is ChatTab, go through window.window to get MainWindow
        if hasattr(self.window, 'window'):
            return self.window.window.app
        return self.window.app
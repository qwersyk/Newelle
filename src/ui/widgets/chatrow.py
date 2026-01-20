"""Chat row widget for Adwaita-styled chat history"""
import gettext
import unicodedata
from gi.repository import Adw, Gtk, Gio, Pango


class ChatRow(Gtk.ListBoxRow):
    """A chat row widget styled according to Adwaita HIG"""
    
    def __init__(self, chat_name: str, chat_index: int, is_selected: bool = False):
        super().__init__()
        self.chat_index = chat_index
        self.is_selected = is_selected
        
        # Process chat name: Remove new lines and limit to 8 words
        processed_name = chat_name.replace("\n", " ").strip()
        words = processed_name.split()
        if len(words) > 8:
            processed_name = " ".join(words[:8]) + "..."
        else:
            processed_name = " ".join(words)
        
        self.chat_name = processed_name
        
        # Check for emoji/symbol at the beginning using unicodedata
        first_emoji = None
        if processed_name:
            first_char = processed_name[0]
            # 'So' is Symbol, Other (includes most emojis)
            # We also check if it's high-surrogate or special char by looking at category
            if unicodedata.category(first_char) in ["So", "Sk"]:
                first_emoji = first_char
                display_name = processed_name[1:].strip()
                # Handle cases where the symbol might be multi-character/combined
                # (Simple approach: just take the first code point for now as is common)
                if not display_name and len(words) > 1:
                    display_name = processed_name
            else:
                display_name = processed_name
        else:
            display_name = processed_name

        # Create main container
        self.main_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=6,
        )
        self.set_child(self.main_box)
        
        # Chat icon/indicator
        if first_emoji:
            self.chat_icon = Gtk.Label(label=first_emoji)
            self.chat_icon.set_size_request(16, 16)
        else:
            self.chat_icon = Gtk.Image.new_from_icon_name("chat-bubbles-text-symbolic")
        
        self.chat_icon.add_css_class("dim-label")
        self.main_box.append(self.chat_icon)
        
        # Chat name label
        self.name_label = Gtk.Label(
            label=display_name,
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
            max_width_chars=30,
        )
        if chat_name != display_name:
            self.set_tooltip_text(chat_name)
        self.main_box.append(self.name_label)
        
        # Actions revealer (revealed on hover)
        self.actions_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=150,
            reveal_child=False,
        )
        self.main_box.append(self.actions_revealer)
        
        # Actions box
        self.actions_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=3,
        )
        self.actions_revealer.set_child(self.actions_box)
        
        # Stack for edit/generate buttons
        self.edit_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=100
        )
        self.actions_box.append(self.edit_stack)
        
        # Generate name button
        self.generate_button = Gtk.Button(
            icon_name="magic-wand-symbolic",
            css_classes=["flat", "circular", "success"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Generate name"),
        )
        self.generate_button.set_name(str(chat_index))
        self.edit_stack.add_named(self.generate_button, "generate")
        
        # Edit name button
        self.edit_button = Gtk.Button(
            icon_name="document-edit-symbolic",
            css_classes=["flat", "circular", "success"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Edit name"),
        )
        self.edit_button.set_name(str(chat_index))
        self.edit_stack.add_named(self.edit_button, "edit")
        self.edit_stack.set_visible_child_name("edit")
        
        # Clone button
        self.clone_button = Gtk.Button(
            icon_name="edit-copy-symbolic",
            css_classes=["flat", "circular", "accent"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Duplicate chat"),
        )
        self.clone_button.set_name(str(chat_index))
        self.actions_box.append(self.clone_button)
        
        # Delete button
        self.delete_button = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "circular", "error"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Delete chat"),
        )
        self.delete_button.set_name(str(chat_index))
        self.actions_box.append(self.delete_button)
        
        # Apply selected styling
        if is_selected:
            self.add_css_class("chat-row-selected")
            if isinstance(self.chat_icon, Gtk.Image):
                self.chat_icon.set_from_icon_name("chat-bubbles-text-symbolic")
            self.chat_icon.remove_css_class("dim-label")
            self.chat_icon.add_css_class("accent")
            self.name_label.add_css_class("heading")
            # Disable delete for selected chat
            self.delete_button.set_sensitive(False)
            self.delete_button.set_tooltip_text(_("Cannot delete current chat"))
        else:
            self.add_css_class("chat-row")
        
        # Add hover controllers
        hover_controller = Gtk.EventControllerMotion()
        hover_controller.connect("enter", self._on_hover_enter)
        hover_controller.connect("leave", self._on_hover_leave)
        self.add_controller(hover_controller)
    
    def _on_hover_enter(self, controller, x, y):
        """Show action buttons on hover"""
        self.actions_revealer.set_reveal_child(True)
    
    def _on_hover_leave(self, controller):
        """Hide action buttons when not hovering"""
        self.actions_revealer.set_reveal_child(False)
    
    def show_generate_button(self):
        """Switch to show generate button instead of edit"""
        self.edit_stack.set_visible_child_name("generate")
    
    def show_edit_button(self):
        """Switch to show edit button"""
        self.edit_stack.set_visible_child_name("edit")
    
    def get_edit_stack(self) -> Gtk.Stack:
        """Get the edit/generate stack for external control"""
        return self.edit_stack
    
    def connect_signals(self, on_generate, on_edit, on_clone, on_delete):
        """Connect all signal handlers"""
        self.generate_button.connect("clicked", on_generate)
        self.edit_button.connect("clicked", on_edit)
        self.clone_button.connect("clicked", on_clone)
        self.delete_button.connect("clicked", on_delete)

from tldextract.tldextract import update
from pylatexenc.latex2text import LatexNodes2Text
import time
import re
import sys
import os
import subprocess
import threading
import json
import base64
import copy
import uuid 
import inspect 
import gettext
from gi.repository import Gtk, Adw, Pango, Gio, Gdk, GObject, GLib, GdkPixbuf

from .ui.settings import Settings

from .utility.message_chunk import get_message_chunks

from .ui.profile import ProfileDialog
from .ui.presentation import PresentationWindow
from .ui.widgets import File, CopyBox, BarChartBox, MarkupTextView, DocumentReaderWidget, TipsCarousel, BrowserWidget, Terminal, CodeEditorWidget, ToolWidget
from .ui import apply_css_to_widget, load_image_with_callback
from .ui.explorer import ExplorerPanel
from .ui.widgets import MultilineEntry, ProfileRow, DisplayLatex, InlineLatex, ThinkingWidget
from .ui.stdout_monitor import StdoutMonitorDialog
from .utility.stdout_capture import StdoutMonitor
from .constants import AVAILABLE_LLMS, SCHEMA_ID, SETTINGS_GROUPS
from .tools import ToolResult
from .utility.system import get_spawn_command, open_website
from .utility.strings import (
    clean_bot_response,
    convert_think_codeblocks,
    get_edited_messages,
    markwon_to_pango,
    remove_markdown,
    remove_thinking_blocks,
    replace_codeblock,
    simple_markdown_to_pango,
    remove_emoji,
    count_tokens,
)
from .utility.replacehelper import PromptFormatter, replace_variables, ReplaceHelper, replace_variables_dict
from .utility.profile_settings import get_settings_dict, get_settings_dict_by_groups, restore_settings_from_dict, restore_settings_from_dict_by_groups
from .utility.audio_recorder import AudioRecorder
from .utility.media import extract_supported_files
from .ui.screenrecorder import ScreenRecorder
from .handlers import ErrorSeverity
from .controller import NewelleController, ReloadType
from .ui_controller import UIController

# Add gettext function
_ = gettext.gettext

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = self.get_application()
        
        # Main program block - On the right Canvas tabs, Chat as content
        self.main_program_block = Adw.OverlaySplitView(
            enable_hide_gesture=False,
            sidebar_position=Gtk.PackType.END,
            min_sidebar_width=420,
            max_sidebar_width=10000
        )
        # UI things
        self.model_loading_spinner_button = None
        self.model_loading_spinner_separator = None
        self.model_loading_status = False
        self.last_generation_time = None
        self.last_token_num = None
        # Breakpoint - Collapse the sidebar when the window is too narrow
        breakpoint = Adw.Breakpoint(condition=Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 1000, Adw.LengthUnit.PX))
        breakpoint.add_setter(self.main_program_block, "collapsed", True)
        self.add_breakpoint(breakpoint)
       
        # Streams
        self.check_streams = {"folder": False, "chat": False}
        # if it is recording
        self.recording = False
        # Stdout monitoring - Initialize and start from program start
        self.stdout_monitor_dialog = None
        self._init_stdout_monitoring()
        # Init controller
        self.controller = NewelleController(sys.path)
        self.controller.ui_init()
        # Init UI controller
        self.ui_controller = UIController(self)
        self.controller.set_ui_controller(self.ui_controller)
        # Replace helper - set variables in the prompt
        ReplaceHelper.set_controller(self.controller)
        # Set basic vars
        self.path = self.controller.config_dir
        self.chats = self.controller.chats
        self.chat = self.controller.chat
        # RAG Indexes to documents for each chat
        self.chat_documents_index = {}
        self.settings = self.controller.settings
        self.extensionloader = self.controller.extensionloader
        self.chat_id = self.controller.newelle_settings.chat_id
        self.main_path = self.controller.newelle_settings.main_path
        # Set window default size
        self.set_default_size(self.settings.get_int("window-width"), self.settings.get_int("window-height"))
        # Set zoom
        self.set_zoom(self.controller.newelle_settings.zoom)
        # Update the settings
        self.first_load = True
        self.update_settings()
        self.first_load = False

        # Helper vars
        self.streams = []
        self.last_error_box = None
        self.edit_entries = {}
        self.auto_run_times = 0
        # Lazy loading state
        self.lazy_load_enabled = True
        self.lazy_load_batch_size = 10  # Number of messages to load initially and per batch
        self.lazy_load_threshold = 0.1  # Load more when within 10% of top/bottom
        self.lazy_loaded_start = 0  # First loaded message index
        self.lazy_loaded_end = 0  # Last loaded message index (exclusive)
        self.lazy_loading_in_progress = False
        self.scroll_handler_id = None  # Store scroll handler ID to disconnect when needed
        # Build Window
        self.chat_panel = Gtk.Box(hexpand_set=True, hexpand=True)
        self.chat_panel.set_size_request(450, -1)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append(_("Thread editing"), "app.thread_editing")
        menu.append(_("Extensions"), "app.extension")
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Keyboard shorcuts"), "app.shortcuts")
        menu.append(_("About"), "app.about")
        menu_button.set_menu_model(menu)
        
        
        self.chat_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True, css_classes=["view"]
        )
        self.chat_header = Adw.HeaderBar(css_classes=["flat", "view"], show_start_title_buttons=False, show_end_title_buttons=True)
        self.chat_header.set_title_widget(
            Gtk.Label(label=_("Chat"), css_classes=["title"])
        )

        # Header box - Contains the buttons that must go in the left side of the header
        self.headerbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        # Mute TTS Button
        self.mute_tts_button = Gtk.Button(
            css_classes=["flat"], icon_name="audio-volume-muted-symbolic", visible=False
        )
        self.mute_tts_button.connect("clicked", self.mute_tts)
        self.headerbox.append(self.mute_tts_button)
        # Flap button
        self.flap_button_left = Gtk.ToggleButton.new()
        self.flap_button_left.set_icon_name(icon_name="sidebar-show-right-symbolic")
        self.flap_button_left.connect("clicked", self.on_flap_button_toggled)
        self.headerbox.append(child=self.flap_button_left)
        # Add headerbox to default parent
        self.chat_header.pack_end(self.headerbox)
    
        self.left_panel_toggle_button = Gtk.ToggleButton(css_classes=["flat"], active=True, icon_name="sidebar-show-left-symbolic")
        self.chat_header.pack_start(self.left_panel_toggle_button)
        self.left_panel_toggle_button.connect("clicked", self.on_chat_panel_toggled)
        self.chat_block.append(self.chat_header)
        self.chat_block.append(Gtk.Separator())
        self.chat_panel.append(self.chat_block)
        self.chat_panel.append(Gtk.Separator())

        # Setup main program block
        self.main = Adw.OverlaySplitView(
            collapsed=False,
            min_sidebar_width=300
        )
        # Connect toggle button
        self.chats_main_box = Gtk.Box(hexpand_set=True)
        self.chats_main_box.set_size_request(300, -1)
        self.chats_secondary_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True
        )
        self.chat_panel_header = Adw.HeaderBar(
            css_classes=["flat"], show_end_title_buttons=False, show_start_title_buttons=True
        )
        self.chat_panel_header.set_title_widget(
            Gtk.Label(label=_("History"), css_classes=["title"])
        )
        self.chats_secondary_box.append(self.chat_panel_header)
        self.chats_secondary_box.append(Gtk.Separator())
        self.chat_panel_header.pack_end(menu_button)
        self.chats_buttons_block = Gtk.ListBox(css_classes=["separators", "background"])
        self.chats_buttons_block.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chats_buttons_scroll_block = Gtk.ScrolledWindow(vexpand=True)
        self.chats_buttons_scroll_block.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.chats_buttons_scroll_block.set_child(self.chats_buttons_block)
        self.chats_secondary_box.append(self.chats_buttons_scroll_block)
        button = Gtk.Button(
            valign=Gtk.Align.END,
            css_classes=["suggested-action"],
            margin_start=7,
            margin_end=7,
            margin_top=7,
            margin_bottom=7,
        )
        button.set_child(Gtk.Label(label=_("Create a chat")))
        button.connect("clicked", self.new_chat)
        self.chats_secondary_box.append(button)
        self.chats_main_box.append(self.chats_secondary_box)
        self.chats_main_box.append(Gtk.Separator())
        self.main.set_sidebar(Adw.NavigationPage(child=self.chats_main_box, title=_("Chats")))
        self.main.set_content(Adw.NavigationPage(child=self.chat_panel, title=_("Chat")))
        self.main.set_show_sidebar(not self.settings.get_boolean("hide-history-on-launch"))
        self.main.connect("notify::show-sidebar", lambda x, _ : self.left_panel_toggle_button.set_active(self.main.get_show_sidebar()))
        # Canvas panel
        self.build_canvas()
        # Secondary message block
        self.secondary_message_chat_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )

        self.chat_block.append(self.secondary_message_chat_block)
        self.chat_list_block = Gtk.ListBox(
            css_classes=["separators", "background", "view"]
        )
        self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chat_scroll = Gtk.ScrolledWindow(vexpand=True)
        # Chat stack - In order to create animation on chat switch
        self.chat_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_UP, transition_duration=500)
        self.chat_scroll_window = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, css_classes=["background", "view"]
        )
        self.chat_scroll.set_child(self.chat_scroll_window)
        self.chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Scroll monitoring will be connected in show_chat after listbox is created
        self.chat_stack.add_child(self.chat_list_block)
        self.chat_stack.set_visible_child(self.chat_list_block)
        self.chat_scroll_window.append(self.chat_stack)
        self.history_block = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_DOWN, transition_duration=300)
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        drop_target.connect("drop", self.handle_file_drag)
        self.history_block.add_controller(drop_target)
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self.handle_file_drag)
        self.history_block.add_controller(drop_target)
        # Chat Offers
        self.offers_entry_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.END,
            halign=Gtk.Align.FILL,
            margin_bottom=6,
        )
        self.chat_scroll_window.append(self.offers_entry_block)
        self.chat_controls_entry_block = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            vexpand=True,
            valign=Gtk.Align.END,
            halign=Gtk.Align.CENTER,
            margin_top=6,
            margin_bottom=6,
        )
        self.chat_scroll_window.append(self.chat_controls_entry_block)
        self.message_suggestion_buttons_array = []
        self.message_suggestion_buttons_array_placeholder = []
        self.notification_block = Adw.ToastOverlay() 
        self.history_block.add_named(self.chat_scroll, "history")
        self.build_placeholder()
        self.history_block.add_named(self.empty_chat_placeholder, "placeholder")
        self.notification_block.set_child(self.history_block)
        self.history_block.set_visible_child_name("history")
        self.secondary_message_chat_block.append(self.notification_block)

        # Explorer panel 
        self.main_program_block.set_show_sidebar(False)
        # Stop chat button
        self.chat_stop_button = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-stop"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Stop"))
        box.append(label)
        self.chat_stop_button.set_child(box)
        self.chat_stop_button.connect("clicked", self.stop_chat)
        self.chat_stop_button.set_visible(False)

        self.chat_controls_entry_block.append(self.chat_stop_button)
        self.status = True
        self.build_offers()
        # Clear chat button
        self.button_clear = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-clear-all-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Clear"))
        box.append(label)
        self.button_clear.set_child(box)
        self.button_clear.connect("clicked", self.clear_chat)
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
        self.button_continue.connect("clicked", self.continue_message)
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
        self.regenerate_message_button.connect("clicked", self.regenerate_message)
        self.regenerate_message_button.set_visible(False)
        self.chat_controls_entry_block.append(self.regenerate_message_button)
        self.profiles_box = None
        self.refresh_profiles_box()

        # Input message box
        self.input_box = Gtk.Box(
            halign=Gtk.Align.FILL,
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
            spacing=6,
        )
        self.input_box.set_valign(Gtk.Align.CENTER)
        self.build_quick_toggles()
        # Attach icon
        button = Gtk.Button(
            css_classes=["flat", "circular"], icon_name="attach-symbolic"
        )
        button.connect("clicked", self.attach_file)
        # Attached image
        self.attached_image = Gtk.Image(visible=False)
        self.attached_image.set_size_request(36, 36)
        self.attached_image_data = None
        self.attach_button = button
        self.input_box.append(button)
        self.input_box.append(self.attached_image)
        if (
            not self.model.supports_vision()
            and not self.model.supports_video_vision()
            and (
                len(self.model.get_supported_files())
                + (
                    len(self.rag_handler.get_supported_files())
                    if self.rag_handler is not None
                    else 0
                )
                == 0
            )
        ):
            self.attach_button.set_visible(False)
        else:
            self.attach_button.set_visible(True)

        # Add screen recording button
        self.screen_record_button = Gtk.Button(
            icon_name="media-record-symbolic",
            css_classes=["flat"],
            halign=Gtk.Align.CENTER,
        )
        self.screen_record_button.connect("clicked", self.start_screen_recording)
        self.input_box.append(self.screen_record_button)

        if not self.model.supports_video_vision():
            self.screen_record_button.set_visible(False)
        self.video_recorder = None

        # Text Entry
        self.input_panel = MultilineEntry(not self.controller.newelle_settings.send_on_enter)
        self.input_panel.set_on_image_pasted(self.image_pasted)
        self.input_box.append(self.input_panel)
        self.input_panel.set_placeholder(_("Send a message..."))

        # Buttons on the right
        self.secondary_message_chat_block.append(Gtk.Separator())
        self.secondary_message_chat_block.append(self.input_box)

        # Mic button
        self.mic_button = Gtk.Button(
            css_classes=["suggested-action"],
            icon_name="audio-input-microphone-symbolic",
            width_request=36,
            height_request=36,
        )
        self.mic_button.set_vexpand(False)
        self.mic_button.set_valign(Gtk.Align.CENTER)
        self.mic_button.connect("clicked", self.start_recording)
        self.recording_button = self.mic_button
        self.input_box.append(self.mic_button)

        # Send button
        box = Gtk.Box()
        box.set_vexpand(False)
        self.send_button = Gtk.Button(
            css_classes=["suggested-action"],
            icon_name="go-next-symbolic",
            width_request=36,
            height_request=36,
        )
        self.send_button.set_vexpand(False)
        self.send_button.set_valign(Gtk.Align.CENTER)
        box.append(self.send_button)
        self.input_box.append(box)
        self.input_panel.set_on_enter(self.on_entry_activate)
        self.send_button.connect("clicked", self.on_entry_button_clicked)
        self.main.connect("notify::show-sidebar", self.handle_main_block_change)
        self.main_program_block.connect(
            "notify::show-sidebar", self.handle_second_block_change
        )

        def build_model_popup():
            self.chat_header.set_title_widget(self.build_model_popup())

        self.stream_number_variable = 0
        GLib.idle_add(self.update_history)
        GLib.idle_add(self.show_chat)
        if not self.settings.get_boolean("welcome-screen-shown"):
            threading.Thread(target=self.show_presentation_window).start()
        GLib.timeout_add(10, build_model_popup)
        self.controller.handlers.set_error_func(self.handle_error)
        
        # Connect cleanup on window destroy
        self.connect("destroy", self._cleanup_on_destroy)

    def _cleanup_on_destroy(self, window):
        """Clean up resources when window is destroyed"""
        # Stop stdout monitoring
        if self.stdout_monitor_dialog:
            self.stdout_monitor_dialog.stop_monitoring_external()

    def build_canvas(self):

        self.canvas_header = Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=False)
        self.canvas_header.set_title_widget(Gtk.Label())
        self.canvas_headerbox = Gtk.Box(halign=Gtk.Align.CENTER)
        self.canvas_header.pack_start(self.canvas_headerbox)
        
        self.canvas_tabs = Adw.TabView()
        self.canvas_tabs.connect("notify::selected-page", self.on_tab_switched)
        self.canvas_button = Adw.TabButton(view=self.canvas_tabs)
        self.canvas_tab_bar = Adw.TabBar(autohide=True, view=self.canvas_tabs, css_classes=["inline"])
        self.canvas_overview = Adw.TabOverview(view=self.canvas_tabs, child=self.canvas_tabs, show_end_title_buttons=False, show_start_title_buttons=False, enable_new_tab=True)
        self.canvas_button.connect("clicked", lambda x:  self.canvas_overview.set_open(not self.canvas_overview.get_open()))
        self.canvas_overview.connect("create-tab", self.add_explorer_tab)
        
        # Add new tab menu button
        self.new_tab_button = Gtk.MenuButton(css_classes=["flat"])
        box = Gtk.Box(spacing=6)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="list-add-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box.append(icon)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="pan-down-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box.append(icon)
        self.new_tab_button.set_child(box)
       
        # Detach tab button 
        self.detach_tab_button = Gtk.Button(css_classes=["flat"], icon_name="detach-symbolic")
        self.detach_tab_button.connect("clicked", self.detach_tab) 
        
        # Create custom menu entries: Title, Icon, Callable
        menu_entries = [
            (_("Explorer Tab"), "folder-symbolic", self.add_explorer_tab),
            (_("Terminal Tab"), "gnome-terminal-symbolic", self.add_terminal_tab),
            (_("Browser Tab"), "internet-symbolic", self.add_browser_tab)
        ]
        menu_entries += self.extensionloader.get_add_tab_buttons()
        
        # Create custom popover with ListBox
        popover = Gtk.Popover()
        listbox = Gtk.ListBox(css_classes=["menu"])
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        
        for title, icon_name, callback in menu_entries:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.set_margin_start(12)
            row_box.set_margin_end(12)
            row_box.set_margin_top(6)
            row_box.set_margin_bottom(6)
            
            # Add icon
            if type(icon_name) is str:
                icon = Gtk.Image.new_from_icon_name(icon_name)
            elif type(icon_name) is GdkPixbuf.Pixbuf:
                icon = Gtk.Image.new_from_pixbuf(icon_name)
            elif type(icon_name) is Gtk.IconPaintable:
                icon = Gtk.Image.new_from_paintable(icon_name)
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            row_box.append(icon)
            
            # Add label
            label = Gtk.Label(label=title, xalign=0)
            row_box.append(label)
            
            row.set_child(row_box)
            row.callback = callback
            listbox.append(row)
        
        def on_row_activated(listbox, row):
            row.callback(None, None)
            popover.popdown()
        
        listbox.connect("row-activated", on_row_activated)
        popover.set_child(listbox)
        self.new_tab_button.set_popover(popover)
        self.canvas_header.pack_end(self.canvas_button)
        self.canvas_header.pack_end(self.new_tab_button)
        self.canvas_header.pack_end(self.detach_tab_button)

        self.canvas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas_box.append(self.canvas_header)
        self.canvas_box.append(self.canvas_tab_bar)
        self.canvas_box.append(self.canvas_overview)
        self.add_explorer_tab(None, self.main_path)
        self.set_content(self.main_program_block)
        bin = Adw.BreakpointBin(child=self.main, width_request=300, height_request=300)
        breakpoint = Adw.Breakpoint(condition=Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 900, Adw.LengthUnit.PX))
        breakpoint.add_setter(self.main, "collapsed", True)
        bin.add_breakpoint(breakpoint)

        self.main_program_block.set_content(bin)
        self.main_program_block.set_sidebar(self.canvas_box)
        self.main_program_block.set_name("hide")
   
    def detach_tab(self, button):
        """Method to move a tab to another window

        Args:
            button (): button - unused (given in callbacks) 
        """
        tab = self.canvas_tabs.get_selected_page()
        if tab is not None:
            widget = tab.get_child()
            if widget is not None:
                # Create another view to transfer the page
                otherview = Adw.TabView()
                self.canvas_tabs.transfer_page(tab, otherview, 0)
                # Set tab title as window title
                tab_title = tab.get_title()
                title_label = Gtk.Label(label=tab_title)
                # Create window
                headerbar = Adw.HeaderBar(css_classes=["flat"], title_widget=title_label)
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                content.append(headerbar)
                content.append(otherview)
                window = Gtk.Window(child=content, decorated=False)
                tab.connect("notify::title", lambda x, title: title_label.set_label(x.get_title()))
                window.show()
                window.connect("close-request", self.reattach_tab, tab, otherview)

    def reattach_tab(self, window, tab: Adw.TabPage, otherview: Adw.TabView):
        """Reattach tab after window closing

        Args:
            window (): 
            tab: tab to reattach 
            otherview: other view from which to transfer the page 

        Returns:
           False in order for the window to close 
        """
        otherview.transfer_page(tab, self.canvas_tabs, self.canvas_tabs.get_n_pages())
        return False 

    def on_tab_switched(self, tab_view, tab):
        current_tab = self.canvas_tabs.get_selected_page()
        if current_tab is None:
            return
        child = current_tab.get_child() 
        if child is not None:
            if hasattr(child, "main_path"):
                self.main_path = child.main_path 
                os.chdir(os.path.expanduser(child.main_path))

    def show_placeholder(self):
        self.history_block.set_visible_child_name("placeholder")
        self.tips_section.shuffle_tips()

    def hide_placeholder(self):
        self.history_block.set_visible_child_name("history")
    
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
        self.offers_entry_block_placeholder.set_size_request(-1, 36*self.offers)
        self.empty_chat_placeholder.append(self.offers_entry_block_placeholder)

    def handle_error(self, message: str, error: ErrorSeverity):
        if error == ErrorSeverity.ERROR:
            dialog = Adw.AlertDialog(title=_("Provider Errror"), body=message)
            dialog.add_response("close", "Close")
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda d, r: d.close())
            dialog.present()
        elif error == ErrorSeverity.WARNING:
            self.notification_block.add_toast(Adw.Toast.new(message))

    def set_zoom(self, zoom):
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.reset_property("gtk-xft-dpi")
            settings.set_property(
                "gtk-xft-dpi", settings.get_property("gtk-xft-dpi") + (zoom - 100) * 400
            )
            self.controller.newelle_settings.zoom = zoom

    def build_quick_toggles(self):
        self.quick_toggles = Gtk.MenuButton(
            css_classes=["flat"], icon_name="controls-big"
        )
        self.quick_toggles_popover = Gtk.Popover()
        entries = [  
            {"setting_name": "rag-on", "title": _("Local Documents")},
            {"setting_name": "memory-on", "title": _("Long Term Memory")},
            {"setting_name": "tts-on", "title": _("TTS")},
            {"setting_name": "virtualization", "title": _("Command virtualization")},
            {"setting_name": "websearch-on", "title": _("Web search")},
        ]

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        container.set_margin_start(12)
        container.set_margin_end(12)
        container.set_margin_top(6)
        container.set_margin_bottom(6)

        for entry in entries:
            title = entry["title"]
            setting_key = entry["setting_name"]
            # Create row container
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            # Label with title
            label = Gtk.Label(label=title, xalign=0)
            label.set_hexpand(True)  # Expand horizontally to push switch right
            # Create the Switch
            switch = Gtk.Switch()
            # Bind to settings
            self.settings.bind(
                setting_key, switch, "active", Gio.SettingsBindFlags.DEFAULT
            )
            # Pack row items
            row.append(label)
            row.append(switch)
            # Add row to vertical container
            container.append(row)

        # Apply to UI
        self.quick_toggles_popover.set_child(container)
        self.quick_toggles.set_popover(self.quick_toggles_popover)
        self.input_box.append(self.quick_toggles)
        self.quick_toggles_popover.connect("closed", self.update_toggles)

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

    def update_toggles(self, *_):
        """Update the quick toggles"""
        reloads = self.controller.update_settings()
        self.tts_enabled = self.controller.newelle_settings.tts_enabled
        self.rag_on = self.controller.newelle_settings.rag_on
        self.memory_on = self.controller.newelle_settings.memory_on
        self.virtualization = self.controller.newelle_settings.virtualization
        if ReloadType.WEBSEARCH in reloads:
            self.model_popup_settings.build_prompts_settings()

    def quick_settings_update(self):
        """Update settings from the quick settings"""
        reloads = self.controller.update_settings()
        self.model = self.controller.handlers.llm
        self.tts_enabled = self.controller.newelle_settings.tts_enabled
        self.rag_on = self.controller.newelle_settings.rag_on
        self.rag_on_documents = self.controller.newelle_settings.rag_on_documents
        self.memory_on = self.controller.newelle_settings.memory_on
        self.update_model_popup()
        if ReloadType.LLM in reloads:
            self.reload_buttons()

    def update_settings(self):
        """Update settings, run every time the program is started or settings dialog closed"""
        reloads = self.controller.update_settings()
        if self.first_load:
            # Load handlers with a timeout in order to not freeze the program
            def load_handlers_async():
                threading.Thread(target=self.controller.handlers.load_handlers).start()
            GLib.timeout_add(1000, load_handlers_async)
        else:
            # Update the send on enter setting
            self.input_panel.set_enter_on_ctrl(not self.controller.newelle_settings.send_on_enter)
        # Basic settings
        self.offers = self.controller.newelle_settings.offers
        self.current_profile = self.controller.newelle_settings.current_profile
        self.profile_settings = self.controller.newelle_settings.profile_settings
        self.memory_on = self.controller.newelle_settings.memory_on
        self.rag_on = self.controller.newelle_settings.rag_on
        self.tts_enabled = self.controller.newelle_settings.tts_enabled
        self.virtualization = self.controller.newelle_settings.virtualization
        self.prompts = self.controller.newelle_settings.prompts
        # Handlers
        self.tts = self.controller.handlers.tts
        self.stt = self.controller.handlers.stt
        self.model = self.controller.handlers.llm
        self.secondary_model = self.controller.handlers.secondary_llm
        self.embeddings = self.controller.handlers.embedding
        self.memory_handler = self.controller.handlers.memory
        self.rag_handler = self.controller.handlers.rag
        self.extensionloader = self.controller.extensionloader
        if ReloadType.RELOAD_CHAT in reloads:
            self.show_chat()
        if ReloadType.RELOAD_CHAT_LIST in reloads:
            self.update_history()
        if ReloadType.OFFERS in reloads:
            self.build_offers()
        # Setup TTS
        self.tts.connect(
            "start", lambda: GLib.idle_add(self.mute_tts_button.set_visible, True)
        )
        self.tts.connect(
            "stop", lambda: GLib.idle_add(self.mute_tts_button.set_visible, False)
        )
        if ReloadType.LLM in reloads:
            self.reload_buttons() 
            self.update_model_popup()
        if ReloadType.TOOLS in reloads:
            self.model_popup_settings.refresh_tools_list()

    def reload_buttons(self):
        """Reload offers and buttons on LLM change"""
        if not self.first_load:
            if (
                not self.model.supports_vision()
                and not self.model.supports_video_vision()
                and len(self.model.get_supported_files())
                + (
                    len(self.rag_handler.get_supported_files())
                    if self.rag_handler is not None
                    else 0
                )
                == 0
            ):
                if self.attached_image_data is not None:
                    self.delete_attachment(self.attach_button)
                self.attach_button.set_visible(False)
            else:
                self.attach_button.set_visible(True)
            if not self.model.supports_video_vision():
                if self.video_recorder is not None:
                    self.video_recorder.stop()
                    self.video_recorder = None
            self.screen_record_button.set_visible(
                self.model.supports_video_vision() and not self.attached_image_data
            )
            self.chat_header.set_title_widget(self.build_model_popup())

    # Model popup
    def update_model_popup(self):
        """Update the label in the popup"""
        if not hasattr(self, "model_menu_button"):
            return
        model_name = AVAILABLE_LLMS[self.model.key]["title"]
        if self.model.get_setting("model") is not None:
            model_name = model_name + " - " + self.model.get_setting("model")
        
        self.model_menu_button.set_child(
            Gtk.Label(
                label=model_name,
                ellipsize=Pango.EllipsizeMode.MIDDLE,
            )
        )

    def set_model_loading_spinner(self, status):
        if status == self.model_loading_status:
            return
        self.model_loading_status = status
        if self.model_loading_spinner_separator is None:
            return
        if status:
            self.title_box.prepend(self.model_loading_spinner_separator)
            self.title_box.prepend(self.model_loading_spinner_button)
        else:
            self.title_box.remove(self.model_loading_spinner_separator)
            self.title_box.remove(self.model_loading_spinner_button)
        


    def build_model_popup(self):
        self.model_menu_button = Gtk.MenuButton()
        self.update_model_popup()
        self.model_popup = Gtk.Popover()
        self.model_popup.set_size_request(500, 500)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        settings = Settings(self.app, self.controller, headless=True, popup=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stack = Adw.ViewStack()
        self.model_popup_settings = settings
        # Add the model selection page
        model_page = self.scrollable(self.build_model_selection())
        self.model_page = model_page
        stack.add_titled_with_icon(
            self.model_page,
            title="Models",
            name="Models",
            icon_name="view-list-symbolic",
        )

        # Add the existing pages
        llm_page = self.steal_from_settings(settings.LLM)
        stack.add_titled_with_icon(
            self.scrollable(llm_page),
            title="LLM",
            name="LLM",
            icon_name="brain-augemnted-symbolic",
        )
        stack.add_titled_with_icon(
            self.scrollable(self.steal_from_settings(settings.prompt)),
            title="Prompts",
            name="Prompts",
            icon_name="question-round-outline-symbolic",
        )
        stack.add_titled_with_icon(
            self.scrollable(self.steal_from_settings(settings.tools_group)),
            title="Tools",
            name="Tools",
            icon_name="tools-symbolic",
        )
        if len(self.model.get_models_list()) == 0:
            stack.set_visible_child(llm_page)
        switcher = Adw.ViewSwitcher()
        switcher.set_stack(stack)
        box.append(switcher)
        box.append(stack)
        self.model_menu_button.set_popover(self.model_popup)
        self.model_popup.connect(
            "closed", lambda x: GLib.idle_add(self.quick_settings_update)
        )
        self.model_popup.set_child(box)
        
        # Create a horizontal box to contain both the model button and settings button
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title_box.set_css_classes(["linked"])
        # Spinner 
        self.model_loading_spinner_button = Gtk.Button() 
        model_loading_spinner = Gtk.Spinner(spinning=True)
        self.model_loading_spinner_separator = separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.model_loading_spinner_separator.set_margin_top(6)
        self.model_loading_spinner_separator.set_margin_bottom(6)
        self.model_loading_spinner_button.set_child(model_loading_spinner)
        if self.model_loading_status:
            title_box.append(self.model_loading_spinner_separator)
            title_box.append(self.model_loading_spinner_button)
        title_box.append(self.model_menu_button)
        self.title_box = title_box 
        # Add a subtle separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.set_margin_top(6)
        separator.set_margin_bottom(6)
        title_box.append(separator)
        
        # Add settings button
        settings_button = Gtk.Button(
            css_classes=["flat"],
            icon_name="settings-symbolic"
        )
        settings_button.connect("clicked", lambda btn: self.get_application().lookup_action("settings").activate(None))
        title_box.append(settings_button)
        
        return title_box

    def update_available_models(self):
        self.controller.update_settings()
        self.model = self.controller.handlers.llm
        if self.model_page is not None:
            self.model_page.set_child(self.build_model_selection())

    def build_model_selection(self):
        # Create a vertical box with some spacing & margins
        provider_title = AVAILABLE_LLMS[self.model.key]["title"]
        if len(self.model.get_models_list()) == 0:
            return Gtk.Label(
                label=_("This provider does not have a model list"), wrap=True
            )
        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12
        )  # Changed to Gtk.Box for more flexibility
        group = Adw.PreferencesGroup(title=provider_title + _(" Models"))

        # Add Search Bar
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search Models..."))
        self.search_entry.connect("search-changed", self._filter_models)
        vbox.append(self.search_entry)  # Add search entry to the main vbox

        # Create a ListBox in SINGLE selection mode with activate-on-single-click
        self.models_list = Gtk.ListBox()
        self.models_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.models_list.set_activate_on_single_click(True)
        self.models_list.connect("row-activated", self.on_model_row_activated)

        # Populate the list with downloaded models.
        provider_title = AVAILABLE_LLMS[self.model.key]["title"]

        favorites = self.model.get_setting("favorites", search_default=False, return_value=[])
        if favorites is None:
            favorites = []

        models = self.model.get_models_list()
        # Sort by favorite
        models = sorted(models, key=lambda x: (x[1] not in favorites, x[0].lower()))

        for name, model in models:
            # Create a ListBoxRow to hold our action row.
            listbox_row = Gtk.ListBoxRow()
            listbox_row.get_style_context().add_class("card")
            listbox_row.set_margin_bottom(5)

            # Create an ActionRow with a title and subtitle.
            # The title shows provider and display; the subtitle shows a per-model description.
            # Retrieve a model-specific subtitle (for example, using a model library if available).
            model_subtitle = "Model: " + model
            if hasattr(self.model, "model_library"):
                for info_dict in self.model.model_library:
                    if info_dict["key"] == model:
                        model_subtitle = info_dict["description"]
                        break
            action_row = Adw.ActionRow(
                title=f"{provider_title} - {name}", subtitle=model_subtitle
            )
            
            # Star button
            is_fav = model in favorites
            btn = Gtk.Button(
                icon_name="star-filled-rounded-symbolic",
                css_classes=["flat"] + [] if not is_fav else ["warning"],
                valign=Gtk.Align.CENTER
            )
            btn.connect("clicked", self.on_star_clicked, model)
            action_row.add_suffix(btn)

            listbox_row.set_child(action_row)

            # Save attributes for selection handling and searching.
            listbox_row.model = model
            listbox_row.search_terms = (
                f"{provider_title} {name} {model_subtitle}".lower()
            )  # Store searchable text

            # Select the correct row on init
            if self.model.get_selected_model() == model:
                self.models_list.select_row(listbox_row)

            self.models_list.get_style_context().add_class("transparent")
            self.models_list.append(listbox_row)

        # Add the listbox to a scrolled window for better handling if list is long
        list_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        list_scroll.set_child(self.models_list)
        group.add(list_scroll)  # Add scrolled list to the group
        vbox.append(group)  # Add the group to the main vbox

        # "+" button to open the full settings
        plus_button = Gtk.Button(label="+")
        plus_button.get_style_context().add_class("suggested-action")
        plus_button.connect(
            "clicked",
            lambda btn: self.get_application().lookup_action("settings").activate(None),
        )
        group.add(plus_button)  # Add plus button inside the group

        # Initial filter call
        self._filter_models(self.search_entry)

        return vbox

    def on_star_clicked(self, button, model):
        favorites = self.model.get_setting("favorites", search_default=False, return_value=[])
        if favorites is None:
            favorites = []
        
        if model in favorites:
            favorites.remove(model)
        else:
            favorites.append(model)
        
        self.model.set_setting("favorites", favorites)
        self.update_available_models()


    def _filter_models(self, search_entry):
        """Filters the models list based on the search entry text."""
        search_text = search_entry.get_text().lower()
        current_row = self.models_list.get_row_at_index(0)
        while current_row is not None:
            if hasattr(current_row, "search_terms"):
                is_visible = search_text in current_row.search_terms
                current_row.set_visible(is_visible)
            current_row = current_row.get_next_sibling()

    def on_model_row_activated(self, listbox, row):
        # Retrieve the stored provider key and internal model.
        internal_model = row.model

        # Set the active LLM
        # self.settings.set_string("language-model", provider_key)
        self.model.set_setting("model", internal_model)
        # Dismiss the popover.
        self.model_popup.popdown()

        # Update the header label to reflect the new choice.
        self.update_model_popup()

    def scrollable(self, widget) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(widget)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        return scroll

    def steal_from_settings(self, widget):
        widget.unparent()
        widget.set_margin_bottom(3)
        widget.set_margin_end(3)
        widget.set_margin_start(3)
        widget.set_margin_top(3)
        return widget

    # UI Functions
    def show_presentation_window(self):
        """Show the window for the initial program presentation on first start"""
        def show_presentation():
            self.presentation_dialog = PresentationWindow(
                "presentation", self.settings, self
            )
            self.presentation_dialog.show()
        self.controller.handlers.handlers_cached.acquire()
        self.controller.handlers.handlers_cached.release()
        GLib.idle_add(show_presentation)

    def mute_tts(self, button: Gtk.Button):
        """Mute the TTS"""
        self.focus_input()
        if self.tts_enabled:
            self.tts.stop()
        return False

    def focus_input(self):
        """Focus the input box. Often used to avoid removing focues objects"""
        self.input_panel.input_panel.grab_focus()
    
    def add_text_to_input(self, text, focus_input=False):
        txt = self.input_panel.get_text()
        txt += "\n" + text 
        self.input_panel.set_text(txt)
        if focus_input:
            self.focus_input()
    # Profiles
    def refresh_profiles_box(self):
        """Changes the profile switch button on the header"""
        if self.profiles_box is not None:
            self.chat_header.remove(self.profiles_box)
        self.profiles_box = self.get_profiles_box()
        self.chat_header.pack_start(self.profiles_box)

    def create_profile(self, profile_name, picture=None, settings={}, settings_groups=[]):
        """Create a profile

        Args:
            profile_name (): name of the profile
            picture (): path to the profile picture
            settings (): settings to override for that profile
        """
        self.controller.create_profile(profile_name, picture, settings, settings_groups)

    def delete_profile(self, profile_name):
        """Delete a profile

        Args:
            profile_name (): name of the profile to delete
        """
        self.controller.delete_profile(profile_name)
        self.refresh_profiles_box()
        self.update_settings()

    def edit_profile(self, profile_name):
        """Edit a profile

        Args:
            profile_name (): name of the profile to edit
        """
        dialog = ProfileDialog(self, self.profile_settings, profile_name=profile_name)
        dialog.present()
        self.refresh_profiles_box()
        self.update_settings()

    def get_profiles_box(self):
        """Create and build the profile selection dialog"""
        box = Gtk.Box()
        scroll = Gtk.ScrolledWindow(
            propagate_natural_width=True,
            propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        profile_button = Gtk.MenuButton()
        if self.profile_settings[self.current_profile]["picture"] is not None:
            avatar = Adw.Avatar(
                custom_image=Gdk.Texture.new_from_filename(
                    self.profile_settings[self.current_profile]["picture"]
                ),
                text=self.current_profile,
                show_initials=True,
                size=20,
            )
            # Set avata image at the correct size
            ch = avatar.get_last_child()
            if ch is not None:
                ch = ch.get_last_child()
                if ch is not None and type(ch) is Gtk.Image:
                    ch.set_icon_size(Gtk.IconSize.NORMAL)
        else:
            avatar = Adw.Avatar(text=self.current_profile, show_initials=True, size=20)

        profile_button.set_child(avatar)
        box.append(profile_button)

        profiles = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["boxed-list"]
        )
        for profile in self.profile_settings.keys():
            account_row = ProfileRow(
                profile,
                self.profile_settings[profile]["picture"],
                self.current_profile == profile,
                allow_delete=profile != "Assistant" and profile != self.current_profile,
                allow_edit=profile != "Assistant"
            )
            profiles.append(account_row)
            account_row.set_on_forget(self.delete_profile)
            account_row.set_on_edit(self.edit_profile)
        # Separator
        separator = Gtk.Separator(
            sensitive=False, can_focus=False, can_target=False, focus_on_click=False
        )
        profiles.append(separator)
        parent = separator.get_parent()
        if parent is not None:
            parent.set_sensitive(False)
        # Add profile row
        profiles.append(
            ProfileRow(
                _("Create new profile"), None, False, add=True, allow_delete=False
            )
        )

        # Assign widgets
        popover = Gtk.Popover(css_classes=["menu"])
        profiles.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroll.set_child(profiles)
        popover.set_child(scroll)
        profile_button.set_popover(popover)
        profiles.select_row(
            profiles.get_row_at_index(
                list(self.profile_settings.keys()).index(self.current_profile)
            )
        )
        profiles.connect(
            "row-selected",
            lambda listbox, action, popover=popover: self.select_profile(
                listbox, action, popover
            ),
        )
        return box

    def select_profile(
        self, listbox: Gtk.ListBox, action: ProfileRow, popover: Gtk.Popover
    ):
        """Handle profile selection in the listbox"""
        if action is None:
            return
        if action.add:
            dialog = ProfileDialog(self, self.profile_settings)
            listbox.select_row(
                listbox.get_row_at_index(
                    list(self.profile_settings.keys()).index(self.current_profile)
                )
            )
            popover.hide()
            dialog.present()
            return
        if self.current_profile != action.profile:
            popover.hide()
        self.switch_profile(action.profile)

    def switch_profile(self, profile: str):
        """Handle profile switching"""
        if self.current_profile == profile:
            return
        print(f"Switching profile to {profile}")
        groups = self.profile_settings[self.current_profile].get("settings_groups", [])
        old_settings = get_settings_dict_by_groups(self.settings, groups, SETTINGS_GROUPS, ["current-profile", "profiles"] )
        self.profile_settings = json.loads(self.settings.get_string("profiles"))
        self.profile_settings[self.current_profile]["settings"] = old_settings

        new_settings = self.profile_settings[profile]["settings"]
        groups = self.profile_settings[profile].get("settings_groups", [])
        restore_settings_from_dict_by_groups(self.settings, new_settings, groups, SETTINGS_GROUPS)
        self.settings.set_string("profiles", json.dumps(self.profile_settings))
        self.settings.set_string("current-profile", profile)
        self.focus_input()
        self.update_settings()

        self.refresh_profiles_box()

    def reload_profiles(self):
        """Reload the profiles"""
        self.focus_input()
        self.refresh_profiles_box()

    # Voice Recording
    def start_recording(self, button):
        """Start recording voice for Speech to Text"""
        path = os.path.join(self.controller.cache_dir, "recording.wav")
        if os.path.exists(path):
            os.remove(path)
        self.recording = True
        if self.controller.newelle_settings.automatic_stt:
            self.automatic_stt_status = True
        # button.set_child(Gtk.Spinner(spinning=True))
        button.set_icon_name("media-playback-stop-symbolic")
        button.disconnect_by_func(self.start_recording)
        button.remove_css_class("suggested-action")
        button.add_css_class("error")
        button.connect("clicked", self.stop_recording)
        self.recorder = AudioRecorder(
            auto_stop=True,
            stop_function=self.auto_stop_recording,
            silence_duration=self.controller.newelle_settings.stt_silence_detection_duration,
            silence_threshold_percent=self.controller.newelle_settings.stt_silence_detection_threshold,
        )
        t = threading.Thread(target=self.recorder.start_recording, args=(path,))
        t.start()

    def auto_stop_recording(self, button=False):
        """Stop recording after an auto stop signal"""
        GLib.idle_add(self.stop_recording_ui, self.recording_button)
        threading.Thread(
            target=self.stop_recording_async, args=(self.recording_button,)
        ).start()

    def stop_recording(self, button=False):
        """Stop a recording manually"""
        self.recording = False
        self.automatic_stt_status = False
        self.recorder.stop_recording(
            os.path.join(self.controller.cache_dir, "recording.wav")
        )
        # self.auto_stop_recording()

    def stop_recording_ui(self, button):
        """Update the UI to show that the recording has been stopped"""
        button.set_child(None)
        button.set_icon_name("audio-input-microphone-symbolic")
        button.add_css_class("suggested-action")
        button.remove_css_class("error")
        button.disconnect_by_func(self.stop_recording)
        button.connect("clicked", self.start_recording)

    def stop_recording_async(self, button=False):
        """Stop recording and save the file"""
        recognizer = self.stt
        result = recognizer.recognize_file(
            os.path.join(self.controller.cache_dir, "recording.wav")
        )

        def idle_record():
            if (
                result is not None
                and "stop" not in result.lower()
                and len(result.replace(" ", "")) > 2
            ):
                self.input_panel.set_text(result)
                self.on_entry_activate(self.input_panel)
            else:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("Could not recognize your voice"), timeout=2)
                )

        GLib.idle_add(idle_record)

    # Screen recording
    def start_screen_recording(self, button):
        """Start screen recording"""
        if self.video_recorder is None:
            self.video_recorder = ScreenRecorder(self)
            self.video_recorder.start()
            if self.video_recorder.recording:
                self.screen_record_button.set_icon_name("media-playback-stop-symbolic")
                self.screen_record_button.set_css_classes(
                    ["destructive-action", "circular"]
                )
            else:
                self.video_recorder = None
        else:
            self.screen_record_button.set_visible(False)
            self.video_recorder.stop()
            self.screen_record_button.set_icon_name("media-record-symbolic")
            self.screen_record_button.set_css_classes(["flat"])
            self.add_file(file_path=self.video_recorder.output_path + ".mp4")
            self.video_recorder = None

    # File attachment
    def attach_file(self, button):
        """Show attach file dialog to add a file"""
        filters = Gio.ListStore.new(Gtk.FileFilter)
        image_patterns = ["*.png", "*.jpg", "*.jpeg", "*.webp"]
        video_patterns = ["*.mp4"]
        file_patterns = self.model.get_supported_files()
        rag_patterns = self.rag_handler.get_supported_files() if self.rag_handler is not None else []
        supported_patterns = []
            
        image_filter = Gtk.FileFilter(
            name=_("Images"), patterns=image_patterns
        )
        video_filter = Gtk.FileFilter(name="Video", patterns=video_patterns)
        file_filter = Gtk.FileFilter(
            name=_("LLM Supported Files"), patterns=file_patterns
        )
        second_file_filter = None
        if (
            self.rag_handler is not None
            and self.controller.newelle_settings.rag_on_documents
        ):
            second_file_filter = Gtk.FileFilter(
                name=_("RAG Supported files"),
                patterns=self.rag_handler.get_supported_files(),
            )
            supported_patterns += rag_patterns
        default_filter = None
        
        if second_file_filter is not None:
            filters.append(second_file_filter)
        if self.model.supports_video_vision():
            filters.append(video_filter)
            supported_patterns += video_patterns
        if len(self.model.get_supported_files()) > 0:
            filters.append(file_filter)
            supported_patterns += file_patterns
        if self.model.supports_vision():
            supported_patterns += image_patterns
            filters.append(image_filter)
        default_filter = Gtk.FileFilter(
            name=_("Supported Files"),
            patterns=supported_patterns
        )
        all_files_filter = Gtk.FileFilter(
            name=_("All Files"),
            patterns=["*"],
        )
        filters.append(default_filter)
        filters.append(all_files_filter)
        dialog = Gtk.FileDialog(
            title=_("Attach file"),
            modal=True,
            default_filter=default_filter,
            filters=filters,
        )
        dialog.open(self, None, self.process_file)

    def process_file(self, dialog, result):
        """Get the attached file by the dialog

        Args:
            dialog ():
            result ():
        """
        try:
            file = dialog.open_finish(result)
        except Exception as _:
            return
        if file is None:
            return
        file_path = file.get_path()
        self.add_file(file_path=file_path)

    def image_pasted(self, image):
        """Handle image pasting

        Args:
            image (): image data
        """
        self.add_file(file_data=image)

    def delete_attachment(self, button):
        """Delete file attachment"""
        self.attached_image_data = None
        self.attach_button.set_icon_name("attach-symbolic")
        self.attach_button.set_css_classes(["circular", "flat"])
        self.attach_button.disconnect_by_func(self.delete_attachment)
        self.attach_button.connect("clicked", self.attach_file)
        self.attached_image.set_visible(False)
        self.screen_record_button.set_visible(self.model.supports_video_vision())
        # self.screen_record_button.set_visible("mp4" in self.model.get_supported_files())

    def add_file(self, file_path=None, file_data=None):
        """Add a file and update the UI, also generates thumbnail for videos

        Args:
            file_path (): file path for the file
            file_data (): file data for the file
        """
        if file_path is not None:
            if file_path.lower().endswith((".mp4", ".avi", ".mov")):
                cmd = [
                    "ffmpeg",
                    "-i",
                    file_path,
                    "-vframes",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ]
                frame_data = subprocess.run(cmd, capture_output=True).stdout

                if frame_data:
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(frame_data)
                    loader.close()
                    self.attached_image.set_from_pixbuf(loader.get_pixbuf())
                else:
                    self.attached_image.set_from_icon_name("video-x-generic")
            elif file_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                self.attached_image.set_from_file(file_path)
            else:
                self.attached_image.set_from_icon_name("text-x-generic")

            self.attached_image_data = file_path
            self.attached_image.set_visible(True)
        elif file_data is not None:
            base64_image = base64.b64encode(file_data).decode("utf-8")
            self.attached_image_data = f"data:image/jpeg;base64,{base64_image}"
            loader = GdkPixbuf.PixbufLoader()
            loader.write(file_data)
            loader.close()
            self.attached_image.set_from_pixbuf(loader.get_pixbuf())
            self.attached_image.set_visible(True)

        self.attach_button.set_icon_name("user-trash-symbolic")
        self.attach_button.set_css_classes(["destructive-action", "circular"])
        self.attach_button.connect("clicked", self.delete_attachment)
        self.attach_button.disconnect_by_func(self.attach_file)
        self.screen_record_button.set_visible(False)

    # Flap management
    def on_chat_panel_toggled(self, button: Gtk.ToggleButton):
        if button.get_active():
            self.main.set_show_sidebar(True)
        else:
            self.main.set_show_sidebar(False)
   
    def return_to_chat_panel(self, button):
        if self.main.get_collapsed():
            self.main.set_show_sidebar(False)

    def handle_second_block_change(self, *a):
        """Handle flaps reveal/hide"""
        status = self.main_program_block.get_show_sidebar()
        if self.main_program_block.get_name() == "hide" and status:
            self.main_program_block.set_show_sidebar(False)
            return True
        elif (self.main_program_block.get_name() == "visible") and (not status):
            self.main_program_block.set_show_sidebar(True)
            return True
        status = self.main_program_block.get_show_sidebar()
        if status:
            self.chat_panel_header.set_show_end_title_buttons(False)
            self.chat_header.set_show_end_title_buttons(False)
            header_widget = self.canvas_headerbox
        else:
            self.chat_panel_header.set_show_end_title_buttons(not self.main.get_show_sidebar())
            self.chat_header.set_show_end_title_buttons(True)
            header_widget = self.chat_header
        # Unparent the headerbox
        self.headerbox.unparent()
        # Move the headerbox to the right widget
        if type(header_widget) is Adw.HeaderBar or type(header_widget) is Gtk.HeaderBar:
            header_widget.pack_end(self.headerbox)
        elif type(header_widget) is Gtk.Box:
            self.canvas_headerbox.append(self.headerbox)

    def on_flap_button_toggled(self, toggle_button: Gtk.ToggleButton):
        """Handle flap button toggle"""
        self.focus_input()
        self.flap_button_left.set_active(True)
        if self.main_program_block.get_name() == "visible":
            self.main_program_block.set_name("hide")
            self.main_program_block.set_show_sidebar(False)
            toggle_button.set_active(False)
        else:
            self.main_program_block.set_name("visible")
            self.main_program_block.set_show_sidebar(True)
            toggle_button.set_active(True)

    # UI Functions for chat management
    def send_button_start_spinner(self):
        """Show a spinner when you click on send button"""
        spinner = Gtk.Spinner(spinning=True)
        self.send_button.set_child(spinner)

    def remove_send_button_spinner(self):
        """Remove the spinner in the send button when the message is received"""
        self.send_button.set_child(None)
        self.send_button.set_icon_name("go-next-symbolic")

    def on_entry_button_clicked(self, *a):
        """When the send message button is clicked activate the input panel"""
        self.on_entry_activate(self.input_panel)

    # Explorer code
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

    def handle_file_drag(self, DropTarget, data, x, y):
        """Handle file drag and drop

        Args:
            DropTarget ():
            data ():
            x ():
            y ():

        Returns:

        """
        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(
                    title=_("The file cannot be sent until the program is finished"),
                    timeout=2,
                )
            )
            return False
        if type(data) is Gdk.FileList:
            paths = []
            for file in data.get_files():
                paths += [file.get_path()]
            data = "\n".join(paths)
        for path in data.split("\n"):
            if os.path.exists(path):
                message_label = self.get_file_button(path)
                if os.path.isdir(path):
                    self.chat.append({"User": "Folder", "Message": " " + path})
                    self.add_message("Folder", message_label)
                else:
                    self.chat.append({"User": "File", "Message": " " + path})
                    self.add_message("File", message_label)
                self.chats[self.chat_id]["chat"] = self.chat
            else:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("The file is not recognized"), timeout=2)
                )
        self.hide_placeholder()

    def handle_main_block_change(self, *data):
        if self.main.get_show_sidebar():
            self.chat_panel_header.set_show_end_title_buttons(
                not self.main_program_block.get_show_sidebar()
            )
            self.chat_header.set_show_start_title_buttons(True)
        else:
            self.chat_panel_header.set_show_end_title_buttons(False)
            self.chat_header.set_show_start_title_buttons(False)

    # Chat management
    def continue_message(self, button):
        """Continue last message"""
        if self.chat[-1]["User"] not in ["Assistant", "Console", "User"]:
            self.notification_block.add_toast(
                Adw.Toast(title=_("You can no longer continue the message."), timeout=2)
            )
        else:
            threading.Thread(target=self.send_message).start()
            self.send_button_start_spinner()

    def regenerate_message(self, *a):
        """Regenerate last message"""
        if self.chat[-1]["User"] in ["Assistant", "Console"]:
            for i in range(len(self.chat) - 1, -1, -1):
                if self.chat[i]["User"] in ["Assistant", "Console"]:
                    self.chat.pop(i)
                else:
                    break
            self.show_chat()
            threading.Thread(target=self.send_message).start()
            self.send_button_start_spinner()
        elif self.last_error_box is not None:
            #self.remove_error(True)
            self.show_chat()
            threading.Thread(target=self.send_message).start()
            self.send_button_start_spinner()
        else:
            self.notification_block.add_toast(
                Adw.Toast(
                    title=_("You can no longer regenerate the message."), timeout=2
                )
            )

    def remove_error(self, idle=False):
        """Remove the last error shown in chat

        Args:
            idle (): if the function is being executed in idle
        """
        if not idle:
            GLib.idle_add(self.remove_error, True)
        if self.last_error_box is not None:
            error_row = self.chat_list_block.get_last_child()
            if error_row is None:
                return
            self.chat_list_block.remove(error_row)
            self.last_error_box = None

    def update_history(self):
        """Reload chats panel"""
        # Focus input to avoid removing a focused child
        # This avoids scroll up
        self.focus_input()

        # Update UI
        list_box = Gtk.ListBox(css_classes=["separators", "background"])
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chats_list_box = list_box
        self.chats_buttons_scroll_block.set_child(list_box)
        chat_range = (
            range(len(self.chats)).__reversed__()
            if self.controller.newelle_settings.reverse_order
            else range(len(self.chats))
        )
        for i in chat_range:
            box = Gtk.Box(
                spacing=6, margin_top=3, margin_bottom=3, margin_start=3, margin_end=3
            )
            stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_UP, transition_duration=100)
            generate_chat_name_button = Gtk.Button(
                css_classes=["flat", "accent"],
                valign=Gtk.Align.CENTER,
                icon_name="magic-wand-symbolic",
                width_request=36,
            )
            generate_chat_name_button.set_name(str(i))
            generate_chat_name_button.connect("clicked", self.generate_chat_name)
            stack.add_named(generate_chat_name_button, "generate")

            edit_chat_name_button = Gtk.Button(
                css_classes=["flat", "accent"],
                valign=Gtk.Align.CENTER,
                icon_name="document-edit-symbolic",
                width_request=36,
            )
            edit_chat_name_button.connect("clicked", self.edit_chat_name, stack)
            edit_chat_name_button.set_name(str(i))
            stack.add_named(edit_chat_name_button, "edit")
            stack.set_visible_child_name("edit")


            create_chat_clone_button = Gtk.Button(
                css_classes=["flat", "success"], valign=Gtk.Align.CENTER
            )
            create_chat_clone_button.connect("clicked", self.copy_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-copy-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            create_chat_clone_button.set_child(icon)
            create_chat_clone_button.set_name(str(i))

            delete_chat_button = Gtk.Button(
                css_classes=["error", "flat"], valign=Gtk.Align.CENTER
            )
            delete_chat_button.connect("clicked", self.remove_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            delete_chat_button.set_child(icon)
            delete_chat_button.set_name(str(i))
            button = Gtk.Button(css_classes=["flat"], hexpand=True)
            name = self.chats[i]["name"]
            if len(name) > 30:
                # name = name[0:27] + "…"
                button.set_tooltip_text(name)
            button.set_child(
                Gtk.Label(
                    label=name,
                    wrap=False,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    xalign=0,
                    ellipsize=Pango.EllipsizeMode.END,
                    width_chars=22,
                    single_line_mode=True,
                )
            )
            button.set_name(str(i))

            if i == self.chat_id:
                button.connect("clicked", self.return_to_chat_panel)
                delete_chat_button.set_css_classes([""])
                delete_chat_button.set_sensitive(False)
                delete_chat_button.set_can_target(False)
                delete_chat_button.set_has_frame(False)
                button.set_has_frame(True)
            else:
                button.connect("clicked", self.chose_chat)
            box.append(button)
            box.append(create_chat_clone_button)
            box.append(stack)
            box.append(delete_chat_button)
            list_box.append(box)

    def remove_chat(self, button):
        """Remove a chat"""
        if int(button.get_name()) < self.chat_id:
            self.chat_id -= 1
        elif int(button.get_name()) == self.chat_id:
            return False
        self.chats.pop(int(button.get_name()))
        self.update_history()

    def edit_chat_name(self, button, stack, multithreading=False):
        """Allow manual editing of chat name by replacing the title with an entry"""
        chat_index = int(button.get_name())
        # Show the generate chat name button
        stack.set_visible_child_name("generate")
        # Find the chat name button in the current list box
        list_box = self.chats_list_box
        if list_box is None:
            return
            
        # Find the correct row
        row_index = 0
        chat_range = (
            range(len(self.chats)).__reversed__()
            if self.controller.newelle_settings.reverse_order
            else range(len(self.chats))
        )
        
        for i in chat_range:
            if i == chat_index:
                break
            row_index += 1
            
        row = list_box.get_row_at_index(row_index)
        if row is None:
            return
            
        # Get the box containing the buttons
        box = row.get_child()
        if box is None:
            return
            
        # Get the chat name button (first child)
        name_button = box.get_first_child()
        if name_button is None:
            return
            
        # Create an entry to replace the label
        entry = Gtk.Entry()
        entry.set_text(self.chats[chat_index]["name"])
        entry.set_hexpand(True)
        entry.set_margin_top(3)
        entry.set_margin_bottom(3)
        
        # Store original button for restoration
        original_button = name_button
        
        # Replace the button with the entry
        box.remove(name_button)
        box.prepend(entry)
        
        # Focus the entry
        entry.grab_focus()
        entry.select_region(0, -1)  # Select all text
        
        # Handle entry activation (Enter key)
        def on_entry_activate(entry):
            new_name = entry.get_text().strip()
            if new_name:
                self.chats[chat_index]["name"] = new_name
                self.save_chat()
            self.update_history()
             
        entry.connect("activate", on_entry_activate)
        
    def new_chat(self, button, *a):
        """Create a new chat and switch to it"""
        self.chats.append({"name": _("Chat ") + str(len(self.chats) + 1), "chat": []})
        if not self.status:
            self.stop_chat()
        self.stream_number_variable += 1
        self.chat_id = len(self.chats) - 1
        self.chat = self.chats[self.chat_id]["chat"]
        self.update_history()
        self.show_chat()
        GLib.idle_add(self.update_button_text)

    def copy_chat(self, button, *a):
        """Copy a chat into a new chat"""
        self.chats.append(
            {
                "name": self.chats[int(button.get_name())]["name"],
                "chat": self.chats[int(button.get_name())]["chat"][:],
            }
        )
        self.update_history()

    def chose_chat(self, button, *a):
        """Switch to another chat"""
        self.return_to_chat_panel(None)
        if not self.status:
            self.stop_chat()
        self.stream_number_variable += 1
        old_chat_id = self.chat_id
        self.chat_id = int(button.get_name())
        self.chat = self.chats[self.chat_id]["chat"]
        # Change profile 
        if self.controller.newelle_settings.remember_profile and "profile" in self.chats[self.chat_id]:
            self.switch_profile(self.chats[self.chat_id]["profile"])
        self.update_history()
        if old_chat_id > self.chat_id:
            self.chat_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP)
        self.show_chat(animate=True)
        self.chat_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_DOWN)
        GLib.idle_add(self.update_button_text)

    def scrolled_chat(self):
        """Scroll at the bottom of the chat"""
        adjustment = self.chat_scroll.get_vadjustment()
        adjustment.set_value(100000)

    def clear_chat(self, button):
        """Delete current chat history"""
        self.notification_block.add_toast(
            Adw.Toast(title=_("Chat is cleared"), timeout=2)
        )
        self.chat = []
        self.chats[self.chat_id]["chat"] = self.chat
        self.show_chat()
        self.stream_number_variable += 1
        threading.Thread(target=self.update_button_text).start()

    def stop_chat(self, button=None):
        """Stop generating the message"""
        self.model.stop()
        self.status = True
        self.stream_number_variable += 1
        self.chat_stop_button.set_visible(False)
        GLib.idle_add(self.update_button_text)
        self.notification_block.add_toast(
            Adw.Toast(
                title=_("The message generation was stopped"), timeout=2
            )
        )
        self.show_chat()
        self.remove_send_button_spinner()

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
            # Generate suggestions in another thread and then add them to the UI
            threading.Thread(target=self.generate_suggestions).start()
        else:
            for btn in self.message_suggestion_buttons_array + self.message_suggestion_buttons_array_placeholder:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.regenerate_message_button.set_visible(False)
            self.chat_stop_button.set_visible(True)
        GLib.idle_add(self.scrolled_chat)

    def on_entry_activate(self, entry):
        """Send a message when input is pressed

        Args:
            entry (): Message input entry
        """
        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(
                    title=_("The message cannot be sent until the program is finished"),
                    timeout=2,
                )
            )
            return False
        text = entry.get_text()
        entry.set_text("")
        if not text == " " * len(text):
            if self.attached_image_data is not None:
                if self.attached_image_data.endswith(
                    (".png", ".jpg", ".jpeg", ".webp")
                ) or self.attached_image_data.startswith("data:image/jpeg;base64,"):
                    text = "```image\n" + self.attached_image_data + "\n```\n" + text
                elif self.attached_image_data.endswith(
                    (".mp4", ".mkv", ".webm", ".avi")
                ):
                    text = "```video\n" + self.attached_image_data + "\n```\n" + text
                else:
                    text = "```file\n" + self.attached_image_data + "\n```\n" + text
                self.delete_attachment(self.attach_button)
            self.chat.append({"User": "User", "Message": text})
            self.show_message(text, True, id_message=len(self.chat) - 1, is_user=True)
        self.scrolled_chat()
        threading.Thread(target=self.send_message).start()
        self.send_button_start_spinner()

    # LLM functions
    def send_message_to_bot(self, message):
        """Send a message to the bot

        Args:
            message (): text of the message

        Returns:
           the message
        """
        return self.model.send_message(self, message)

    def send_bot_response(self, button):
        """Add message to the chat, display the user message and the spiner and launch a thread to get response

        Args:
            button (): send message button
        """
        self.send_button_start_spinner()
        text = button.get_child().get_label()
        self.chat.append({"User": "User", "Message": text})
        self.show_message(text, id_message=len(self.chat) - 1, is_user=True)
        
        threading.Thread(target=self.send_message).start()

    def generate_suggestions(self):
        """Create the suggestions and update the UI when it's finished"""
        self.model.set_history([], self.get_history())
        suggestions = self.secondary_model.get_suggestions(
            self.controller.newelle_settings.prompts["get_suggestions_prompt"],
            self.offers,
        )
        GLib.idle_add(self.populate_suggestions, suggestions)

    def populate_suggestions(self, suggestions):
        """Update the UI with the generated suggestions"""
        i = 0
        # Convert to tuple to remove duplicates
        for suggestion in tuple(suggestions):
            if i + 1 > self.offers:
                break
            else:
                message = suggestion.replace("\n", "")
                btn = self.message_suggestion_buttons_array[i]
                btn.get_child().set_label(message)
                btn.set_visible(True)
                # Placeholder buttons 
                btn_placeholder = self.message_suggestion_buttons_array_placeholder[i]
                btn_placeholder.get_child().set_label(message)
                btn_placeholder.set_visible(True)

                GLib.idle_add(self.scrolled_chat)
            i += 1
        self.chat_stop_button.set_visible(False)
        GLib.idle_add(self.scrolled_chat)

    def get_history(
        self, chat=None, include_last_message=False, copy_chat=True
    ) -> list[dict[str, str]]:
        """Format the history excluding none messages and picking the right context size

        Args:
            chat (): chat history, if None current is taken

        Returns:
           chat history
        """
        if chat is None:
            chat = self.chat
        if copy_chat:
            chat = copy.deepcopy(chat)
        history = []
        count = self.controller.newelle_settings.memory
        msgs = chat[:-1] if not include_last_message else chat
        msgs.reverse()
        for msg in msgs:
            if count == 0:
                break
            if msg["User"] == "Console" and msg["Message"] == "None":
                continue
            if self.controller.newelle_settings.remove_thinking:
                msg["Message"] = remove_thinking_blocks(msg["Message"])
            if msg["User"] == "File" or msg["User"] == "Folder":
                msg["Message"] = f"```{msg['User'].lower()}\n{msg['Message'].strip()}\n```"
                msg["User"] = "User"
            history.insert(0,msg)
            count -= 1
        return history

    def get_memory_prompt(self):
        r = []
        if self.memory_on:
            r += self.memory_handler.get_context(
                self.chat[-1]["Message"], self.get_history()
            )
        if self.rag_on:
            r += self.rag_handler.get_context(
                self.chat[-1]["Message"], self.get_history()
            )
        if (
            self.controller.newelle_settings.rag_on_documents
            and self.rag_handler is not None
        ):
            documents = extract_supported_files(
                self.get_history(include_last_message=True),
                self.rag_handler.get_supported_files_reading(),
                self.model.get_supported_files()
            )
            if len(documents) > 0:
                existing_index = self.chat_documents_index.get(self.chat_id, None)
                if existing_index is None:
                    GLib.idle_add(self.add_reading_widget, documents)
                    existing_index = self.rag_handler.build_index(documents)
                    self.chat_documents_index[self.chat_id] = existing_index
                else:
                    GLib.idle_add(self.add_reading_widget,documents)
                    existing_index.update_index(documents)
                if existing_index.get_index_size() > self.controller.newelle_settings.rag_limit: 
                    r += existing_index.query(
                        self.chat[-1]["Message"]
                    )
                else:
                    r += existing_index.get_all_contexts()
                GLib.idle_add(self.remove_reading_widget)
        return r

    def update_memory(self, bot_response):
        if self.memory_on:
            threading.Thread(
                target=self.memory_handler.register_response,
                args=(bot_response, self.chat),
            ).start()

    def get_variable(self, name:str):
        tools = self.controller.tools.get_all_tools()
        for tool in tools:
            if tool.name == name:
                if tool in self.controller.get_enabled_tools():
                    return True
                else:
                    return False
        if name == "tts_on":
            return self.tts_enabled
        elif name == "virtualization_on":
            return self.virtualization
        elif name == "auto_run":
            return self.controller.newelle_settings.auto_run
        elif name == "websearch_on":
            return self.controller.newelle_settings.websearch_on
        elif name == "rag_on":
            return self.rag_on_documents
        elif name == "local_folder":
            return self.rag_on
        elif name == "automatic_stt":
            return self.controller.newelle_settings.automatic_stt
        elif name == "profile_name":
            return self.controller.newelle_settings.current_profile
        elif name == "external_browser":
            return self.controller.newelle_settings.external_browser
        elif name == "history":
            return "\n".join([f"{msg['User']}: {msg['Message']}" for msg in self.get_history()])
        elif name == "message":
            return self.chat[-1]["Message"]
        else:
            rep = replace_variables_dict()
            var = "{" + name.upper() + "}"
            if var in rep:
                return rep[var]
            else:
                return None

    def send_message(self, manual=True):
        """Send a message in the chat and get bot answer, handle TTS etc"""
        # Save profile for generation 
        self.chats[self.chat_id]["profile"] = self.current_profile

        GLib.idle_add(self.hide_placeholder)
        if manual:
            self.auto_run_times = 0
        self.stream_number_variable += 1
        stream_number_variable = self.stream_number_variable
        self.status = False
        GLib.idle_add(self.update_button_text)

        # Append extensions prompts
        prompts = []
        formatter = PromptFormatter(replace_variables_dict(), self.get_variable)
        for prompt in self.controller.newelle_settings.bot_prompts:
            prompts.append(formatter.format(prompt))

        # Start creating the message
        if self.model.stream_enabled():
            self.streamed_message = ""
            self.curr_label = ""
            self.streaming_label = None
            self.last_update = time.time()
            self.stream_thinking = False
            GLib.idle_add(self.create_streaming_message_label)
        # Append memory
        if (
            self.memory_on
            or self.rag_on
            or self.controller.newelle_settings.rag_on_documents
        ):
            prompts += self.get_memory_prompt()

        # Set the history for the model
        history = self.get_history()
        # Let extensions preprocess the history
        old_history = copy.deepcopy(history)
        old_user_prompt = self.chat[-1]["Message"]
        self.chat, prompts = self.controller.integrationsloader.preprocess_history(self.chat, prompts)
        self.chat, prompts = self.extensionloader.preprocess_history(self.chat, prompts)
        # Edit messages that require to be updated
        history = self.get_history()
        edited_messages = get_edited_messages(history, old_history)
        if edited_messages is None:
            GLib.idle_add(self.show_chat)
        else:
            for message in edited_messages:
                GLib.idle_add(self.reload_message, message)
        if len(self.chat) == 0:
            GLib.idle_add(self.remove_send_button_spinner)
            GLib.idle_add(self.show_chat)
            return
        if self.chat[-1]["Message"] != old_user_prompt:
            self.reload_message(len(self.chat) - 1)

        self.model.set_history(prompts, history)
        try:
            t1 = time.time()
            if self.model.stream_enabled():
                message_label = self.model.send_message_stream(
                    self,
                    self.chat[-1]["Message"],
                    self.update_message,
                    [stream_number_variable],
                )
                try:
                    parent = self.streaming_box.get_parent()
                    if parent is not None:
                        parent.set_visible(False)
                except Exception as _:
                    pass
            else:
                message_label = self.send_message_to_bot(self.chat[-1]["Message"])
            self.last_generation_time = time.time() - t1
            
            input_tokens = 0
            for prompt in prompts:
                input_tokens += count_tokens(prompt)
            for message in history:
                input_tokens += count_tokens(message.get("User", "")) + count_tokens(message.get("Message", ""))
            input_tokens += count_tokens(self.chat[-1]["Message"])
            
            output_tokens = count_tokens(message_label)
            self.last_token_num = (input_tokens, output_tokens)
            
            message_label = clean_bot_response(message_label) 
        except Exception as e:
            # Show error messsage
            GLib.idle_add(self.show_message, str(e), False, -1, False, False, True)
            GLib.idle_add(self.remove_send_button_spinner)

            def remove_streaming_box():
                try:
                    if self.model.stream_enabled() and hasattr(self, "streaming_box"):
                        if self.streaming_box is not None:
                            parent = self.streaming_box.get_parent()
                            if parent is not None:
                                self.streaming_box.unparent()
                except (AttributeError, RuntimeError):
                    # Widget may have been destroyed or unparented already
                    pass

            GLib.timeout_add(250, remove_streaming_box)
            return
        if self.stream_number_variable == stream_number_variable:
            old_history = copy.deepcopy(self.chat)
            history, message_label = self.controller.integrationsloader.postprocess_history(self.chat, message_label)
            history, message_label = self.extensionloader.postprocess_history(
                self.chat, message_label
            )
            # Edit messages that require to be updated
            edited_messages = get_edited_messages(history, old_history)
            if edited_messages is None:
                GLib.idle_add(self.show_chat)
            else:
                for message in edited_messages:
                    GLib.idle_add(self.reload_message, message)
        GLib.idle_add(
            self.show_message,
            message_label,
            False,
            -1,
            False,
            False,
            False,
            "\n".join(prompts),
        )

        # Clean up streaming_box after message is displayed
        def cleanup_streaming_box():
            try:
                if self.model.stream_enabled() and hasattr(self, "streaming_box"):
                    if self.streaming_box is not None:
                        parent = self.streaming_box.get_parent()
                        if parent is not None:
                            self.streaming_box.unparent()
            except (AttributeError, RuntimeError):
                pass

        GLib.idle_add(cleanup_streaming_box)
        GLib.idle_add(self.remove_send_button_spinner)
        # Generate chat name
        self.update_memory(message_label)
        if self.controller.newelle_settings.auto_generate_name and len(self.chat) == 1:
            GLib.idle_add(self.generate_chat_name, Gtk.Button(name=str(self.chat_id)))
        # TTS
        tts_thread = None
        if self.tts_enabled:
            message_label = convert_think_codeblocks(message_label)
            message = re.sub(r"```.*?```", "", message_label, flags=re.DOTALL)
            message = remove_markdown(message)
            message = remove_emoji(message)
            if not (
                not message.strip()
                or message.isspace()
                or all(char == "\n" for char in message)
            ):
                tts_thread = threading.Thread(
                    target=self.tts.play_audio, args=(message,)
                )
                tts_thread.start()

        # Wait for tts to finish to restart recording
        def restart_recording():
            if not self.automatic_stt_status:
                return
            if tts_thread is not None:
                tts_thread.join()
            GLib.idle_add(self.start_recording, self.recording_button)

        if self.controller.newelle_settings.automatic_stt:
            threading.Thread(target=restart_recording).start()

    def add_reading_widget(self, documents):
        d = [document.replace("file:", "") for document in documents if document.startswith("file:")]
        documents = d
        if self.model.stream_enabled():
            self.reading = DocumentReaderWidget()
            for document in documents:
                self.reading.add_document(document)
            self.streaming_box.append(self.reading)

    def remove_reading_widget(self):
        try:
            if hasattr(self, "reading") and hasattr(self, "streaming_box"):
                # Check if streaming_box still exists and reading is a child of it
                if self.streaming_box is not None and self.reading is not None:
                    # Check if reading is still attached to streaming_box
                    parent = self.reading.get_parent()
                    if parent == self.streaming_box:
                        self.streaming_box.remove(self.reading)
        except (AttributeError, TypeError, RuntimeError):
            # Widget may have been destroyed or unparented already
            pass

    def create_streaming_message_label(self):
        """Create a label for message streaming"""
        # Create a scrolledwindow for the text view
        self.streaming_message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrolled_window = Gtk.ScrolledWindow(
            margin_top=10, margin_start=10, margin_bottom=10, margin_end=10
        )
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scrolled_window.set_overflow(Gtk.Overflow.HIDDEN)
        scrolled_window.set_max_content_width(200)
        # Create a label for the message that will be streamed
        self.streaming_label = Gtk.Label(
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            hexpand=True,
            xalign=0,
            selectable=True,
            valign=Gtk.Align.START,
        )
        # Remove background color from window and label
        scrolled_window.add_css_class("scroll")
        self.streaming_label.add_css_class("scroll")
        apply_css_to_widget(
            scrolled_window, ".scroll { background-color: rgba(0,0,0,0);}"
        )
        apply_css_to_widget(
            self.streaming_label, ".scroll { background-color: rgba(0,0,0,0);}"
        )
        # Add the label to the scrolledwindow
        scrolled_window.set_child(self.streaming_label)
        # Create the message label
        self.streaming_message_box.append(scrolled_window)
        self.streaming_box = self.add_message("Assistant", self.streaming_message_box)
        # Safely remove the last element from messages_box
        try:
            if hasattr(self, "messages_box") and len(self.messages_box) > 0:
                self.messages_box.pop()
        except (AttributeError, IndexError):
            pass
        self.streaming_box.set_overflow(Gtk.Overflow.VISIBLE)

    def update_message(self, message, stream_number_variable, *args):
        """Update message label when streaming

        Args:
            message (): new message text
            stream_number_variable (): stream number, avoid conflicting streams
        """
        if self.stream_number_variable != stream_number_variable:
            return
        # Safety check: ensure streaming_box and streaming_label still exist and are valid
        if not hasattr(self, "streaming_box") or self.streaming_box is None:
            return
        if not hasattr(self, "streaming_label") or self.streaming_label is None:
            return
        self.streamed_message = message
        last_update_checked = False
        if self.streamed_message.startswith("<think>") and not self.stream_thinking:
            self.stream_thinking = True
            text = self.streamed_message.split("</think>")
            thinking = text[0].replace("<think>", "")
            message = text[1] if len(text) > 1 else ""
            self.streaming_thought = thinking
            def idle():
                try:
                    if hasattr(self, "streaming_message_box") and self.streaming_message_box is not None:
                        self.thinking_box = ThinkingWidget()
                        self.streaming_message_box.prepend(self.thinking_box)
                        self.thinking_box.start_thinking(thinking)
                except (AttributeError, RuntimeError):
                    pass
            GLib.idle_add(idle)
        elif self.stream_thinking:

            t = time.time()
            if t - self.last_update < 0.05:
                return
            last_update_checked = True
            self.last_update = t
            text = self.streamed_message.split("</think>")
            thinking = text[0].replace("<think>", "")
            message = text[1] if len(text) > 1 else ""
            added_thinking = thinking[len(self.streaming_thought) :]
            self.streaming_thought += added_thinking
            try:
                self.thinking_box.append_thinking(added_thinking)
            except (AttributeError, RuntimeError):
                pass
        if self.streaming_label is not None:
            # Find the differences between the messages
            t = time.time()
            if t - self.last_update < 0.05 and not last_update_checked:
                return
            self.last_update = t
            self.curr_label = message

            # Edit the label on the main thread
            def idle_edit():
                try:
                    if self.streaming_label is not None:
                        self.streaming_label.set_markup(
                            simple_markdown_to_pango(self.curr_label)
                        )
                except (AttributeError, RuntimeError):
                    pass

            GLib.idle_add(idle_edit)

    # Show messages in chat
    def show_chat(self, animate=False):
        """Show a chat"""
        self.last_error_box = None
        self.messages_box = [] 
        if not self.check_streams["chat"]:
            self.check_streams["chat"] = True
            try:
                if not animate:
                    self.chat_stack.set_transition_duration(0)
                old_chat_list_block = self.chat_list_block
                self.chat_list_block = Gtk.ListBox(
                    css_classes=["separators", "background", "view"]
                )
                self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)

                self.chat_stack.add_child(self.chat_list_block)
                self.chat_stack.set_visible_child(self.chat_list_block)
                GLib.idle_add(self.chat_stack.remove,old_chat_list_block)
                GLib.idle_add(self.chat_stack.set_transition_duration, 300)
                # Connect scroll monitoring for lazy loading
                # Disconnect old handler if it exists
                if self.scroll_handler_id is not None:
                    adjustment = self.chat_scroll.get_vadjustment()
                    adjustment.disconnect(self.scroll_handler_id)
                adjustment = self.chat_scroll.get_vadjustment()
                self.scroll_handler_id = adjustment.connect("value-changed", self._on_scroll_changed)
            except Exception as e:
                self.notification_block.add_toast(Adw.Toast(title=str(e)))

            self.chat_scroll_window.remove(self.chat_controls_entry_block)
            self.chat_scroll_window.remove(self.offers_entry_block)
            self.chat_scroll_window.append(self.chat_controls_entry_block)
            self.chat_scroll_window.append(self.offers_entry_block)
            if not self.controller.newelle_settings.virtualization:
                self.add_message("WarningNoVirtual")
            else:
                self.add_message("Disclaimer")
            
            # Use lazy loading for long chats
            total_messages = len(self.chat)
            if self.lazy_load_enabled and total_messages > self.lazy_load_batch_size:
                # Load only the last batch_size messages initially
                # Messages are indexed from 0 (oldest) to len-1 (newest)
                start_idx = max(0, total_messages - self.lazy_load_batch_size)
                self.lazy_loaded_start = start_idx
                self.lazy_loaded_end = total_messages
                self._load_message_range(start_idx, total_messages)
            else:
                # Load all messages for short chats
                self.lazy_loaded_start = 0
                self.lazy_loaded_end = total_messages
                for i in range(len(self.chat)):
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
            self.check_streams["chat"] = False
        if len(self.chat) == 0:
            self.show_placeholder()
        else:
            self.hide_placeholder()
        GLib.idle_add(self.scrolled_chat)
        GLib.idle_add(self.update_button_text)

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
    
    def _create_file_message_wrapper(self, message_idx: int):
        """Create a file/folder message wrapper box"""
        return self.get_file_button(
            self.chat[message_idx]["Message"][1 : len(self.chat[message_idx]["Message"])]
        )
    
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
        
        # Create edit controls if editable
        stack = None
        apply_edit_stack = None
        if editable:
            apply_edit_stack = self.build_edit_box(wrapper_box, str(id_message))
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message, wrapper_box, apply_edit_stack)
            evk.set_name(str(id_message))
            evk.set_button(3)
            wrapper_box.add_controller(evk)
            ev = Gtk.EventControllerMotion.new()
            stack = Gtk.Stack()
            ev.connect("enter", lambda x, y, data: stack.set_visible_child_name("edit"))
            ev.connect("leave", lambda data: stack.set_visible_child_name("label"))
            wrapper_box.add_controller(ev)
        
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
                wrapper_box.append(stack)
            else:
                wrapper_box.append(label)
            wrapper_box.set_css_classes(["card", "user"])
        elif user_type == "Assistant":
            label = Gtk.Label(
                label=self.current_profile + ": ",
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
                wrapper_box.append(stack)
            else:
                wrapper_box.append(label)
            wrapper_box.set_css_classes(["card", "assistant"])
        elif user_type == "File":
            wrapper_box.append(
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
            wrapper_box.append(
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
        wrapper_box.append(content_box)
        
        return wrapper_box

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

    def add_prompt(self, prompt: str | None):
        if prompt is None:
            return
        self.chat[-1]["enlapsed"] = self.last_generation_time
        self.chat[-1]["Prompt"] = prompt
        self.chat[-1]["InputTokens"] = self.last_token_num[0]
        self.chat[-1]["OutputTokens"] = self.last_token_num[1]

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
        """Show a message in the chat.

        Args:
            message_label: Text content of the message
            restore: Whether the chat is being restored from history
            id_message: ID of the message (-1 for new messages)
            is_user: True if it's a user message
            return_widget: If True, return the widget instead of adding it
            newelle_error: If True, display as an error message
            prompt: Optional prompt to add to history

        Returns:
            Gtk.Widget | None: The message widget if return_widget is True
        """
        if id_message == -1:
            id_message = len(self.chat)

        # Handle empty/whitespace messages
        if message_label == " " * len(message_label) and not is_user:
            if not restore:
                self.chat.append({"User": "Assistant", "Message": message_label})
                self.add_prompt(prompt)
                self._finalize_message_display()
            GLib.idle_add(self.scrolled_chat)
            self.save_chat()
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
                ),
            )
            GLib.idle_add(self.scrolled_chat)
            self.save_chat()
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

        # Parse message into chunks
        chunks = get_message_chunks(
            message_label, self.controller.newelle_settings.display_latex
        )

        # Build the message container
        box = Gtk.Box(
            margin_top=10,
            margin_start=10,
            margin_bottom=10,
            margin_end=10,
            orientation=Gtk.Orientation.VERTICAL,
        )

        # Process state
        state = {
            "codeblock_id": -1,
            "id_message": id_message,
            "original_id": id_message,
            "editable": True,
            "has_terminal_command": False,
            "running_threads": [],
            "tool_call_counter": 0,  # Counter for multiple tool calls in same message
        }

        # Process each chunk
        for chunk in chunks:
            self._process_chunk(
                chunk, box, state, restore, is_user, msg_uuid
            )

        # Finalize and display the message
        return self._finalize_show_message(
            box, state, restore, is_user, return_widget
        )

    def _finalize_message_display(self):
        """Update UI state after message display."""
        GLib.idle_add(self.update_button_text)
        self.status = True
        self.chat_stop_button.set_visible(False)

    def _process_chunk(self, chunk, box, state, restore, is_user, msg_uuid):
        """Process a single message chunk and add appropriate widget to box."""
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
            think.set_thinking(chunk.text)
            box.append(think)
        elif chunk.type == "text":
            self._process_text(chunk, box)

    def _process_codeblock(self, chunk, box, state, restore, is_user, msg_uuid):
        """Process a codeblock chunk."""
        state["codeblock_id"] += 1
        codeblock_id = state["codeblock_id"]
        lang = chunk.lang
        text = chunk.text

        # Check for extension/integration codeblocks
        codeblocks = {
            **self.extensionloader.codeblocks,
            **self.controller.integrationsloader.codeblocks
        }

        if lang in codeblocks:
            self._process_extension_codeblock(
                chunk, box, state, restore, msg_uuid, codeblocks[lang]
            )
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
                box.append(self.get_file_button(obj))
        elif lang == "chart" and not is_user:
            self._process_chart_codeblock(chunk, box)
        elif lang == "latex":
            try:
                box.append(DisplayLatex(text, 16, self.controller.cache_dir))
            except Exception as e:
                print(e)
                box.append(CopyBox(text, lang, parent=self))
        else:
            box.append(CopyBox(
                text, lang, parent=self,
                id_message=state["id_message"],
                id_codeblock=codeblock_id,
                allow_edit=state["editable"]
            ))

    def _process_extension_codeblock(self, chunk, box, state, restore, msg_uuid, extension):
        """Process a codeblock handled by an extension."""
        lang = chunk.lang
        value = chunk.text

        try:
            # Check if extension supports UUID parameter (retrocompatibility)
            sig = inspect.signature(extension.get_gtk_widget)
            supports_uuid = len(sig.parameters) == 3

            if restore:
                widget = (extension.restore_gtk_widget(value, lang, msg_uuid)
                          if supports_uuid else extension.restore_gtk_widget(value, lang))
            else:
                widget = (extension.get_gtk_widget(value, lang, msg_uuid)
                          if supports_uuid else extension.get_gtk_widget(value, lang))

            if widget is not None:
                box.append(widget)

            # Check if extension provides both widget and answer
            if widget is None or extension.provides_both_widget_and_answer(value, lang):
                self._setup_extension_async_response(
                    chunk, box, state, restore, extension, widget
                )

        except Exception as e:
            print(f"Extension error {extension.id}: {e}")
            box.append(CopyBox(
                chunk.text, lang, parent=self,
                id_message=state["id_message"],
                id_codeblock=state["codeblock_id"],
                allow_edit=state["editable"]
            ))

    def _setup_extension_async_response(self, chunk, box, state, restore, extension, widget):
        """Set up async response handling for extension codeblocks."""
        lang = chunk.lang
        value = chunk.text
        # state["editable"] = False
        state["has_terminal_command"] = True

        # Get console reply if restoring
        state["id_message"] += 1
        reply_from_console = self._get_console_reply(state["id_message"])

        # Create result handler and UI widget
        if widget is not None:
            # Widget handles its own display, just show errors
            def on_result(code):
                if not code[0]:
                    self.add_message("Error", code[1])
        else:
            # Create expander to show result
            text_expander = Gtk.Expander(
                label=lang,
                css_classes=["toolbar", "osd"],
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
            )
            text_expander.set_expanded(False)
            box.append(text_expander)

            # Capture chunk text in closure
            chunk_text = value

            def on_result(code, expander=text_expander, text=chunk_text):
                expander.set_child(
                    Gtk.Label(
                        wrap=True,
                        wrap_mode=Pango.WrapMode.WORD_CHAR,
                        label=f"{text}\n{code[1]}",
                        selectable=True,
                    )
                )

        # Create and start thread - capture variables in closure
        ext = extension
        val = value
        lng = lang
        is_restore = restore
        console_reply = reply_from_console

        def get_response():
            if not is_restore:
                response = ext.get_answer(val, lng)
                code = (True, response) if response is not None else (False, "Error:")
            else:
                code = (True, console_reply)
            self.chat.append({"User": "Console", "Message": " " + str(code[1])})
            GLib.idle_add(on_result, code)

        t = threading.Thread(target=get_response)
        t.start()
        state["running_threads"].append(t)

    def _process_image_codeblock(self, text, box):
        """Process an image codeblock."""
        for line in text.split("\n"):
            if not line.strip():
                continue
            image = Gtk.Image(css_classes=["image"])
            if line.startswith("data:image/jpeg;base64,"):
                data = line[len("data:image/jpeg;base64,"):]
                raw_data = base64.b64decode(data)
                loader = GdkPixbuf.PixbufLoader()
                loader.write(raw_data)
                loader.close()
                image.set_from_pixbuf(loader.get_pixbuf())
            elif line.startswith(("https://", "http://")):
                # Capture image in closure
                img = image
                load_image_with_callback(
                    line,
                    lambda pixbuf_loader, i=img: i.set_from_pixbuf(pixbuf_loader.get_pixbuf())
                )
            else:
                image.set_from_file(line)
            box.append(image)

    def _process_video_codeblock(self, text, box):
        """Process a video codeblock."""
        for line in text.split("\n"):
            if not line.strip():
                continue
            video = Gtk.Video(css_classes=["video"], vexpand=True, hexpand=True)
            video.set_size_request(-1, 400)
            video.set_file(Gio.File.new_for_path(line))
            box.append(video)

    def _process_console_codeblock(self, chunk, box, state, restore):
        """Process a console command codeblock."""
        # state["editable"] = False
        state["id_message"] += 1
        command = chunk.text

        # Check if auto-run is allowed
        dangerous_commands = ["rm ", "apt ", "sudo ", "yum ", "mkfs "]
        can_auto_run = (
            self.controller.newelle_settings.auto_run
            and not any(cmd in command for cmd in dangerous_commands)
            and self.auto_run_times < self.controller.newelle_settings.max_run_times
        )

        if can_auto_run:
            state["has_terminal_command"] = True
            text_expander = Gtk.Expander(
                label="Console",
                css_classes=["toolbar", "osd"],
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
            )
            text_expander.set_expanded(False)
            box.append(text_expander)

            reply_from_console = self._get_console_reply(state["id_message"])

            # Capture variables in closure
            cmd = command
            is_restore = restore
            console_reply = reply_from_console
            expander = text_expander

            def run_command():
                if not is_restore:
                    path = os.path.normpath(self.main_path)
                    code = self.execute_terminal_command(cmd)
                    self.chat.append({"User": "Console", "Message": " " + str(code[1])})
                else:
                    path = self.main_path
                    code = (True, console_reply)

                text = f"[User {path}]:$ {cmd}\n{code[1]}"

                def apply_result():
                    expander.set_child(
                        Gtk.Label(
                            wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR,
                            label=text,
                            selectable=True,
                        )
                    )
                    if not code[0]:
                        self.add_message("Error", expander)

                GLib.idle_add(apply_result)

            t = threading.Thread(target=run_command)
            if self.controller.newelle_settings.parallel_tool_execution:
                t.start()
            state["running_threads"].append(t)

            if not restore:
                self.auto_run_times += 1
        else:
            if not restore:
                self.chat.append({"User": "Console", "Message": "None"})
            box.append(CopyBox(
                command, "console", self,
                state["id_message"],
                id_codeblock=state["codeblock_id"],
                allow_edit=state["editable"]
            ))

    def _process_chart_codeblock(self, chunk, box):
        """Process a chart codeblock."""
        result = {}
        percentages = False

        for line in chunk.text.split("\n"):
            parts = line.split("-")
            if len(parts) != 2:
                box.append(CopyBox(chunk.text, "chart", parent=self))
                return

            key = parts[0].strip()
            percentages = "%" in parts[1]
            value_str = "".join(c for c in parts[1] if c.isdigit() or c == ".")
            try:
                result[key] = float(value_str)
            except ValueError:
                result[key] = 0

        if result:
            box.append(BarChartBox(result, percentages))

    def _process_tool_call(self, chunk, box, state, restore):
        """Process a tool call chunk."""
        tool_name = chunk.tool_name
        args = chunk.tool_args
        tool = self.controller.tools.get_tool(tool_name)

        state["id_message"] += 1
        if not restore:
            self.controller.msgid = state["id_message"]

        if not tool:
            widget = CopyBox(chunk.text, "tool_call", parent=self)
            box.append(widget)
            return

        # Generate or retrieve tool call UUID
        tool_call_id = state.get("tool_call_counter", 0)
        state["tool_call_counter"] = tool_call_id + 1
        
        if not restore:
            tool_uuid = str(uuid.uuid4())[:8]  # Short UUID for readability
        else:
            # Retrieve UUID from existing console reply
            tool_uuid = self._get_tool_call_uuid(state["id_message"], tool_name, tool_call_id)
        # state["editable"] = False
        state["has_terminal_command"] = True

        # Make tool UUID accessible via ui_controller during execution
        self.controller.current_tool_uuid = tool_uuid

        try:
            # Pass tool_uuid to restore function
            if restore:
                result = tool.restore(msg_id=state["id_message"], tool_uuid=tool_uuid, **args)
            else:
                result = tool.execute(**args)
            widget = result.widget

            if widget is not None:
                # Tool provides its own widget
                def on_result(code, w=widget):
                    if not code[0]:
                        self.add_message("Error", code[1])
            else:
                # Create ToolWidget for display
                tool_widget = ToolWidget(tool.name, chunk.text)

                def on_result(code, tw=tool_widget):
                    tw.set_result(code[0], code[1])
                widget = tool_widget

            reply_from_console = self._get_tool_response(state["id_message"], tool_name, tool_uuid)

            # Capture variables in closure
            is_restore = restore
            console_reply = reply_from_console
            tool_result = result
            callback = on_result
            t_name = tool_name
            t_uuid = tool_uuid

            def get_response():
                if not is_restore:
                    response = tool_result.get_output()
                    code = (True, response) if response is not None else (False, "Error:")
                    # Store tool response with identifiable format directly in message
                    formatted_response = f"[Tool: {t_name}, ID: {t_uuid}]\n{code[1]}"
                    self.chat.append({
                        "User": "Console",
                        "Message": formatted_response,
                    })
                else:
                    code = (True, console_reply)
                GLib.idle_add(callback, code)

            t = threading.Thread(target=get_response)
            # Restore expects all tools to return things instantly and do not take any action, so we run them in parallel
            # They are not considered for the response
            if self.controller.newelle_settings.parallel_tool_execution or (restore):
                t.start()
            state["running_threads"].append(t)
            box.append(widget)

        except Exception as e:
            print(f"Tool error {tool.name}: {e}")

    def _process_table(self, chunk, box):
        """Process a table chunk."""
        try:
            box.append(self.create_table(chunk.text.split("\n")))
        except Exception as e:
            print(e)
            box.append(CopyBox(chunk.text, "table", parent=self))

    def _process_inline_chunks(self, chunk, box):
        """Process inline chunks (text with inline LaTeX)."""
        if not chunk.subchunks:
            return

        # Create overlay with hidden label for sizing
        overlay = Gtk.Overlay()
        label = Gtk.Label(
            label=" ".join(ch.text for ch in chunk.subchunks),
            wrap=True
        )
        label.set_opacity(0)
        overlay.set_child(label)

        # Create textview for content
        textview = MarkupTextView()
        textview.set_valign(Gtk.Align.START)
        textview.set_hexpand(True)
        overlay.add_overlay(textview)
        overlay.set_measure_overlay(textview, True)

        buffer = textview.get_buffer()
        text_iter = buffer.get_start_iter()

        for subchunk in chunk.subchunks:
            if subchunk.type == "text":
                textview.add_markup_text(text_iter, markwon_to_pango(subchunk.text))
            elif subchunk.type == "latex_inline":
                try:
                    anchor = buffer.create_child_anchor(text_iter)
                    font_size = int(5 + (self.controller.newelle_settings.zoom / 100 * 4))
                    latex = InlineLatex(subchunk.text, font_size)

                    # Embed in overlay to avoid misalignment
                    latex_overlay = Gtk.Overlay()
                    latex_overlay.add_overlay(latex)
                    spacer = Gtk.Box()
                    spacer.set_size_request(latex.picture.dims[0], latex.picture.dims[1] + 1)
                    latex_overlay.set_child(spacer)
                    latex.set_margin_top(5)
                    textview.add_child_at_anchor(latex_overlay, anchor)
                except Exception:
                    buffer.insert(text_iter, LatexNodes2Text().latex_to_text(subchunk.text))

        box.append(overlay)

    def _process_latex(self, chunk, box):
        """Process a LaTeX chunk."""
        try:
            box.append(DisplayLatex(chunk.text, 16, self.controller.cache_dir))
        except Exception:
            box.append(CopyBox(chunk.text, "latex", parent=self))

    def _process_text(self, chunk, box):
        """Process a text chunk."""
        if chunk.text == ".":
            return
        box.append(
            Gtk.Label(
                label=markwon_to_pango(chunk.text),
                wrap=True,
                halign=Gtk.Align.START,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                width_chars=1,
                selectable=True,
                use_markup=True,
            )
        )

    def _get_console_reply(self, id_message):
        """Get existing console reply from chat history if available."""
        idx = min(id_message, len(self.chat) - 1)
        if idx >= 0 and self.chat[idx].get("User") == "Console":
            return self.chat[idx]["Message"]
        return None

    def _get_tool_response(self, id_message, tool_name, tool_uuid):
        """Get existing tool response from chat history by tool name and UUID."""
        # Search forward from id_message for matching tool response
        for i in range(id_message, len(self.chat)):
            entry = self.chat[i]
            if entry.get("User") == "Console":
                msg = entry.get("Message", "")
                # Check if message contains matching tool header
                if msg.startswith(f"[Tool: {tool_name}, ID: {tool_uuid}]"):
                    # Extract the actual response after the header
                    lines = msg.split("\n", 1)
                    return lines[1] if len(lines) > 1 else ""
                # Legacy format: no tool header, use position-based matching
                if not msg.startswith("[Tool:"):
                    return msg
        return None

    def _get_tool_call_uuid(self, id_message, tool_name, tool_call_index):
        """Get tool call UUID from chat history during restore."""
        import re
        # Count tool responses to find the right one by index
        count = 0
        for i in range(id_message, len(self.chat)):
            entry = self.chat[i]
            if entry.get("User") == "Console":
                msg = entry.get("Message", "")
                # Parse tool header: [Tool: name, ID: uuid]
                match = re.match(r'\[Tool: ([^,]+), ID: ([^\]]+)\]', msg)
                if match:
                    parsed_name, parsed_uuid = match.groups()
                    if parsed_name == tool_name:
                        if count == tool_call_index:
                            return parsed_uuid
                        count += 1
                # Legacy format without tool tracking
                elif not msg.startswith("[Tool:"):
                    return str(uuid.uuid4())[:8]
        return str(uuid.uuid4())[:8]  # Fallback for new calls

    def _finalize_show_message(self, box, state, restore, is_user, return_widget):
        """Finalize message display and handle thread completion."""
        user_type = "User" if is_user else "Assistant"

        if return_widget:
            return box

        self.add_message(user_type, box, state["original_id"], state["editable"])

        if not state["has_terminal_command"]:
            if not restore:
                self._finalize_message_display()
                self.chats[self.chat_id]["chat"] = self.chat
        else:
            if not restore and not is_user:
                # Wait for all threads to complete, then send follow-up
                threads = state["running_threads"]
                parallel = self.controller.newelle_settings.parallel_tool_execution

                def wait_and_continue():
                    if not parallel:
                        for t in threads:
                            t.start()
                            t.join()
                    else:
                        for t in threads:
                            t.join()
                    if threads:
                        self.send_message(manual=False)

                self.chats[self.chat_id]["chat"] = self.chat
                threading.Thread(target=wait_and_continue).start()

        GLib.idle_add(self.scrolled_chat)
        self.save_chat()
        return None

    def create_table(self, table):
        """Create a table

        Args:
            table (): markdown table code

        Returns:
           table widget
        """
        data = []
        for row in table:
            cells = row.strip("|").split("|")
            data.append([cell.strip() for cell in cells])
        model = Gtk.ListStore(*[str] * len(data[0]))
        for row in data[1:]:
            if not all(
                len(element.replace(":", "").replace(" ", "").replace("-", "").strip())
                == 0
                for element in row
            ):
                r = []
                for element in row: 
                    r.append(simple_markdown_to_pango(LatexNodes2Text().latex_to_text(element)))
                model.append(r)
        self.treeview = Gtk.TreeView(
            model=model, css_classes=["toolbar", "view", "transparent"]
        )

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, markup=i)
            self.treeview.append_column(column)
        scroll = Gtk.ScrolledWindow(child=self.treeview, propagate_natural_height=True, propagate_natural_width=True, vscrollbar_policy=Gtk.PolicyType.NEVER,)
        return scroll

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

        old_message = box.get_last_child()
        if old_message is None:
            return

        entry = MultilineEntry()
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
        box.remove(old_message)
        box.append(entry)

    def reload_message(self, message_id: int):
        """Reload a message

        Args:
            message_id (int): the id of the message to reload
        """
        if len(self.messages_box) < message_id:
            return
        if self.chat[message_id]["User"] == "Console":
            return
        message_box = self.messages_box[message_id + 1]  # +1 to fix message warning
        old_label = message_box.get_last_child()
        if old_label is not None:

            message_box.remove(old_label)
            message_box.append(
                self.show_message(
                    self.chat[message_id]["Message"],
                    id_message=message_id,
                    is_user=self.chat[message_id]["User"] == "User",
                    return_widget=True,
                    restore=True
                )
            )

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

        apply_edit_stack.set_visible_child_name("edit")
        self.chat[int(gesture.get_name())]["Message"] = entry.get_text()
        self.save_chat()
        box.remove(entry)
        box.append(
            self.show_message(
                entry.get_text(),
                restore=True,
                id_message=int(gesture.get_name()),
                is_user=self.chat[int(gesture.get_name())]["User"] == "User",
                return_widget=True,
            )
        )

    def cancel_edit_message(self, gesture, box: Gtk.Box, apply_edit_stack: Gtk.Stack):
        """Restore the old message

        Args:
            gesture (): widget with the id of the message to edit as name
            box: box of the message
            apply_edit_stack: stack with the edit controls
        """
        entry = self.edit_entries[int(gesture.get_name())]
        self.focus_input()
        apply_edit_stack.set_visible_child_name("edit")
        box.remove(entry)
        box.append(
            self.show_message(
                self.chat[int(gesture.get_name())]["Message"],
                restore=True,
                id_message=int(gesture.get_name()),
                is_user=self.chat[int(gesture.get_name())]["User"] == "User",
                return_widget=True,
            )
        )

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
        self.save_chat()
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

    def build_edit_box(self, box, id):
        """Create the box and the stack with the edit buttons

        Args:
            box (): box of the message
            id (): id of the message

        Returns:
            Gtk.Stack
        """
        has_prompt = len(self.chat) > int(id) and "Prompt" in self.chat[int(id)]
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
        # Prompt box
        if has_prompt:
            prompt_box = Gtk.Box(halign=Gtk.Align.CENTER)
            button = Gtk.Button(
                icon_name="question-round-outline-symbolic",
                css_classes=["flat", "accent"],
                valign=Gtk.Align.CENTER,
                halign=Gtk.Align.CENTER,
            )
            button.connect("clicked", self.show_prompt, int(id))
            prompt_box.append(button)
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
        return apply_edit_stack

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
            halign=Gtk.Align.START,
        )
        self.messages_box.append(box)
        # Create edit controls
        if editable:
            apply_edit_stack = self.build_edit_box(box, str(id_message))
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message, box, apply_edit_stack)
            evk.set_name(str(id_message))
            evk.set_button(3)
            box.add_controller(evk)
            ev = Gtk.EventControllerMotion.new()

            stack = Gtk.Stack()
            ev.connect("enter", lambda x, y, data: stack.set_visible_child_name("edit"))
            ev.connect("leave", lambda data: stack.set_visible_child_name("label"))
            box.add_controller(ev)

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
                box.append(stack)
            else:
                box.append(label)
            box.set_css_classes(["card", "user"])
        if user == "Assistant":
            label = Gtk.Label(
                label=self.current_profile + ": ",
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
                box.append(stack)
            else:
                box.append(label)
            box.set_css_classes(["card", "assistant"])
        if user == "Done":
            box.append(
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
            box.append(
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
            box.append(
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
            box.append(
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
            box.append(box_warning)
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
            box.append(box_warning)
            box.set_halign(Gtk.Align.CENTER)
            box.set_css_classes(["card"])
        elif message is not None:
            box.append(message)
        self.chat_list_block.append(box)
        return box

    def save_chat(self):
        """Save the chat to a file"""
        self.controller.save_chats()

    def execute_terminal_command(self, command):
        """Run console commands

        Args:
            command (): command to run

        Returns:
           output of the command
        """
        os.chdir(os.path.expanduser(self.main_path))
        console_permissions = ""
        if not self.controller.newelle_settings.virtualization:
            console_permissions = " ".join(get_spawn_command())
        commands = command.split(" && ")
        txt = ""
        path = self.main_path
        for t in commands:
            if txt != "":
                txt += " && "
            if "cd " in t:
                txt += t
                p = (t.split("cd "))[min(len(t.split("cd ")), 1)]
                explorer = self.get_current_explorer_panel()
                if explorer is not None:
                    v = explorer.get_target_directory(path, p)
                    if not v[0]:
                        Adw.Toast(title=_("Wrong folder path"), timeout=2)
                    else:
                        path = v[1]
            else:
                txt += console_permissions + " " + t
        process = subprocess.Popen(
            txt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
        )
        outputs = []

        def read_output(process, outputs):
            try:
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    outputs.append((False, stderr.decode()))
                else:
                    if stdout.decode() == "":
                        outputs.append((True, "Done"))
                    outputs.append((True, stdout.decode()))
            except Exception as e:
                pass

        output_thread = threading.Thread(target=read_output, args=(process, outputs))
        output_thread.start()
        for i in range(5):
            time.sleep(i)
            if outputs != []:
                break
        else:
            self.streams.append(process)
            outputs = [
                (
                    True,
                    _("Thread has not been completed, thread number: ")
                    + str(len(self.streams)),
                )
            ]
        if os.path.exists(os.path.expanduser(path)):
            os.chdir(os.path.expanduser(path))
            self.main_path = path
            explorer = self.get_current_explorer_panel()
            if explorer is not None:
                explorer.main_path = path
            GLib.idle_add(self.update_explorer_panels)
        else:
            Adw.Toast(title=_("Failed to open the folder"), timeout=2)
        if len(outputs[0][1]) > 1000:
            new_value = outputs[0][1][0:1000] + "..."
            outputs = ((outputs[0][0], new_value),)
        return outputs[0]

    def update_explorer_panels(self):
        tabs = self.canvas_tabs.get_n_pages()
        for i in range(tabs):
            page = self.canvas_tabs.get_nth_page(i)
            child = page.get_child()
            if child is not None and hasattr(child, "main_path"):
                child.update_folder()
    
    def get_current_explorer_panel(self) -> ExplorerPanel | None:
        """Get the current explorer panel if focused

        Returns:
            the current explorer panel 
        """
        tab = self.canvas_tabs.get_selected_page()
        if tab is not None and hasattr(tab.get_child(), "main_path"):
            return tab.get_child()

    def get_current_browser_panel(self) -> BrowserWidget | None:
        """Get the current browser panel if focused

        Returns: the current browser panel 
        """
        tab = self.canvas_tabs.get_selected_page()
        if tab is not None and hasattr(tab.get_child(), "webview"):
            return tab.get_child()

    def show_sidebar(self):
        self.main_program_block.set_name("visible")
        self.main_program_block.set_show_sidebar(True)
        
    def add_terminal_tab(self, action=None, param=None, command=None):
        """Add a terminal tab"""
        command = "" if command is None else command + ";"

        cmd = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;" + command + " exec bash"]
        terminal = Terminal(cmd)
        terminal.set_vexpand(True)
        terminal.set_hexpand(True)
        
        # Create a box to hold the terminal
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(terminal)
        
        # Add the tab
        tab = self.canvas_tabs.append(box)
        tab.set_title("Terminal")
        tab.set_icon(Gio.ThemedIcon(name="gnome-terminal-symbolic"))
        self.show_sidebar()
        return tab

    def add_browser_tab(self, action=None, param=None, url=None):
        """Add a browser tab"""
        if url is None:
            url = self.controller.newelle_settings.initial_browser_page
        if self.controller.newelle_settings.browser_session_persist:
            session = self.controller.config_dir + "/bsession.json"
        else:
            session = None
        browser = BrowserWidget(url,self.controller.newelle_settings.browser_search_string, session)
        
        # Add the tab
        tab = self.canvas_tabs.append(browser)
        tab.set_title("Browser")
        tab.set_icon(Gio.ThemedIcon(name="internet-symbolic"))
        # Update tab title when page changes
        def on_page_changed(browser, url, title, favicon):
            if title:
                tab.set_title(title)
            if favicon:
                tab.set_icon(favicon)
        
        browser.connect("page-changed", on_page_changed)
        browser.connect("attach-clicked", self._on_attach_clicked)
        def update_favicon():
            tab.set_icon(browser.favicon_pixbuf)
        browser.connect("favicon-changed", lambda b,s: update_favicon())
        self.show_sidebar()
        self.canvas_tabs.set_selected_page(tab)
        return tab

    def _on_attach_clicked(self, browser):
        text = "```website\n" + browser.get_current_url() + "\n```"
        self.chat.append({"User": "User", "Message": text})
        self.hide_placeholder()
        self.show_message(text, False, is_user=True)
    
    def add_explorer_tab(self, tabview=None, path=None):
        """Add an explorer tab

        Args:
            path (): path of the tab
        """
        if path is None:
            path = self.main_path
        if not os.path.isdir(os.path.expanduser(path)):
            return self.add_editor_tab(None, path)
        panel = ExplorerPanel(self.controller, path)
        tab = self.canvas_tabs.append(panel)
        panel.set_tab(tab)
        panel.connect("new-tab-requested", self.add_explorer_tab)
        panel.connect("path-changed", self.update_path)
        panel.connect("open-terminal-requested", lambda panel, path: self.add_terminal_tab(None, None, path))
        self.show_sidebar()
        self.canvas_tabs.set_selected_page(tab)
        return tab

    def update_path(self, panel, path):
        self.main_path = path

    def add_editor_tab(self, tabview=None, file=None):
        if file is not None:
            base_title = os.path.basename(file)
            editor = CodeEditorWidget(scheme=self.controller.newelle_settings.editor_color_scheme)
            editor.load_from_file(file)
            editor.connect("add-to-chat", self.add_file_to_chat, file)
            tab = self.canvas_tabs.append(editor)
            editor.connect("edit_state_changed", self._on_editor_modified, tab, base_title)
            tab.set_title(base_title)
            tab.set_icon(Gio.ThemedIcon(name=File(self.main_path, file).get_icon_name()))
            self.show_sidebar()
            self.canvas_tabs.set_selected_page(tab)
            return tab

    def add_editor_tab_inline(self, id_message, id_codeblock, content, lang):
        editor = CodeEditorWidget(scheme=self.controller.newelle_settings.editor_color_scheme)
        editor.load_from_string(content, lang)
        tab = self.canvas_tabs.append(editor)
        base_title = "Message " + str(id_message) + " " + str(id_codeblock)
        tab.set_title(base_title)
        editor.connect("edit_state_changed", self._on_editor_modified, tab, base_title)
        editor.connect("content-saved", lambda editor, _: self.edit_copybox(id_message, id_codeblock, editor.get_text(), editor))
        self.canvas_tabs.set_selected_page(tab)
        self.show_sidebar()
        return tab

    def edit_copybox(self, id_message, id_codeblock, new_content, editor=None):
        message_content = self.chat[id_message]["Message"]
        replace = replace_codeblock(message_content, id_codeblock, new_content)
        self.chat[id_message]["Message"] = replace
        self.reload_message(id_message)
        if editor is not None:
            editor.saved()
        
    def add_file_to_chat(self, widget, path):
        message_label = self.get_file_button(path)
        self.chat.append({"User": "File", "Message": " " + path})
        self.add_message("File", message_label)
        self.chats[self.chat_id]["chat"] = self.chat

    def _on_editor_modified(self, editor, param, tab, base_title):
        """Update the tab icon and title when the editor's modified state changes."""
        if editor.is_modified:
            tab.set_title(base_title + " •")  # Add indicator
        else:
            tab.set_title(base_title)  # Remove indicator

    def save(self):
        tab = self.canvas_tabs.get_selected_page()
        if tab is None:
            return
        editor = tab.get_child()
        if editor is not None and hasattr(editor, "save"):
            editor.save()
    
    def add_tab(self, tab):
        self.canvas_tabs.add_page(tab.get_child(), tab)

    def generate_chat_name(self, button, multithreading=False):
        """Generate the name of the chat using llm. Reloaunches on another thread if not already in one"""
        if multithreading:
            if len(self.chats[int(button.get_name())]["chat"]) < 2:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("Chat is empty"), timeout=2)
                )
                return False
            spinner = Gtk.Spinner(spinning=True)
            button.set_child(spinner)
            button.set_can_target(False)
            button.set_has_frame(True)

            self.secondary_model.set_history(
                [], self.get_history(self.chats[int(button.get_name())]["chat"])
            )
            name = self.secondary_model.generate_chat_name(
                self.prompts["generate_name_prompt"]
            )
            if name is None:
                button.set_icon_name("warning-outline-symbolic")
                button.can_target = True
                button.remove_css_class("suggested-action")
                button.add_css_class("error")
                GLib.timeout_add(2000, self.update_history)
            else:
                name = remove_thinking_blocks(name)
                if name is None:
                    self.update_history()
                    return
                name = remove_markdown(name)
                if name != "Chat has been stopped":
                    self.chats[int(button.get_name())]["name"] = name
                self.update_history()
        else:
            threading.Thread(
                target=self.generate_chat_name, args=[button, True]
            ).start()

    def _init_stdout_monitoring(self):
        """Initialize stdout monitoring from program start""" 
        # Create the dialog but don't show it yet
        self.stdout_monitor_dialog = StdoutMonitorDialog(self) 
        # Start monitoring immediately with capturing enabled by default
        # We need to initialize the monitor without showing the dialog
        self.stdout_monitor_dialog.stdout_monitor = StdoutMonitor(self.stdout_monitor_dialog._on_stdout_received)
        self.stdout_monitor_dialog.stdout_monitor.start_monitoring()
        
    def show_stdout_monitor_dialog(self, parent=None):
        """Create and show a dialog to monitor stdout in real-time with terminal interface"""
        if parent is None:
            parent = self
        if self.stdout_monitor_dialog is None:
            self._init_stdout_monitoring()
        self.stdout_monitor_dialog.parent_window = parent
        # Show the dialog and populate it with existing captured data
        self.stdout_monitor_dialog.show_window()
        
        # If monitoring was already active, update the dialog's UI state
        if (self.stdout_monitor_dialog.stdout_monitor and 
            self.stdout_monitor_dialog.stdout_monitor.is_active()):
            # Set the toggle button to active state
            if self.stdout_monitor_dialog.stdout_toggle_button:
                self.stdout_monitor_dialog.stdout_toggle_button.set_active(True)
                # Update status labels
                if self.stdout_monitor_dialog.stdout_status_label:
                    self.stdout_monitor_dialog.stdout_status_label.set_label(_("Monitoring: Active"))
                # Update button appearance
                self.stdout_monitor_dialog.stdout_toggle_button.set_icon_name("media-playback-stop-symbolic")
                self.stdout_monitor_dialog.stdout_toggle_button.remove_css_class("suggested-action")
                self.stdout_monitor_dialog.stdout_toggle_button.add_css_class("destructive-action")
                
            # Start the display update timer for the dialog
            GLib.timeout_add(100, self.stdout_monitor_dialog._update_stdout_display)



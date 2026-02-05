from select import select
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
import gettext
import datetime
from gi.repository import Gtk, Adw, Pango, Gio, Gdk, GObject, GLib, GdkPixbuf

from .ui.settings import Settings

from .ui.profile import ProfileDialog
from .ui.presentation import PresentationWindow
from .ui.widgets import File, CopyBox, BarChartBox, MarkupTextView, DocumentReaderWidget, TipsCarousel, BrowserWidget, Terminal, CodeEditorWidget, ToolWidget
from .ui.explorer import ExplorerPanel
from .ui.widgets import MultilineEntry, ProfileRow, DisplayLatex, InlineLatex, ThinkingWidget, Message, ChatRow, ChatHistory, ChatTab
from .ui.stdout_monitor import StdoutMonitorDialog
from .utility.stdout_capture import StdoutMonitor
from .constants import AVAILABLE_LLMS, SCHEMA_ID, SETTINGS_GROUPS
from .utility.system import get_spawn_command, open_website, is_flatpak
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
from .controller import NewelleController, ReloadType, NewelleSettings
from .ui_controller import UIController


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
        self.automatic_stt_status = False
        self.model_loading_spinner_button = None
        self.model_loading_spinner_separator = None
        self.model_loading_status = False
        self.last_generation_time = None
        self.last_token_num = None
        self.last_update = 0
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
        self.path = self.controller.config_dir
        
        # Ensure all chats have unique IDs and handle branching fields

        for chat_entry in self.chats:
            if "id" not in chat_entry:
                chat_entry["id"] = str(uuid.uuid4())
            if "branched_from" not in chat_entry:
                chat_entry["branched_from"] = None
        
                chat_entry["branched_from"] = None
        
        # RAG Indexes to documents for each chat

        self.chat_documents_index = {}
        self.settings = self.controller.settings
        self.extensionloader = self.controller.extensionloader
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
        
        # Add export/import section as a submenu
        export_import_menu = Gio.Menu()
        export_current = Gio.MenuItem.new(_("Export current chat"), "app.export_current_chat")
        export_all = Gio.MenuItem.new(_("Export all chats"), "app.export_all_chats")
        import_chats = Gio.MenuItem.new(_("Import chats"), "app.import_chats")
        export_import_menu.append_item(export_current)
        export_import_menu.append_item(export_all)
        export_import_menu.append_item(import_chats)
        
        menu.append_submenu(_("Export/Import"), export_import_menu)
        
        menu.append(_("About"), "app.about")
        menu_button.set_menu_model(menu)
        
        
        self.chat_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True, css_classes=["view"]
        )
        self.chat_header = Adw.HeaderBar(css_classes=["flat", "view"], show_start_title_buttons=False, show_end_title_buttons=True)
        self.chat_header.set_title_widget(
            Gtk.Label(label=_("Chat"), css_classes=["title", "window-bar-label"])
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
            orientation=Gtk.Orientation.VERTICAL, hexpand=True, css_classes=["background"]
        )
        self.chat_panel_header = Adw.HeaderBar(
            css_classes=["flat"], show_end_title_buttons=False, show_start_title_buttons=True
        )
        self.chat_panel_header.set_title_widget(
            Gtk.Label(label=_("History"), css_classes=["title"])
        )
        self.chats_secondary_box.append(self.chat_panel_header)
        self.chat_panel_header.pack_end(menu_button)
        
        # Chat list with navigation-sidebar styling for Adwaita look
        self.chats_buttons_block = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.chats_buttons_block.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.chats_buttons_scroll_block = Gtk.ScrolledWindow(vexpand=True)
        self.chats_buttons_scroll_block.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.chats_buttons_scroll_block.set_child(self.chats_buttons_block)
        self.chats_secondary_box.append(self.chats_buttons_scroll_block)
        
        # New chat button with Adwaita pill style
        new_chat_button = Gtk.Button(
            valign=Gtk.Align.END,
            css_classes=["suggested-action", "pill"],
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
        )
        new_chat_button_content = Adw.ButtonContent(
            icon_name="list-add-symbolic",
            label=_("New Chat"),
        )
        new_chat_button.set_child(new_chat_button_content)
        new_chat_button.connect("clicked", self.new_chat)
        self.chats_secondary_box.append(new_chat_button)
        self.chats_main_box.append(self.chats_secondary_box)
        self.chats_main_box.append(Gtk.Separator())
        self.main.set_sidebar(Adw.NavigationPage(child=self.chats_main_box, title=_("Chats")))
        self.main.set_content(Adw.NavigationPage(child=self.chat_panel, title=_("Chat")))
        self.main.set_show_sidebar(not self.settings.get_boolean("hide-history-on-launch"))
        self.main.set_name("visible" if self.main.get_show_sidebar() else "hide")
        self.main.connect("notify::show-sidebar", lambda x, _ : self.left_panel_toggle_button.set_active(self.main.get_show_sidebar()))
        # Canvas panel
        self.build_canvas()
        # Secondary message block
        self.secondary_message_chat_block = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )

        self.chat_block.append(self.secondary_message_chat_block)
        
        # Chat tabs system - Adw.TabView for multi-tab chat support
        self.chat_tabs = Adw.TabView()
        self.chat_tabs.connect("notify::selected-page", self._on_chat_tab_switched)
        self.chat_tabs.connect("close-page", self._on_chat_tab_close_requested)
        self.chat_tabs.connect("page-detached", lambda *_: self.update_history())
        
        # Tab bar - shows tabs when more than one is open
        self.chat_tab_bar = Adw.TabBar(autohide=True, view=self.chat_tabs, css_classes=["inline"])
        self.secondary_message_chat_block.append(self.chat_tab_bar)
        
        # Tab overview for managing tabs
        self.chat_tab_overview = Adw.TabOverview(
            view=self.chat_tabs, 
            child=self.chat_tabs, 
            enable_new_tab=True,
            show_end_title_buttons=False,
            show_start_title_buttons=False
        )
        self.chat_tab_overview.connect("create-tab", self._on_create_chat_tab)
        
        # Notification block wraps the tab overview
        self.notification_block = Adw.ToastOverlay()
        self.notification_block.set_child(self.chat_tab_overview)
        self.secondary_message_chat_block.append(self.notification_block)
        
        # Header controls on the left: History - Profile - Tab Overview - Add Tab
        self.chat_header.pack_start(self.left_panel_toggle_button)
        
        self.profiles_box = None
        self.refresh_profiles_box()

        self.chat_tab_button = Adw.TabButton(view=self.chat_tabs)
        self.chat_tab_button.connect("clicked", lambda x: self.chat_tab_overview.set_open(not self.chat_tab_overview.get_open()))
        self.chat_header.pack_start(self.chat_tab_button)

        self.add_chat_button = Gtk.Button(css_classes=["flat"], icon_name="list-add-symbolic")
        self.add_chat_button.set_tooltip_text(_("New Tab"))
        self.add_chat_button.connect("clicked", lambda *_: self._on_create_chat_tab(None))
        self.chat_header.pack_start(self.add_chat_button)

        # Explorer panel 
        self.main_program_block.set_show_sidebar(False)

        # Add the initial chat tab
        self.add_chat_tab(self.chat_id)

        def build_model_popup():
            self.chat_header.set_title_widget(self.build_model_popup())

        # Connect sidebar handlers
        self.main.connect("notify::show-sidebar", self.handle_main_block_change)
        self.main.connect("notify::collapsed", self.handle_main_block_change)
        self.main_program_block.connect(
            "notify::show-sidebar", self.handle_second_block_change
        )
        self.main_program_block.connect(
            "notify::collapsed", self.handle_second_block_change
        )

        # Legacy streaming state - kept for compatibility but delegated to active tab
        self.active_tool_results = []
        self.stream_number_variable = 0
        self.stream_tools = False

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

    # Chat Tab Management
    def add_chat_tab(self, chat_id: int) -> Adw.TabPage | None:
        """Add a new chat tab for the given chat_id.
        
        If a tab for this chat already exists, switch to it instead.
        
        Args:
            chat_id: The ID of the chat to open in a new tab
            
        Returns:
            The created or existing tab page, or None if failed
        """
        # Check if tab for this chat already exists
        existing_tab = self.get_tab_for_chat(chat_id)
        if existing_tab is not None:
            self.chat_tabs.set_selected_page(existing_tab)
            return existing_tab
        
        # Validate chat_id
        if chat_id < 0 or chat_id >= len(self.chats):
            return None
        
        # Create new ChatTab widget
        chat_tab = ChatTab(self, chat_id)
        chat_tab.connect("chat-name-changed", self._on_chat_name_changed)
        
        # Add to tab view
        tab_page = self.chat_tabs.append(chat_tab)
        tab_page.set_title(chat_tab.chat_name)
        # No icon by default - only show when generating
        
        # Store reference in widget
        chat_tab.set_tab_page(tab_page)
        
        # Select the new tab
        self.chat_tabs.set_selected_page(tab_page)
        
        return tab_page
    
    def get_tab_for_chat(self, chat_id: int) -> Adw.TabPage | None:
        """Get the tab page for a given chat_id, if it exists.
        
        Args:
            chat_id: The chat ID to find
            
        Returns:
            The tab page if found, None otherwise
        """
        n_pages = self.chat_tabs.get_n_pages()
        for i in range(n_pages):
            page = self.chat_tabs.get_nth_page(i)
            child = page.get_child()
            if isinstance(child, ChatTab) and child.chat_id == chat_id:
                return page
        return None
    
    def get_active_chat_tab(self) -> ChatTab | None:
        """Get the currently active ChatTab widget.
        
        Returns:
            The active ChatTab, or None if no tab is active
        """
        page = self.chat_tabs.get_selected_page()
        if page is not None:
            child = page.get_child()
            if isinstance(child, ChatTab):
                return child
        return None
    
    def _on_chat_tab_switched(self, tab_view, param):
        """Handle chat tab selection changes."""
        tab = self.get_active_chat_tab()
        if tab is not None:
            # Update global chat_id to match selected tab
            self.chat_id = tab.chat_id
            
            # Change profile if remember_profile is enabled and chat has a stored profile
            if self.controller.newelle_settings.remember_profile and "profile" in self.chats[tab.chat_id]:
                target_profile = self.chats[tab.chat_id]["profile"]
                if target_profile != self.current_profile:
                    self.switch_profile(target_profile)
            
            # Update chat list selection
            self.update_history()
    
    def _on_chat_tab_close_requested(self, tab_view, page) -> bool:
        """Handle chat tab close request.
        
        Args:
            tab_view: The TabView
            page: The page being closed
            
        Returns:
            True to prevent closing, False to allow
        """
        child = page.get_child()
        if isinstance(child, ChatTab):
            # Don't allow closing if generation is in progress
            if not child.status:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("Cannot close tab while generating"), timeout=2)
                )
                return True  # Prevent close
            
            # If this is the last tab, create a new one for the current chat first
            if self.chat_tabs.get_n_pages() == 1:
                # Just switch to a new chat tab instead of closing
                self.new_chat(None)
                return True  # Prevent close, we'll handle it via new_chat
        
        return False  # Allow close
    
    def _on_create_chat_tab(self, tab_overview) -> Adw.TabPage:
        """Handle new tab creation from tab overview."""
        # Create a new chat and force open it in a new tab
        return self._create_new_chat_internal(force_new_tab=True)
    
    def _on_chat_name_changed(self, chat_tab, name):
        """Handle chat name change signal from ChatTab."""
        # Update history list
        self.update_history()
    
    def _create_new_chat_internal(self, force_new_tab: bool = False):
        """Internal method to create a new chat and potentially open it in a new tab.
        
        Args:
            force_new_tab: If True, always creates a new tab instead of switching current
            
        Returns:
            The Adw.TabPage if created/switched to
        """
        new_chat_entry = {
            "name": _("Chat %d") % (len(self.chats) + 1),
            "chat": [],
            "id": str(uuid.uuid4()),
            "branched_from": None
        }
        self.chats.append(new_chat_entry)
        # Update all existing tab chat_ids since we inserted at 0
        self._update_tab_chat_ids_after_insert(len(self.chats) - 1)
        self.chat_id = len(self.chats) - 1
        self.save_chat()

        if force_new_tab:
            tab_page = self.add_chat_tab(self.chat_id)
            self.update_history()
            return tab_page
        else:
            # Switch current tab to the new chat instead of creating new tab
            current_tab = self.get_active_chat_tab()
            if current_tab is not None:
                current_tab.switch_to_chat(self.chat_id)
                # Update history AFTER switching so the UI shows correct open state
                self.update_history()
                return current_tab.tab_page
            else:
                # No tabs exist, create one
                tab_page = self.add_chat_tab(self.chat_id)
                self.update_history()
                return tab_page
    
    def _update_tab_chat_ids_after_insert(self, inserted_at: int):
        """Update chat_ids in all tabs after a chat was inserted.
        
        Args:
            inserted_at: The index where a new chat was inserted
        """
        n_pages = self.chat_tabs.get_n_pages()
        for i in range(n_pages):
            page = self.chat_tabs.get_nth_page(i)
            child = page.get_child()
            if isinstance(child, ChatTab) and child._chat_id >= inserted_at:
                child._chat_id += 1
    
    def _update_tab_chat_ids_after_delete(self, deleted_at: int):
        """Update chat_ids in all tabs after a chat was deleted.

        Args:
            deleted_at: The index where a chat was deleted
        """
        n_pages = self.chat_tabs.get_n_pages()
        # Go backwards to safely close tabs
        for i in range(n_pages - 1, -1, -1):
            page = self.chat_tabs.get_nth_page(i)
            child = page.get_child()
            if isinstance(child, ChatTab):
                if child._chat_id == deleted_at:
                    # Close tab showing the deleted chat
                    self.chat_tabs.close_page(page)
                elif child._chat_id > deleted_at:
                    # Update chat_id for tabs after the deleted one
                    child._chat_id -= 1

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
            # Update the send on enter setting for all chat tabs
            send_on_enter = not self.controller.newelle_settings.send_on_enter
            n_pages = self.chat_tabs.get_n_pages()
            for i in range(n_pages):
                page = self.chat_tabs.get_nth_page(i)
                child = page.get_child()
                if isinstance(child, ChatTab):
                    child.input_panel.set_enter_on_ctrl(send_on_enter)
                    for entry in child.chat_history.edit_entries.values():
                        entry.set_enter_on_ctrl(send_on_enter)
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
        """Reload offers and buttons on LLM change for all chat tabs"""
        if not self.first_load:
            n_pages = self.chat_tabs.get_n_pages()
            for i in range(n_pages):
                page = self.chat_tabs.get_nth_page(i)
                child = page.get_child()
                if isinstance(child, ChatTab):
                    child._update_attach_visibility()
                    if not self.model.supports_video_vision():
                        if child.video_recorder is not None:
                            child.video_recorder.stop()
                            child.video_recorder = None
                    child.screen_record_button.set_visible(
                        self.model.supports_video_vision() and not child.attached_image_data
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
        """Focus the input box of the active chat tab."""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.focus_input()
    
    def add_text_to_input(self, text, focus_input=False):
        """Add text to the input of the active chat tab."""
        tab = self.get_active_chat_tab()
        if tab is not None:
            txt = tab.input_panel.get_text()
            txt += "\n" + text 
            tab.input_panel.set_text(txt)
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
        
        self._update_profile_state(profile, groups)
        
        self.focus_input()
        self.refresh_profiles_box()
    
    def _update_profile_state(self, profile: str, groups: list):
        """Update application state for profile switch (optimized - only reloads what changed)
        
        Args:
            profile: The new profile name
            groups: List of settings groups that were changed
        """
        self.current_profile = profile
        
        if len(groups) == 0:
            groups = list(SETTINGS_GROUPS.keys())
        
        reload_types = set()
        for group in groups:
            if group == "LLM":
                reload_types.add(ReloadType.LLM)
                reload_types.add(ReloadType.SECONDARY_LLM)
            elif group == "TTS":
                reload_types.add(ReloadType.TTS)
            elif group == "STT":
                reload_types.add(ReloadType.STT)
            elif group == "Embedding":
                reload_types.add(ReloadType.EMBEDDINGS)
            elif group == "memory":
                reload_types.add(ReloadType.MEMORIES)
            elif group == "websearch":
                reload_types.add(ReloadType.WEBSEARCH)
            elif group == "rag":
                reload_types.add(ReloadType.RAG)
            elif group == "extensions":
                reload_types.add(ReloadType.EXTENSIONS)
            elif group == "prompts":
                reload_types.add(ReloadType.PROMPTS)
            elif group == "tools":
                reload_types.add(ReloadType.TOOLS)
            elif group == "interface":
                reload_types.add(ReloadType.RELOAD_CHAT_LIST)
            elif group == "general":
                reload_types.add(ReloadType.RELOAD_CHAT_LIST)
        
        newsettings = NewelleSettings()
        newsettings.load_settings(self.settings)
        self.newelle_settings = newsettings
        self.profile_settings = newsettings.profile_settings
        
        for reload_type in reload_types:
            self.controller.reload(reload_type)
        
        self.offers = self.controller.newelle_settings.offers
        self.memory_on = self.controller.newelle_settings.memory_on
        self.rag_on = self.controller.newelle_settings.rag_on
        self.tts_enabled = self.controller.newelle_settings.tts_enabled
        self.virtualization = self.controller.newelle_settings.virtualization
        self.prompts = self.controller.newelle_settings.prompts
        
        self.tts = self.controller.handlers.tts
        self.stt = self.controller.handlers.stt
        self.model = self.controller.handlers.llm
        self.secondary_model = self.controller.handlers.secondary_llm
        self.embeddings = self.controller.handlers.embedding
        self.memory_handler = self.controller.handlers.memory
        self.rag_handler = self.controller.handlers.rag
        self.extensionloader = self.controller.extensionloader
        
        if ReloadType.RELOAD_CHAT in reload_types:
            self.show_chat()
        if ReloadType.RELOAD_CHAT_LIST in reload_types:
            self.update_history()
        if ReloadType.LLM in reload_types:
            self.reload_buttons()
            self.update_model_popup()
        if ReloadType.TOOLS in reload_types:
            self.model_popup_settings.refresh_tools_list()
        
        self.tts.connect(
            "start", lambda: GLib.idle_add(self.mute_tts_button.set_visible, True)
        )
        self.tts.connect(
            "stop", lambda: GLib.idle_add(self.mute_tts_button.set_visible, False)
        )
        
        if ReloadType.LLM in reload_types:
            send_on_enter = not self.controller.newelle_settings.send_on_enter
            n_pages = self.chat_tabs.get_n_pages()
            for i in range(n_pages):
                page = self.chat_tabs.get_nth_page(i)
                child = page.get_child()
                if isinstance(child, ChatTab):
                    child.input_panel.set_enter_on_ctrl(send_on_enter)
                    for entry in child.chat_history.edit_entries.values():
                        entry.set_enter_on_ctrl(send_on_enter)

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
        self.recording_button = button  # Store the button for auto_stop_recording
        if self.controller.newelle_settings.automatic_stt:
            self.automatic_stt_status = True
        # button.set_child(Gtk.Spinner(spinning=True))
        button.set_icon_name("media-playback-stop-symbolic")
        try:
            button.disconnect_by_func(self.start_recording)
        except TypeError:
            # Handler was not connected to this function
            pass
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
        try:
            button.disconnect_by_func(self.stop_recording)
        except TypeError:
            pass
        # Reconnect to the active chat tab's start_recording method
        tab = self.get_active_chat_tab()
        if tab is not None:
            button.connect("clicked", tab.start_recording)

    def stop_recording_async(self, button=False):
        """Stop recording and save the file"""
        recognizer = self.stt
        result = recognizer.recognize_file(
            os.path.join(self.controller.cache_dir, "recording.wav")
        )

        def idle_record():
            tab = self.get_active_chat_tab()
            if tab is None:
                return
            if (
                result is not None
                and "stop" not in result.lower()
                and len(result.replace(" ", "")) > 2
            ):
                tab.input_panel.set_text(result)
                tab.on_entry_activate(tab.input_panel)
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
        """Delete file attachment - delegates to the active chat tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.delete_attachment(button)

    def add_file(self, file_path=None, file_data=None):
        """Add a file and update the UI - delegates to the active chat tab

        Args:
            file_path (): file path for the file
            file_data (): file data for the file
        """
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.add_file(file_path, file_data)

    # Flap management
    def on_chat_panel_toggled(self, button: Gtk.ToggleButton):
        if button.get_active():
            self.main.set_name("visible")
            self.main.set_show_sidebar(True)
        else:
            self.main.set_name("hide")
            self.main.set_show_sidebar(False)
   
    def return_to_chat_panel(self, button):
        if self.main.get_collapsed():
            self.main.set_show_sidebar(False)

    def handle_second_block_change(self, *a):
        """Handle flaps reveal/hide"""
        status = self.main_program_block.get_show_sidebar()
        name = self.main_program_block.get_name()
        collapsed = self.main_program_block.get_collapsed()

        if name == "hide" and status:
            self.main_program_block.set_show_sidebar(False)
            return True
        elif name == "visible" and not status and not collapsed:
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
        if toggle_button.get_active():
            self.main_program_block.set_name("visible")
            self.main_program_block.set_show_sidebar(True)
        else:
            self.main_program_block.set_name("hide")
            self.main_program_block.set_show_sidebar(False)

    # UI Functions for chat management
    def send_button_start_spinner(self):
        """Show a spinner on the active tab's send button"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.send_button_start_spinner()

    def remove_send_button_spinner(self):
        """Remove the spinner in the active tab's send button"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.remove_send_button_spinner()

    def on_entry_button_clicked(self, *a):
        """When the send message button is clicked activate the input panel"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.on_entry_button_clicked()

    # Explorer code
    def handle_file_drag(self, DropTarget, data, x, y):
        """Handle file drag and drop

        Args:
            DropTarget ():
            data ():
            x ():
            y ():

        Returns:

        """
        tab = self.get_active_chat_tab()
        if tab is None:
            return False
        if not tab.status:
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
                message_label = tab.chat_history.get_file_button(path)
                if os.path.isdir(path):
                    tab.chat.append({"User": "Folder", "Message": " " + path})
                    tab.chat_history.add_message("Folder", message_label)
                else:
                    tab.chat.append({"User": "File", "Message": " " + path})
                    tab.chat_history.add_message("File", message_label)
                self.chats[tab.chat_id]["chat"] = tab.chat
                tab.chat_history.hide_placeholder()
            else:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("The file is not recognized"), timeout=2)
                )

    def handle_main_block_change(self, *data):
        status = self.main.get_show_sidebar()
        name = self.main.get_name()
        collapsed = self.main.get_collapsed()

        if name == "hide" and status:
            self.main.set_show_sidebar(False)
        elif name == "visible" and not status and not collapsed:
            self.main.set_show_sidebar(True)

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
        """Continue last message in the active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.continue_message()

    def regenerate_message(self, *a):
        """Regenerate last message in the active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.regenerate_message()

    def remove_error(self, idle=False):
        """Remove the last error shown in chat

        Args:
            idle (): if the function is being executed in idle
        """
        if not idle:
            GLib.idle_add(self.remove_error, True)
        tab = self.get_active_chat_tab()
        if tab is not None and tab.chat_history.last_error_box is not None:
            error_row = tab.chat_history.chat_list_block.get_last_child()
            if error_row is None:
                return
            tab.chat_history.chat_list_block.remove(error_row)
            tab.chat_history.last_error_box = None
            tab.last_error_box = None
            
    def _on_branch_requested(self, chat_history, message_id: int):
        """Handle branch-requested signal from ChatHistory."""
        self.create_branch(message_id)

    def _on_clear_requested(self, chat_history):
        """Handle clear-requested signal from ChatHistory."""
        self.clear_chat(None)

    def _on_continue_requested(self, chat_history):
        """Handle continue-requested signal from ChatHistory - deprecated, now handled by ChatTab."""
        pass

    def _on_regenerate_requested(self, chat_history):
        """Handle regenerate-requested signal from ChatHistory - deprecated, now handled by ChatTab."""
        pass

    def _on_stop_requested(self, chat_history):
        """Handle stop-requested signal from ChatHistory - deprecated, now handled by ChatTab."""
        pass

    def _on_files_dropped(self, chat_history, data):
        """Handle files-dropped signal from ChatHistory (file drag and drop)."""
        self.handle_file_drag(None, data, 0, 0)

    def create_branch(self, message_id: int, source_chat_id: int = None):
        """Create a new chat branching from a specific message ID
        
        Args:
            message_id: The message ID to branch from
            source_chat_id: The chat ID to branch from (defaults to current chat_id)
        """
        if source_chat_id is None:
            source_chat_id = self.chat_id
            
        if source_chat_id < 0 or source_chat_id >= len(self.chats):
            return
            
        parent_chat = self.chats[source_chat_id]
        parent_id = parent_chat.get("id")
        
        # Copy messages up to message_id (inclusive)
        branched_messages = parent_chat["chat"][:message_id + 1]
        
        new_chat_entry = {
            "name": parent_chat["name"],
            "chat": copy.deepcopy(branched_messages),
            "id": str(uuid.uuid4()),
            "branched_from": parent_id,
            "profile": parent_chat.get("profile", self.current_profile)
        }
        
        # Append to end
        self.chats.append(new_chat_entry)
        
        # Switch to the new chat - open in new tab
        new_chat_id = len(self.chats) - 1
        self.chat_id = new_chat_id
        
        self.save_chat()
        self.update_history()
        # Open in new tab
        self.add_chat_tab(new_chat_id)
        tab = self.get_active_chat_tab()
        if tab is not None:
            GLib.idle_add(tab.chat_history.update_button_text)

    def update_history(self):
        """Reload chats panel with Adwaita-styled ChatRow widgets, supporting branching hierarchy"""
        # Focus input to avoid removing a focused child
        self.focus_input()

        # Create new list box with Adwaita navigation sidebar styling
        list_box = Gtk.ListBox(css_classes=["navigation-sidebar"])
        list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.chats_list_box = list_box
        self.chats_buttons_scroll_block.set_child(list_box)
        
        list_box.connect("row-activated", self.on_chat_row_activated)
        
        # Build hierarchy map
        id_to_index = {chat.get("id"): i for i, chat in enumerate(self.chats)}
        children_map = {}
        top_level_indices = []
        
        for i, chat in enumerate(self.chats):
            parent_id = chat.get("branched_from")
            if parent_id and parent_id in id_to_index:
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(i)
            else:
                top_level_indices.append(i)
        
        # Handle reverse order if needed for top-level chats
        if self.controller.newelle_settings.reverse_order:
            top_level_indices.reverse()
            
        def add_chat_recursive(index, level=0):
            chat_entry = self.chats[index]
            name = chat_entry["name"]
            is_selected = (index == self.chat_id)
            is_open = self.get_tab_for_chat(index) is not None
            
            # Create ChatRow widget with indentation level
            chat_row = ChatRow(
                chat_name=name,
                chat_index=index,
                is_selected=is_selected,
                level=level,
                is_open=is_open
            )
            
            # Connect signals
            chat_row.connect_signals(
                on_generate=self.generate_chat_name,
                on_edit=lambda btn, row=chat_row: self.edit_chat_name(btn, row.get_edit_stack()),
                on_clone=self.copy_chat,
                on_delete=self.remove_chat
            ) 
            
            list_box.append(chat_row)
            if is_selected:
                list_box.select_row(chat_row)
                
            # Add offspring
            chat_id = chat_entry.get("id")
            if chat_id in children_map:
                # Keep children in the same order as they appear in self.chats (chronological)
                # unless specifically reversed, but usually branches are chronological
                children = children_map[chat_id]
                for child_index in children:
                    add_chat_recursive(child_index, level + 1)
        
        for i in top_level_indices:
            add_chat_recursive(i)
    
    def on_chat_row_activated(self, listbox, row):
        """Handle chat row activation to switch chats"""
        if not hasattr(row, 'chat_index'):
            return
            
        if row.chat_index == self.chat_id:
            self.return_to_chat_panel(row)
        else:
            self.chose_chat(row.chat_index)
    
    def remove_chat(self, button):
        """Remove a chat"""
        deleted_index = int(button.get_name())
        if deleted_index < self.chat_id:
            self.chat_id -= 1
        elif deleted_index == self.chat_id:
            return False
        self.chats.pop(deleted_index)
        # Update chat_ids in all tabs after deletion
        self._update_tab_chat_ids_after_delete(deleted_index)
        self.save_chat()
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
            
        # Get the ChatRow's main_box containing the widgets
        if not hasattr(row, 'main_box') or not hasattr(row, 'name_label'):
            return
            
        # Create an entry to replace the label
        entry = Gtk.Entry()
        entry.set_text(self.chats[chat_index]["name"])
        entry.set_hexpand(True)
        
        # Store original label for restoration
        original_label = row.name_label
        
        # Find the position of the label in the box and replace it
        # The label is between the icon and the actions revealer
        siblings = []
        child = row.main_box.get_first_child()
        while child:
            siblings.append(child)
            child = child.get_next_sibling()
        
        # Find and replace the name_label
        label_index = siblings.index(original_label) if original_label in siblings else -1
        if label_index >= 0:
            row.main_box.remove(original_label)
            # Insert entry at the same position
            if label_index == 0:
                row.main_box.prepend(entry)
            else:
                row.main_box.insert_child_after(entry, siblings[label_index - 1])
        
        # Focus the entry
        entry.grab_focus()
        entry.select_region(0, -1)  # Select all text
        
        # Handle entry activation (Enter key)
        def on_entry_activate(entry):
            new_name = entry.get_text().strip()
            if new_name:
                self.chats[chat_index]["name"] = new_name
                self.save_chat()
                
                # Update tab title if this chat is open in a tab
                tab_page = self.get_tab_for_chat(chat_index)
                if tab_page:
                    tab_page.set_title(new_name)
                    
            self.update_history()
             
        entry.connect("activate", on_entry_activate)
        
    def new_chat(self, button, *a):
        """Create a new chat and open it in a new tab"""
        # Use internal method for consistency
        self._create_new_chat_internal()

    def copy_chat(self, button, *a):
        """Copy a chat into a new chat"""
        source_chat = self.chats[int(button.get_name())]
        self.chats.append(
            {
                "name": source_chat["name"],
                "chat": source_chat["chat"][:],
                "id": str(uuid.uuid4()),
                "branched_from": source_chat.get("id"),
                "profile": source_chat.get("profile", self.current_profile)
            }
        )
        self.update_history()

    def chose_chat(self, id, *a):
        """Switch to another chat - switches current tab or focuses existing tab"""
        self.return_to_chat_panel(None)
        chat_id = int(id)
        
        # Check if this chat is already open in a tab
        existing_tab = self.get_tab_for_chat(chat_id)
        if existing_tab is not None:
            # Switch to existing tab
            self.chat_tabs.set_selected_page(existing_tab)
        else:
            # Switch current tab to the new chat
            current_tab = self.get_active_chat_tab()
            if current_tab is not None and current_tab.status:
                # Current tab is idle, switch its chat
                current_tab.switch_to_chat(chat_id)
            else:
                # Current tab is generating, open in new tab
                self.add_chat_tab(chat_id)
        
        # Update global chat_id
        self.chat_id = chat_id
        
        # Change profile 
        if self.controller.newelle_settings.remember_profile and "profile" in self.chats[chat_id]:
            self.switch_profile(self.chats[chat_id]["profile"])
        
        self.update_history()
        tab = self.get_active_chat_tab()
        if tab is not None:
            GLib.idle_add(tab.chat_history.update_button_text)

    def clear_chat(self, button):
        """Delete current chat history in the active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.clear_chat()

    def stop_chat(self, button=None):
        """Stop generating the message in the active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.stop_chat()

    def on_entry_activate(self, entry):
        """Send a message when input is pressed - delegates to active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.on_entry_activate(tab.input_panel)

    # LLM functions

    def send_bot_response(self, button):
        """Add message to the chat, display the user message and launch a thread to get response"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.send_bot_response(button)

    def generate_suggestions(self):
        """Create the suggestions and update the UI when it's finished"""
        tab = self.get_active_chat_tab()
        if tab is None:
            return
        suggestions = self.secondary_model.get_suggestions(
            self.controller.newelle_settings.prompts["get_suggestions_prompt"],
            self.offers,
            self.controller.get_history(chat=tab.chat)
        )
        GLib.idle_add(self.populate_suggestions, suggestions)

    def populate_suggestions(self, suggestions):
        """Update the UI with the generated suggestions"""
        tab = self.get_active_chat_tab()
        if tab is None:
            return
        i = 0
        # Convert to tuple to remove duplicates
        for suggestion in tuple(suggestions):
            if i + 1 > self.offers:
                break
            else:
                message = suggestion.replace("\n", "")
                if i < len(tab.chat_history.message_suggestion_buttons_array):
                    btn = tab.chat_history.message_suggestion_buttons_array[i]
                    btn.get_child().set_label(message)
                    btn.set_visible(True)
                if i < len(tab.chat_history.message_suggestion_buttons_array_placeholder):
                    btn_placeholder = tab.chat_history.message_suggestion_buttons_array_placeholder[i]
                    btn_placeholder.get_child().set_label(message)
                    btn_placeholder.set_visible(True)
                GLib.idle_add(tab.chat_history.scrolled_chat)
            i += 1
        tab.chat_history.chat_stop_button.set_visible(False)
        GLib.idle_add(tab.chat_history.scrolled_chat)

    def send_message(self, manual=True):
        """Send a message in the active chat tab - delegates to ChatTab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.send_message(manual)

    def add_reading_widget(self, documents):
        """Add reading widget to active chat tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.add_reading_widget(documents)

    def remove_reading_widget(self):
        """Remove reading widget from active chat tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.remove_reading_widget()

    # Show messages in chat
    def show_chat(self, animate=False):
        """Reload chat in the active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.show_chat()
     
    def add_prompt(self, prompt: str | None):
        """Add prompt metadata - delegates to active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.add_prompt(prompt)

    def reload_message(self, message_id: int):
        """Reload a message in the active tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.reload_message(message_id)

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
        self.chat_history.show_message(text, False, is_user=True)
    
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
        message_label = self.chat_history.get_file_button(path)
        self.chat.append({"User": "File", "Message": " " + path})
        self.chat_history.add_message("File", message_label)
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
            name = self.secondary_model.generate_chat_name(
                self.prompts["generate_name_prompt"],
                self.controller.get_history(self.chats[int(button.get_name())]["chat"])
            )
            
            def on_complete():
                if name is None:
                    button.set_icon_name("warning-outline-symbolic")
                    button.set_can_target(True)
                    button.remove_css_class("suggested-action")
                    button.add_css_class("error")
                    GLib.timeout_add(2000, self.update_history)
                else:
                    clean_name = remove_thinking_blocks(name)
                    if clean_name is None:
                        self.update_history()
                        return
                    clean_name = remove_markdown(clean_name)
                    if clean_name != "Chat has been stopped":
                        chat_idx = int(button.get_name())
                        self.chats[chat_idx]["name"] = clean_name
                        
                        # Update tab title if this chat is open in a tab
                        tab_page = self.get_tab_for_chat(chat_idx)
                        if tab_page:
                            tab_page.set_title(clean_name)
                            
                    self.update_history()

            GLib.idle_add(on_complete)
        else:
            if len(self.chats[int(button.get_name())]["chat"]) < 2:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("Chat is empty"), timeout=2)
                )
                return False
                
            spinner = Gtk.Spinner(spinning=True)
            button.set_child(spinner)
            button.set_can_target(False)
            button.set_has_frame(True)
            
            threading.Thread(
                target=self.generate_chat_name, args=[button, True]
            ).start()

    def export_chat(self, export_all=False):
        """Export chat(s) to a JSON file

        Args:
            export_all: If True, export all chats; if False, export only current chat
        """
        # Get export data
        if export_all:
            export_data = self.controller.export_all_chats()
            default_filename = f"newelle_chats_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        else:
            export_data = self.controller.export_single_chat(self.chat_id)
            if export_data is None:
                self.notification_block.add_toast(
                    Adw.Toast(title=_("Failed to export chat"), timeout=2)
                )
                return
            default_filename = f"newelle_chat_{self.chat_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        # Save to file
        dialog = Gtk.FileDialog(
            title=_("Export Chat"),
            modal=True
        )
        dialog.set_initial_name(default_filename)

        dialog.save(self, None, self._export_chat_finish, export_data)

    def _export_chat_finish(self, dialog, result, export_data):
        """Finish the export operation after file selection

        Args:
            dialog: The file dialog
            result: The async result
            export_data: The export data to save
        """
        try:
            file = dialog.save_finish(result)
        except Exception as e:
            print(f"Export failed: {e}")
            return

        if file is None:
            return

        file_path = file.get_path()
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            self.notification_block.add_toast(
                Adw.Toast(title=_("Chat exported successfully"), timeout=2)
            )
        except Exception as e:
            self.notification_block.add_toast(
                Adw.Toast(title=_("Export failed: {0}").format(str(e)), timeout=2)
            )

    def import_chat(self, button):
        """Import chat(s) from a JSON file"""
        dialog = Gtk.FileDialog(
            title=_("Import Chat"),
            modal=True
        )

        dialog.open(self, None, self._import_chat_finish)

    def _import_chat_finish(self, dialog, result):
        """Finish the import operation after file selection

        Args:
            dialog: The file dialog
            result: The async result
        """
        try:
            file = dialog.open_finish(result)
        except Exception as e:
            print(f"Import failed: {e}")
            return

        if file is None:
            return

        file_path = file.get_path()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Import the chat(s)
            success, message, count = self.controller.import_chat(data)

            if success:
                self.notification_block.add_toast(
                    Adw.Toast(title=message, timeout=3)
                )
                # Update the UI to show imported chats
                self.update_history()
                # Switch to the first imported chat if we imported at least one
                if count > 0:
                    self.chat_id = len(self.chats) - count
                    self.show_chat()
            else:
                self.notification_block.add_toast(
                    Adw.Toast(title=message, timeout=3)
                )
        except json.JSONDecodeError:
            self.notification_block.add_toast(
                Adw.Toast(title=_("Invalid JSON file"), timeout=2)
            )
        except Exception as e:
            self.notification_block.add_toast(
                Adw.Toast(title=_("Import failed: {0}").format(str(e)), timeout=2)
            )

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


    @property
    def chats(self):
        """Get the chats list from the controller"""
        return self.controller.chats

    @chats.setter
    def chats(self, value):
        """Set the chats list in the controller"""
        self.controller.chats = value

    @property
    def chat(self):
        """Get the current chat from the controller"""
        return self.controller.chat

    @chat.setter
    def chat(self, value):
        """Set the current chat in the controller"""
        self.controller.chat = value

    @property
    def chat_id(self):
        """Get the current chat ID from the controller's settings"""
        return self.controller.newelle_settings.chat_id

    @chat_id.setter
    def chat_id(self, value):
        """Set the current chat ID in the controller's settings"""
        self.controller.newelle_settings.chat_id = value

    @property
    def status(self):
        """Get the status of the active chat tab (True = ready, False = generating)"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            return tab.status
        return True

    @status.setter
    def status(self, value):
        """Set the status of the active chat tab"""
        tab = self.get_active_chat_tab()
        if tab is not None:
            tab.status = value
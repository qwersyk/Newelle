from tldextract.tldextract import update
from pylatexenc.latex2text import LatexNodes2Text
import time
import re
import sys
import os
import subprocess
import threading
import posixpath
import json
import base64
import copy
import random
import gettext

from gi.repository import Gtk, Adw, Pango, Gio, Gdk, GObject, GLib, GdkPixbuf

from .ui.settings import Settings

from .utility.message_chunk import get_message_chunks

from .ui.profile import ProfileDialog
from .ui.presentation import PresentationWindow
from .ui.widgets import File, CopyBox, BarChartBox, MarkupTextView, DocumentReaderWidget, TipsCarousel, BrowserWidget, Terminal, CodeEditorWidget
from .ui import apply_css_to_widget
from .ui.explorer import ExplorerPanel
from .ui.widgets import MultilineEntry, ProfileRow, DisplayLatex, InlineLatex, ThinkingWidget
from .constants import AVAILABLE_LLMS, SCHEMA_ID, SETTINGS_GROUPS

from .utility.system import get_spawn_command, open_website
from .utility.strings import (
    convert_think_codeblocks,
    get_edited_messages,
    markwon_to_pango,
    remove_markdown,
    remove_thinking_blocks,
    replace_codeblock,
    simple_markdown_to_pango,
    remove_emoji,
)
from .utility.replacehelper import replace_variables, ReplaceHelper
from .utility.profile_settings import get_settings_dict, get_settings_dict_by_groups, restore_settings_from_dict, restore_settings_from_dict_by_groups
from .utility.audio_recorder import AudioRecorder
from .utility.media import extract_supported_files
from .ui.screenrecorder import ScreenRecorder
from .handlers import ErrorSeverity
from .controller import NewelleController, ReloadType
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
        # Breakpoint - Collapse the sidebar when the window is too narrow
        breakpoint = Adw.Breakpoint(condition=Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 1000, Adw.LengthUnit.PX))
        breakpoint.add_setter(self.main_program_block, "collapsed", True)
        self.add_breakpoint(breakpoint)
       
        # Streams
        self.check_streams = {"folder": False, "chat": False}
        # if it is recording
        self.recording = False
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

        self.left_panel_back_button = Gtk.Button(css_classes=["flat"], visible=False)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-previous-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        self.left_panel_back_button.set_child(box)
        self.left_panel_back_button.connect("clicked", self.go_back_to_chats_panel)
        self.chat_header.pack_start(self.left_panel_back_button)
        self.chat_block.append(self.chat_header)
        self.chat_block.append(Gtk.Separator())
        self.chat_panel.append(self.chat_block)
        self.chat_panel.append(Gtk.Separator())

        # Setup main program block
        self.main = Adw.Leaflet(
            fold_threshold_policy=Adw.FoldThresholdPolicy.NATURAL,
            can_navigate_back=True,
            can_navigate_forward=True,
        )
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
        self.main.append(self.chats_main_box)
        self.main.append(self.chat_panel)
        self.main.set_visible_child(self.chat_panel)
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
        self.chat_scroll_window = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, css_classes=["background", "view"]
        )
        self.chat_scroll.set_child(self.chat_scroll_window)
        self.chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chat_scroll_window.append(self.chat_list_block)
        self.history_block = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_UP, transition_duration=500)
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
        self.main.connect("notify::folded", self.handle_main_block_change)
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
        
        # Create menu model
        menu = Gio.Menu()
        menu.append(_("Explorer Tab"), "win.new_explorer_tab")
        menu.append(_("Terminal Tab"), "win.new_terminal_tab") 
        menu.append(_("Browser Tab"), "win.new_browser_tab")
        self.new_tab_button.set_menu_model(menu)
        # Add actions
        action = Gio.SimpleAction.new("new_explorer_tab", None)
        action.connect("activate", self.add_explorer_tab)
        self.add_action(action)
        
        action = Gio.SimpleAction.new("new_terminal_tab", None)
        action.connect("activate", self.add_terminal_tab)
        self.add_action(action)
        
        action = Gio.SimpleAction.new("new_browser_tab", None)
        action.connect("activate", self.add_browser_tab)
        self.add_action(action)
        
        self.canvas_button.connect("clicked", lambda x : self.canvas_overview.set_open(not self.canvas_overview.get_open()))
        self.canvas_header.pack_end(self.canvas_button)
        self.canvas_header.pack_end(self.new_tab_button)


        self.canvas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas_box.append(self.canvas_header)
        self.canvas_box.append(self.canvas_tab_bar)
        self.canvas_box.append(self.canvas_overview)
        self.add_explorer_tab(None, self.main_path)
        self.set_content(self.main_program_block)
        self.main_program_block.set_content(self.main)
        self.main_program_block.set_sidebar(self.canvas_box)
        self.main_program_block.set_name("hide")
    
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
        model_name = AVAILABLE_LLMS[self.model.key]["title"]
        if self.model.get_setting("model") is not None:
            model_name = model_name + " - " + self.model.get_setting("model")
        self.model_menu_button.set_child(
            Gtk.Label(
                label=model_name,
                ellipsize=Pango.EllipsizeMode.MIDDLE,
            )
        )

    def build_model_popup(self):
        self.model_menu_button = Gtk.MenuButton()
        self.update_model_popup()
        self.model_popup = Gtk.Popover()
        self.model_popup.set_size_request(500, 500)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        settings = Settings(self.app, self.controller, headless=True)
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
        return self.model_menu_button

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
        for name, model in self.model.get_models_list():
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
    def go_back_to_chats_panel(self, button):
        self.main.set_visible_child(self.chats_main_box)

    def return_to_chat_panel(self, button):
        self.main.set_visible_child(self.chat_panel)
    
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
            self.chat_panel_header.set_show_end_title_buttons(self.main.get_folded())
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
                os.chdir(os.path.expanduser(self.main_path))
                GLib.idle_add(self.update_folder)
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
        if self.main.get_folded():
            self.chat_panel_header.set_show_end_title_buttons(
                not self.main_program_block.get_show_sidebar()
            )
            self.left_panel_back_button.set_visible(True)
            self.chat_header.set_show_start_title_buttons(True)
        else:
            self.chat_panel_header.set_show_end_title_buttons(False)
            self.left_panel_back_button.set_visible(False)
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
            self.remove_error(True)
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
            self.chat_list_block.remove(self.last_error_box)
            self.last_error_box = None

    def update_history(self):
        """Reload chats panel"""
        # Focus input to avoid removing a focused child
        # This avoids scroll up
        self.focus_input()

        # Update UI
        list_box = Gtk.ListBox(css_classes=["separators", "background"])
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
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
            generate_chat_name_button = Gtk.Button(
                css_classes=["flat", "accent"],
                valign=Gtk.Align.CENTER,
                icon_name="document-edit-symbolic",
                width_request=36,
            )  # wanted to use: tag-outline-symbolic
            generate_chat_name_button.connect("clicked", self.generate_chat_name)
            generate_chat_name_button.set_name(str(i))

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
            box.append(generate_chat_name_button)
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
        self.main.set_visible_child(self.chat_panel)
        if not self.status:
            self.stop_chat()
        self.stream_number_variable += 1
        self.chat_id = int(button.get_name())
        self.chat = self.chats[self.chat_id]["chat"]
        self.update_history()
        self.show_chat()
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
        self.status = True
        self.stream_number_variable += 1
        self.chat_stop_button.set_visible(False)
        GLib.idle_add(self.update_button_text)
        if len(self.chat) > 0 and (
            self.chat[-1]["User"] != "Assistant"
            or "```console" in self.chat[-1]["Message"]
        ):
            for i in range(len(self.chat) - 1, -1, -1):
                if self.chat[i]["User"] in ["Assistant", "Console"]:
                    self.chat.pop(i)
                else:
                    break
        self.notification_block.add_toast(
            Adw.Toast(
                title=_("The message was canceled and deleted from history"), timeout=2
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
        self, chat=None, include_last_message=False
    ) -> list[dict[str, str]]:
        """Format the history excluding none messages and picking the right context size

        Args:
            chat (): chat history, if None current is taken

        Returns:
           chat history
        """
        if chat is None:
            chat = self.chat
        history = []
        count = self.controller.newelle_settings.memory
        msgs = chat[:-1] if not include_last_message else chat
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
            history.append(msg)
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

    def send_message(self, manual=True):
        """Send a message in the chat and get bot answer, handle TTS etc"""
        GLib.idle_add(self.hide_placeholder)
        if manual:
            self.auto_run_times = 0
        self.stream_number_variable += 1
        stream_number_variable = self.stream_number_variable
        self.status = False
        GLib.idle_add(self.update_button_text)

        # Append extensions prompts
        prompts = []
        for prompt in self.controller.newelle_settings.bot_prompts:
            prompts.append(replace_variables(prompt))

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
        if len(self.chat) == 0:
            GLib.idle_add(self.remove_send_button_spinner)
            GLib.idle_add(self.show_chat)
            return
        if self.chat[-1]["Message"] != old_user_prompt:
            self.reload_message(len(self.chat) - 1)

        self.model.set_history(prompts, history)
        try:
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
        except Exception as e:
            # Show error messsage
            GLib.idle_add(self.show_message, str(e), False, -1, False, False, True)
            GLib.idle_add(self.remove_send_button_spinner)

            def remove_streaming_box():
                if self.model.stream_enabled() and hasattr(self, "streaming_box"):
                    self.streaming_box.unparent()

            GLib.timeout_add(250, remove_streaming_box)
            return
        if self.stream_number_variable == stream_number_variable:
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
        if hasattr(self, "reading"):
            self.streaming_box.remove(self.reading)

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
        # Create a textview for the message that will be streamed
        self.streaming_label = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, editable=False, hexpand=True
        )
        # Remove background color from window and textview
        scrolled_window.add_css_class("scroll")
        self.streaming_label.add_css_class("scroll")
        apply_css_to_widget(
            scrolled_window, ".scroll { background-color: rgba(0,0,0,0);}"
        )
        apply_css_to_widget(
            self.streaming_label, ".scroll { background-color: rgba(0,0,0,0);}"
        )
        # Add the textview to the scrolledwindow
        scrolled_window.set_child(self.streaming_label)
        # Remove background from the text buffer
        text_buffer = self.streaming_label.get_buffer()
        tag = text_buffer.create_tag(
            "no-background", background_set=False, paragraph_background_set=False
        )
        if tag is not None:
            text_buffer.apply_tag(
                tag, text_buffer.get_start_iter(), text_buffer.get_end_iter()
            )
        # Create the message label
        self.streaming_message_box.append(scrolled_window)
        self.streaming_box = self.add_message("Assistant", self.streaming_message_box)
        self.streaming_box.set_overflow(Gtk.Overflow.VISIBLE)

    def update_message(self, message, stream_number_variable):
        """Update message label when streaming

        Args:
            message (): new message text
            stream_number_variable (): stream number, avoid conflicting streams
        """
        if self.stream_number_variable != stream_number_variable:
            return
        self.streamed_message = message
        if self.streamed_message.startswith("<think>") and not self.stream_thinking:
            self.stream_thinking = True
            text = self.streamed_message.split("</think>")
            thinking = text[0].replace("<think>", "")
            message = text[1] if len(text) > 1 else ""
            self.streaming_thought = thinking
            def idle():
                self.thinking_box = ThinkingWidget() 
                self.streaming_message_box.prepend(self.thinking_box)
                self.thinking_box.start_thinking(thinking)
            GLib.idle_add(idle)
        elif self.stream_thinking:

            t = time.time()
            if t - self.last_update < 0.05:
                return
            self.last_update = t
            text = self.streamed_message.split("</think>")
            thinking = text[0].replace("<think>", "")
            message = text[1] if len(text) > 1 else ""
            added_thinking = message[len(self.streaming_thought) :]
            self.thinking_box.append_thinking(added_thinking)
        if self.streaming_label is not None:
            # Find the differences between the messages
            added_message = message[len(self.curr_label) :]
            t = time.time()
            if t - self.last_update < 0.05:
                return
            self.last_update = t
            self.curr_label = message

            # Edit the label on the main thread
            def idle_edit():
                self.streaming_label.get_buffer().insert(
                    self.streaming_label.get_buffer().get_end_iter(), added_message
                )
                pl = self.streaming_label.create_pango_layout(self.curr_label)
                width, height = pl.get_size()
                width = (
                    Gtk.Widget.get_scale_factor(self.streaming_label)
                    * width
                    / Pango.SCALE
                )
                height = (
                    Gtk.Widget.get_scale_factor(self.streaming_label)
                    * height
                    / Pango.SCALE
                )
                wmax = self.chat_list_block.get_size(Gtk.Orientation.HORIZONTAL)
                # Dynamically take the width of the label
                self.streaming_label.set_size_request(int(min(width, wmax - 150)), -1)

            GLib.idle_add(idle_edit)

    # Show messages in chat
    def show_chat(self):
        """Show a chat"""
        self.messages_box = []
        self.last_error_box = None
        if not self.check_streams["chat"]:
            self.check_streams["chat"] = True
            try:
                self.chat_scroll_window.remove(self.chat_list_block)
                self.chat_list_block = Gtk.ListBox(
                    css_classes=["separators", "background", "view"]
                )
                self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)

                self.chat_scroll_window.append(self.chat_list_block)
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

    def add_prompt(self, prompt: str | None):
        if prompt is None:
            return
        self.chat[-1]["Prompt"] = prompt

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
        """Show a message

        Args:
            message_label (): text of the message
            restore (): if the chat is being restored
            id_message (): id of the message
            is_user (): true if it's a user message
            return_widget (): if the widget should be returned and not added
            newelle_error (): if the message is an error from Newelle

        Returns:
            Gtk.Widget | None
        """
        codeblock_id = -1
        if id_message == -1:
            id_message = len(self.chat) 
        editable = True
        if message_label == " " * len(message_label) and not is_user:
            if not restore:
                self.chat.append({"User": "Assistant", "Message": message_label})
                self.add_prompt(prompt)
                GLib.idle_add(self.update_button_text)
                self.status = True
                self.chat_stop_button.set_visible(False)
        elif newelle_error:
            if not restore:
                self.chat_stop_button.set_visible(False)
                GLib.idle_add(self.update_button_text)
                self.status = True
            message_label = markwon_to_pango(message_label)
            self.last_error_box = self.add_message(
                "Error",
                Gtk.Label(
                    label=message_label,
                    use_markup=True,
                    wrap=True,
                    margin_top=10,
                    margin_end=10,
                    margin_bottom=10,
                    margin_start=10,
                ),
            )
        else:
            if not restore and not is_user:
                self.chat.append({"User": "Assistant", "Message": message_label})
                self.add_prompt(prompt)
            chunks = get_message_chunks(
                message_label, self.controller.newelle_settings.display_latex
            )
            box = Gtk.Box(
                margin_top=10,
                margin_start=10,
                margin_bottom=10,
                margin_end=10,
                orientation=Gtk.Orientation.VERTICAL,
            )
            code_language = ""
            has_terminal_command = False
            running_threads = []
            for chunk in chunks:
                if chunk.type == "codeblock":
                    codeblock_id += 1
                    code_language = chunk.lang
                    # Join extensions and integrations codeblocks
                    codeblocks = {**self.extensionloader.codeblocks, **self.controller.integrationsloader.codeblocks}
                    if code_language in codeblocks:
                        value = chunk.text
                        extension = codeblocks[code_language]

                        try:
                            # Check if the extension widget is available
                            if restore:
                                widget = extension.restore_gtk_widget(value, code_language)
                            else:
                                widget = extension.get_gtk_widget(value, code_language)
                            if widget is not None:
                                # Add the widget to the message
                                box.append(widget)
                            if widget is None or extension.provides_both_widget_and_anser(value, code_language):
                                if widget is not None:
                                    # If the answer is provided, the apply_async function 
                                    # Should only do something on error\
                                    # The widget must be edited by the extension
                                    def apply_sync(code):    
                                        if not code[0]:
                                            self.add_message("Error", code[1])
                                else:
                                    # In case only the answer is provided, the apply_async function
                                    # Also return a text expander with the code
                                    text_expander = Gtk.Expander(
                                        label=code_language,
                                        css_classes=["toolbar", "osd"],
                                        margin_top=10,
                                        margin_start=10,
                                        margin_bottom=10,
                                        margin_end=10,
                                    )
                                    text_expander.set_expanded(False)
                                    box.append(text_expander)
                                    def apply_sync(code):
                                        text_expander.set_child(
                                            Gtk.Label(
                                                wrap=True,
                                                wrap_mode=Pango.WrapMode.WORD_CHAR,
                                                label=chunk.text + "\n" + str(code[1]),
                                                selectable=True,
                                            )
                                        ) 
                                # Add message to history
                                editable = False
                                if id_message == -1:
                                    id_message = len(self.chat) - 1
                                id_message += 1
                                has_terminal_command = True
                                reply_from_the_console = None
                                if (
                                    self.chat[min(id_message, len(self.chat) - 1)][
                                        "User"
                                    ]
                                    == "Console"
                                ):
                                    reply_from_the_console = self.chat[
                                        min(id_message, len(self.chat) - 1)
                                    ]["Message"]
                                
                                # Get the response async
                                def get_response(apply_sync):
                                    if not restore:
                                        response = extension.get_answer(
                                            value, code_language
                                        )
                                        if response is not None:
                                            code = (True, response)
                                        else:
                                            code = (False, "Error:")
                                    else:
                                        code = (True, reply_from_the_console)
                                    self.chat.append(
                                        {
                                            "User": "Console",
                                            "Message": " " + str(code[1]),
                                        }
                                    )
                                    GLib.idle_add(apply_sync, code)

                                t = threading.Thread(target=get_response, args=(apply_sync,))
                                t.start()
                                running_threads.append(t)
                        except Exception as e:
                            print("Extension error " + extension.id + ": " + str(e))
                            box.append(CopyBox(chunk.text, code_language, parent=self, id_message=id_message, id_codeblock=codeblock_id, allow_edit=editable))
                    elif code_language == "think":
                        think = ThinkingWidget()
                        think.set_thinking(chunk.text)
                        box.append(
                            think
                        )
                    elif code_language == "image":
                        for i in chunk.text.split("\n"):
                            if i.startswith("data:image/jpeg;base64,"):
                                data = i[len("data:image/jpeg;base64,") :]
                                raw_data = base64.b64decode(data)
                                loader = GdkPixbuf.PixbufLoader()
                                loader.write(raw_data)
                                loader.close()
                                image = Gtk.Image(css_classes=["image"])
                                image.set_from_pixbuf(loader.get_pixbuf())
                                box.append(image)
                            else:
                                image = Gtk.Image(css_classes=["image"])
                                image.set_from_file(i)
                                box.append(image)
                    elif code_language == "video":
                        for i in chunk.text.split("\n"):
                            video = Gtk.Video(
                                css_classes=["video"], vexpand=True, hexpand=True
                            )
                            video.set_size_request(-1, 400)
                            video.set_file(Gio.File.new_for_path(i))
                            box.append(video)
                    elif code_language == "console" and not is_user:
                        editable = False
                        if id_message == -1:
                            id_message = len(self.chat) - 1
                        id_message += 1
                        if (
                            self.controller.newelle_settings.auto_run
                            and not any(
                                command in chunk.text
                                for command in ["rm ", "apt ", "sudo ", "yum ", "mkfs "]
                            )
                            and self.auto_run_times
                            < self.controller.newelle_settings.max_run_times
                        ):
                            has_terminal_command = True
                            value = chunk.text
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
                            path = ""
                            reply_from_the_console = None
                            if (
                                self.chat[min(id_message, len(self.chat) - 1)]["User"]
                                == "Console"
                            ):
                                reply_from_the_console = self.chat[
                                    min(id_message, len(self.chat) - 1)
                                ]["Message"]

                            def getresponse(path):
                                if not restore:
                                    path = os.path.normpath(self.main_path)
                                    code = self.execute_terminal_command(value)
                                else:
                                    code = (True, reply_from_the_console)
                                text = f"[User {path}]:$ {value}\n{code[1]}"
                                if not restore:
                                    self.chat.append(
                                        {
                                            "User": "Console",
                                            "Message": " " + str(code[1]),
                                        }
                                    )

                                def apply_sync():
                                    text_expander.set_child(
                                        Gtk.Label(
                                            wrap=True,
                                            wrap_mode=Pango.WrapMode.WORD_CHAR,
                                            label=text,
                                            selectable=True,
                                        )
                                    )
                                    if not code[0]:
                                        self.add_message("Error", text_expander)

                                GLib.idle_add(apply_sync)

                            t = threading.Thread(target=getresponse, args=(path,))
                            t.start()
                            running_threads.append(t)
                            if not restore:
                                self.auto_run_times += 1
                        else:
                            if not restore:
                                self.chat.append({"User": "Console", "Message": "None"})
                            box.append(
                                CopyBox(chunk.text, code_language, self, id_message, id_codeblock=codeblock_id, allow_edit=editable)
                            )
                        result = {}
                    elif code_language in ["file", "folder"]:
                        for obj in chunk.text.split("\n"):
                            box.append(self.get_file_button(obj))
                    elif code_language == "chart" and not is_user:
                        result = {}
                        lines = chunk.text.split("\n")
                        percentages = ""
                        for line in lines:
                            parts = line.split("-")
                            if len(parts) == 2:
                                key = parts[0].strip()
                                percentages = "%" in parts[1]
                                value = "".join(
                                    filter(lambda x: x.isdigit() or x == ".", parts[1])
                                )
                                try:
                                    result[key] = float(value)
                                except Exception as e:
                                    result[key] = 0
                            else:
                                box.append(
                                    CopyBox(chunk.text, code_language, parent=self)
                                )
                                result = {}
                                break
                        if result != {}:
                            box.append(BarChartBox(result, percentages))
                    elif code_language == "latex":
                        try:
                            box.append(
                                DisplayLatex(chunk.text, 16, self.controller.cache_dir)
                            )
                        except Exception as e:
                            print(e)
                            box.append(CopyBox(chunk.text, code_language, parent=self))
                    else:
                        box.append(CopyBox(chunk.text, code_language, parent=self, id_message=id_message, id_codeblock=codeblock_id, allow_edit=editable))
                elif chunk.type == "table":
                    try:
                        box.append(self.create_table(chunk.text.split("\n")))
                    except Exception as e:
                        print(e)
                        box.append(CopyBox(chunk.text, "table", parent=self))
                elif chunk.type == "inline_chunks":
                    if chunk.subchunks is None:
                        continue
                    # Create a label to guess the size of the chunk
                    overlay = Gtk.Overlay()
                    label = Gtk.Label(label=" ".join(ch.text for ch in chunk.subchunks), wrap=True)
                    label.set_opacity(0)
                    overlay.set_child(label)
                    # Create the textview
                    textview = MarkupTextView()
                    textview.set_valign(Gtk.Align.START)
                    textview.set_hexpand(True)
                    overlay.add_overlay(textview)
                    overlay.set_measure_overlay(textview, True)
                    buffer = textview.get_buffer()
                    iter = buffer.get_start_iter()
                    txt = ""
                    for chunk in chunk.subchunks: 
                        if chunk.type == "text":
                            textview.add_markup_text(iter, markwon_to_pango(chunk.text))
                            txt += chunk.text 
                        elif chunk.type == "latex_inline":
                            txt += chunk.text
                            try:
                                # Create the anchor for the widget
                                anchor = buffer.create_child_anchor(iter)
                                # Calculate the current font size according to the current zoom
                                font_size = 5 + ((self.controller.newelle_settings.zoom)/100 * 4)
                                # Create the LaTeX widget
                                latex = InlineLatex(chunk.text, int(font_size))
                                # Embed the Widget in an overlay in order to avoid disalignment
                                overlay1 = Gtk.Overlay()
                                overlay1.add_overlay(latex)
                                box2 = Gtk.Box()
                                box2.set_size_request(latex.picture.dims[0], latex.picture.dims[1] + 1)
                                overlay1.set_child(box2)
                                latex.set_margin_top(5)
                                textview.add_child_at_anchor(overlay1, anchor)
                            except Exception as e:
                                buffer.insert(iter, LatexNodes2Text().latex_to_text(chunk.text))
                    box.append(overlay)
                elif chunk.type == "latex" or chunk.type == "latex_inline":
                    try:
                        box.append(
                            DisplayLatex(chunk.text, 16, self.controller.cache_dir)
                        )
                    except Exception:
                        box.append(CopyBox(chunk.text, "latex", parent=self))
                elif chunk.type == "thinking":
                    think = ThinkingWidget()
                    think.set_thinking(chunk.text)
                    box.append(
                        think
                    )
                elif chunk.type == "text":
                    if chunk.text == ".":
                        continue
                    label = markwon_to_pango(chunk.text)
                    box.append(
                        Gtk.Label(
                            label=label,
                            wrap=True,
                            halign=Gtk.Align.START,
                            wrap_mode=Pango.WrapMode.WORD_CHAR,
                            width_chars=1,
                            selectable=True,
                            use_markup=True,
                        )
                    )
            if not has_terminal_command:
                if not return_widget:
                    self.add_message(
                        "Assistant" if not is_user else "User",
                        box,
                        id_message,
                        editable,
                    )
                else:
                    return box
                if not restore:
                    GLib.idle_add(self.update_button_text)
                    self.status = True
                    self.chat_stop_button.set_visible(False)
                    self.chats[self.chat_id]["chat"] = self.chat
            else:
                if not return_widget:
                    self.add_message("Assistant", box, id_message, editable)
                else:
                    return box
                if not restore and not is_user:

                    def wait_threads_sm():
                        for t in running_threads:
                            t.join()
                        if len(running_threads) > 0:
                            self.send_message(manual=False)

                    self.chats[self.chat_id]["chat"] = self.chat
                    threading.Thread(target=wait_threads_sm).start()
        GLib.idle_add(self.scrolled_chat)
        self.save_chat()

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
                    r.append(simple_markdown_to_pango(element))
                model.append(r)
        self.treeview = Gtk.TreeView(
            model=model, css_classes=["toolbar", "view", "transparent"]
        )

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, markup=i)
            self.treeview.append_column(column)
        return self.treeview

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
        if len(self.messages_box) <= message_id:
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
        del self.chat[int(gesture.get_name())]
        self.chat_list_block.remove(box.get_parent())
        self.messages_box.remove(box)
        self.save_chat()
        self.show_chat()

    def show_prompt(self, button, id):
        """Show a prompt

        Args:
            id (): id of the prompt to show
        """
        dialog = Adw.Dialog(can_close=True)
        dialog.set_title(_("Prompt content"))
        label = Gtk.Label(
            label=self.chat[id]["Prompt"],
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD,
            selectable=True,
            halign=Gtk.Align.START,
        )
        scroll = Gtk.ScrolledWindow(propagate_natural_width=True, height_request=600)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(label)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(
            Adw.HeaderBar(css_classes=["flat"], show_start_title_buttons=True)
        )
        content.append(scroll)
        dialog.set_child(content)
        dialog.set_content_width(400)
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
        if command is None:
            command = ""
        cmd = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;" + command + "; exec bash"]
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
            url = "https://duckduckgo.com"
        browser = BrowserWidget(url)
        
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
        self.show_message(text, False, is_user=True)
    
    def add_explorer_tab(self, tabview=None, path=None):
        """Add an explorer tab

        Args:
            path (): path of the tab
        """
        if path is None:
            path = self.main_path
        if not os.path.isdir(os.path.expanduser(path)):
            return self.add_editor_tab(tab, path)
        panel = ExplorerPanel(self.controller, path)
        tab = self.canvas_tabs.append(panel)
        panel.set_tab(tab)
        panel.connect("new-tab-requested", self.add_explorer_tab)
        panel.connect("path-changed", self.update_path)
        self.show_sidebar()
        self.canvas_tabs.set_selected_page(tab)
        return tab

    def update_path(self, panel, path):
        self.main_path = path

    def add_editor_tab(self, tabview=None, file=None):
        if file is not None:
            base_title = os.path.basename(file)
            editor = CodeEditorWidget()
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
        editor = CodeEditorWidget()
        editor.load_from_string(content, lang)
        tab = self.canvas_tabs.append(editor)
        base_title = "Message " + str(id_message) + " " + str(id_codeblock)
        tab.set_title(base_title)
        editor.connect("edit_state_changed", self._on_editor_modified, tab, base_title)
        editor.connect("content-saved", lambda editor, _: self.edit_copybox(id_message, id_codeblock, editor.get_text(), editor))
        self.canvas_tabs.set_selected_page(tab)
        self.show_sidebar()

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

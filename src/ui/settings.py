from typing import Any
import threading 
import os 
import shutil
import json
import time
import traceback
import weakref
import re
from subprocess import Popen 

from gi.repository import Gtk, Adw, Gio, GLib, GObject, Gdk, GtkSource

from ..utility.util import PerformanceMonitor

from ..handlers import Handler

from ..constants import AVAILABLE_EMBEDDINGS, AVAILABLE_LLMS, AVAILABLE_MEMORIES, AVAILABLE_PROMPTS, AVAILABLE_TTS, AVAILABLE_STT, PROMPTS, AVAILABLE_RAGS, AVAILABLE_WEBSEARCH, AVAILABLE_IMAGE_GENERATORS
from ..utility.pip import install_module
from ..utility.download_manager import get_download_manager
from .extension import ExtensionPage
from .interfaces import InterfacesPage
from .extra_settings import ExtraSettingsBuilder
from .widgets import ComboRowHelper, CopyBox 
from .widgets import MultilineEntry
from ..utility.system import can_escape_sandbox, get_spawn_command, open_website, open_folder, is_flatpak 

from ..controller import NewelleController
from ..modes import DEFAULT_MODE_NAME

class Settings(Adw.Window):
    def __init__(self,app, controller: NewelleController,headless=False, startup_page=None, popup=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = app
        self.controller = controller
        self.settings = controller.settings
        self.headless = headless
        self.popup = popup
        if not headless:
            self.set_transient_for(app.win)
        self.set_title(_("Settings"))
        self.set_default_size(950, 720)
        self.set_modal(True)
        self.set_resizable(False)
        self.downloading = {}
        self.slider_labels = {}
        self.directory = GLib.get_user_config_dir()
        # Load extensions 
        self.extensionloader = controller.extensionloader
        self.model_threads = {}
        self._pending_download_button_rows = []
        self._download_button_queue_scheduled = False
        # Load custom prompts
        self.custom_prompts = self.controller.newelle_settings.custom_prompts 
        self.prompts_settings = self.controller.newelle_settings.prompts_settings
        self.prompts = self.controller.newelle_settings.prompts
        self.sandbox = can_escape_sandbox()
       
        self.handlers = self.controller.handlers
        # Page building
        self.general_page = Adw.PreferencesPage(icon_name="settings-symbolic", title=_("General"))
        self.LLMPage = Adw.PreferencesPage(icon_name="brain-augemnted-symbolic", title=_("LLM")) 
        self.PromptsPage = Adw.PreferencesPage(icon_name="question-round-outline-symbolic", title=_("Prompts"))
        self.ToolsPage = Adw.PreferencesPage(icon_name="tools-symbolic", title=_("Tools"))
        self.PermissionsPage = Adw.PreferencesPage(
            icon_name="key-symbolic",
            title=_("Permissions"),
        )
        self.MemoryPage = Adw.PreferencesPage(icon_name="vcard-symbolic", title=_("Knowledge"))
        self.VoicePage = Adw.PreferencesPage(icon_name="audio-input-microphone-symbolic", title=_("Voice"))
        self.SkillsPage = Adw.PreferencesPage(icon_name="skills-symbolic", title=_("Skills"))
        self.MCPPage = Adw.PreferencesPage(icon_name="internet-symbolic", title=_("MCP Servers"))
        # Dictionary containing all the rows for settings update
        self.settingsrows = {}
        self.extra_settings_builder = ExtraSettingsBuilder(
            settingsrows=self.settingsrows,
            convert_constants=self.convert_constants,
            on_before_rebuild=self._on_extra_settings_rebuild,
        )
        self._llm_primary_rows = []
        self._llm_primary_other_rows = []
        self._llm_secondary_rows = []
        self._llm_secondary_other_rows = []
        # Build the LLMs settings
        self.LLM = Adw.PreferencesGroup(title=_('Language Model'))
        # Add duplication and help buttons.
        llm_header_actions = Gtk.Box(spacing=3)
        duplicate_llm = Gtk.Button(
            css_classes=["flat"],
            icon_name="list-add-symbolic",
            tooltip_text=_("Add a custom LLM provider"),
        )
        duplicate_llm.connect("clicked", self.on_duplicate_llm)
        llm_header_actions.append(duplicate_llm)
        help = Gtk.Button(css_classes=["flat"], icon_name="info-outline-symbolic")
        help.connect("clicked", lambda button : Popen(get_spawn_command() + ["xdg-open", "https://github.com/qwersyk/Newelle/wiki/User-guide-to-the-available-LLMs"]))
        llm_header_actions.append(help)
        self.LLM.set_header_suffix(llm_header_actions)
        # Add LLMs
        self.LLMPage.add(self.LLM)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("language-model")
        others_row = Adw.ExpanderRow(title=_('Other LLMs'), subtitle=_("Other available LLM providers"))
        self._llm_primary_other_group = others_row
        for model_key in AVAILABLE_LLMS:
           row = self.build_row(AVAILABLE_LLMS, model_key, selected, group)
           if "secondary" in AVAILABLE_LLMS[model_key] and AVAILABLE_LLMS[model_key]["secondary"]:
               others_row.add_row(row)
               self._llm_primary_other_rows.append(row)
           else:
                self.LLM.add(row)
                self._llm_primary_rows.append(row)
        self.LLM.add(others_row)
        # Secondary LLM settings
        self.SECONDARY_LLM = Adw.PreferencesGroup(title=_('Secondary LLM'))
        self.LLMPage.add(self.SECONDARY_LLM)

        secondary_LLM_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("secondary-llm-on", secondary_LLM_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        secondary_LLM = Adw.ExpanderRow(title=_('Secondary Language Model'), subtitle=_("Model used for secondary tasks, like offer, chat name and memory generation"))
        secondary_LLM.add_action(secondary_LLM_enabled)
        # Add the secondary model selector as its own expander.
        # Add LLMs
        group = Gtk.CheckButton()
        selected = self.settings.get_string("secondary-language-model")
        others_row = Adw.ExpanderRow(title=_('Other LLMs'), subtitle=_("Other available LLM providers"))
        self._llm_secondary_model_group = secondary_LLM
        self._llm_secondary_other_group = others_row
        for model_key in AVAILABLE_LLMS:
           row = self.build_row(AVAILABLE_LLMS, model_key, selected, group, True)
           if "secondary" in AVAILABLE_LLMS[model_key] and AVAILABLE_LLMS[model_key]["secondary"]:
               others_row.add_row(row)
               self._llm_secondary_other_rows.append(row)
           else:
               secondary_LLM.add_row(row)
               self._llm_secondary_rows.append(row)
        secondary_LLM.add_row(others_row)
        self.SECONDARY_LLM.add(secondary_LLM)

        # Vision routing is a separate action row in the same group.
        vision_row = Adw.ActionRow(
            title=_("Use secondary LLM for vision"),
            subtitle=_("Use the secondary model for chats containing images or videos"),
        )
        vision_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        vision_row.add_suffix(vision_switch)
        self.settings.bind("secondary-llm-vision", vision_switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.SECONDARY_LLM.add(vision_row)

        self.KNOWLEDGE = Adw.PreferencesGroup(title=_('Advanced LLM Settings'))
        self.MemoryPage.add(self.KNOWLEDGE)
        
        # Build the Embedding settings
        embedding_row = Adw.ExpanderRow(title=_('Embedding Model'), subtitle=_("Embedding is used to trasform text into vectors. Used by Long Term Memory and RAG. Changing it might require you to re-index documents or reset memory."))
        self.KNOWLEDGE.add(embedding_row)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("embedding-model")
        for key in AVAILABLE_EMBEDDINGS:
           row = self.build_row(AVAILABLE_EMBEDDINGS, key, selected, group) 
           embedding_row.add_row(row)
        
        # Build the Long Term Memory settings
        memory_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("memory-on", memory_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        tts_program = Adw.ExpanderRow(title=_('Long Term Memory'), subtitle=_("Keep memory of old conversations"))
        tts_program.add_action(memory_enabled)
        self.KNOWLEDGE.add(tts_program)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("memory-model")
        for key in AVAILABLE_MEMORIES:
           row = self.build_row(AVAILABLE_MEMORIES, key, selected, group) 
           tts_program.add_row(row)
        
        # Build the Web Search settings
        web_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("websearch-on", web_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        tts_program = Adw.ExpanderRow(title=_('Web Search'), subtitle=_("Search information on the Web"))
        tts_program.add_action(web_enabled)
        self.KNOWLEDGE.add(tts_program)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("websearch-model")
        for key in AVAILABLE_WEBSEARCH:
           row = self.build_row(AVAILABLE_WEBSEARCH, key, selected, group) 
           tts_program.add_row(row)
        # Build the Image Generator settings
        image_generator_row = Adw.ExpanderRow(title=_('Image Generator'), subtitle=_("Choose which image generation engine to use"))
        self.KNOWLEDGE.add(image_generator_row)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("image-generator")
        for key in AVAILABLE_IMAGE_GENERATORS:
           row = self.build_row(AVAILABLE_IMAGE_GENERATORS, key, selected, group)
           image_generator_row.add_row(row)
        # Build the RAG settings
        self.build_rag_settings()

        # Build the TTS settings
        self.Voicegroup = Adw.PreferencesGroup(title=_('Voice'))
        self.VoicePage.add(self.Voicegroup)
        tts_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("tts-on", tts_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        tts_program = Adw.ExpanderRow(title=_('Text To Speech Program'), subtitle=_("Choose which text to speech to use"))
        tts_program.add_action(tts_enabled)
        self.Voicegroup.add(tts_program)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("tts")
        for tts_key in AVAILABLE_TTS:
           row = self.build_row(AVAILABLE_TTS, tts_key, selected, group) 
           tts_program.add_row(row)
        # Build the Speech to Text settings
        stt_engine = Adw.ExpanderRow(title=_('Speech To Text Engine'), subtitle=_("Choose which speech recognition engine you want"))
        self.Voicegroup.add(stt_engine)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("stt-engine")
        for stt_key in AVAILABLE_STT:
            if AVAILABLE_STT[stt_key].get("primary", True):
                row = self.build_row(AVAILABLE_STT, stt_key, selected, group)
                stt_engine.add_row(row)

        # Automatic STT settings
        self.auto_stt = Adw.ExpanderRow(title=_('Automatic Speech To Text'), subtitle=_("Automatically restart speech to text at the end of a text/TTS"))
        self.build_auto_stt()
        self.Voicegroup.add(self.auto_stt)
        # Wakeword Detection
        self.wakeword_row = Adw.ExpanderRow(
            title=_('Wakeword Detection'),
            subtitle=_("Detect wakeword to send voice commands")
        )
        wakeword_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("wakeword-on", wakeword_enabled, 'active',
                           Gio.SettingsBindFlags.DEFAULT)
        self.wakeword_row.add_action(wakeword_enabled)

        # Wakeword mode toggle group
        mode_row = Adw.ActionRow(title=_('Detection Method'), subtitle=_("Choose wakeword detection method"))
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        
        current_mode = self.settings.get_string("wakeword-mode")
        self.wakeword_mode_secondary = Gtk.ToggleButton(label=_("Secondary STT"), active=(current_mode == "secondary-stt"))
        self.wakeword_mode_secondary.add_css_class("flat")
        self.wakeword_mode_wakeword = Gtk.ToggleButton(label=_("Wakeword Model"), group=self.wakeword_mode_secondary, active=(current_mode == "openwakeword"))
        self.wakeword_mode_wakeword.add_css_class("flat")
        
        mode_box.append(self.wakeword_mode_secondary)
        mode_box.append(self.wakeword_mode_wakeword)
        mode_row.add_suffix(mode_box)
        self.wakeword_row.add_row(mode_row)

        # Secondary STT mode rows (visible when secondary-stt mode is selected)
        self.secondary_stt_rows = []
        
        # Secondary STT engine selection
        secondary_stt_engine = Adw.ExpanderRow(
            title=_('Secondary STT Engine'),
            subtitle=_("Fast STT for quick wakeword detection")
        )
        group = Gtk.CheckButton()
        selected = self.settings.get_string("secondary-stt-engine")
        for stt_key in AVAILABLE_STT:
            if "secondary" in AVAILABLE_STT[stt_key] and AVAILABLE_STT[stt_key]["secondary"]:
                row = self.build_row(AVAILABLE_STT, stt_key, selected, group, True)
                secondary_stt_engine.add_row(row)
        self.wakeword_row.add_row(secondary_stt_engine)
        self.secondary_stt_rows.append(secondary_stt_engine)

        # Wakeword text entry (for secondary STT mode)
        wakeword_entry = Adw.EntryRow(title=_('Wakeword'))
        wakeword_entry.set_tooltip_text(_("Word or phrase to detect (multiple separated by comma)"))
        self.settings.bind("wakeword", wakeword_entry, 'text',
                           Gio.SettingsBindFlags.DEFAULT)
        self.wakeword_row.add_row(wakeword_entry)
        self.secondary_stt_rows.append(wakeword_entry)

        # Wakeword engine mode rows (visible when openwakeword mode is selected)
        self.wakeword_engine_rows = []
        
        # Wakeword engine selection (handlers with "wakeword": True)
        wakeword_engine = Adw.ExpanderRow(
            title=_('Wakeword Engine'),
            subtitle=_("Model specialized for wakeword detection")
        )
        group = Gtk.CheckButton()
        selected = self.settings.get_string("wakeword-engine")
        for stt_key in AVAILABLE_STT:
            if AVAILABLE_STT[stt_key].get("wakeword", False):
                row = self.build_row(AVAILABLE_STT, stt_key, selected, group, True)
                wakeword_engine.add_row(row)
        self.wakeword_row.add_row(wakeword_engine)
        self.wakeword_engine_rows.append(wakeword_engine)

        # Pre-buffer duration
        pre_buffer_adj = Gtk.Adjustment(
            lower=0.1,
            upper=2.0,
            step_increment=0.1,
            page_increment=0.5
        )
        pre_buffer_adj.set_value(self.settings.get_double("wakeword-pre-buffer-duration"))
        pre_buffer_row = Adw.SpinRow(
            title=_('Pre-buffer Duration'),
            subtitle=_("Seconds of audio to capture before speech"),
            adjustment=pre_buffer_adj,
            digits=1
        )
        def update_pre_buffer(spin, input):
            self.settings.set_double("wakeword-pre-buffer-duration", spin.get_value())
            return False
        pre_buffer_row.connect("input", update_pre_buffer)
        self.wakeword_row.add_row(pre_buffer_row)

        # Silence duration
        silence_adj = Gtk.Adjustment(
            lower=0.1,
            upper=5.0,
            step_increment=0.05,
            page_increment=0.5
        )
        silence_adj.set_value(self.settings.get_double("wakeword-silence-duration"))
        silence_row = Adw.SpinRow(
            title=_('Silence Timeout'),
            subtitle=_("Seconds of silence to end speech segment"),
            adjustment=silence_adj,
            digits=2
        )
        def update_silence(spin, input):
            self.settings.set_double("wakeword-silence-duration", spin.get_value())
            return False
        silence_row.connect("input", update_silence)
        self.wakeword_row.add_row(silence_row)

        # Energy threshold
        energy_adj = Gtk.Adjustment(
            lower=0,
            upper=1000,
            step_increment=50,
            page_increment=100
        )
        energy_adj.set_value(self.settings.get_int("wakeword-energy-threshold"))
        energy_row = Adw.SpinRow(
            title=_('Noise Threshold'),
            subtitle=_("Audio energy level to ignore (higher = less sensitive, 0-1000)"),
            adjustment=energy_adj,
            digits=0
        )
        def update_energy(spin, input):
            self.settings.set_int("wakeword-energy-threshold", int(spin.get_value()))
            return False
        energy_row.connect("input", update_energy)
        self.wakeword_row.add_row(energy_row)

        # Toggle visibility based on mode
        def on_wakeword_mode_changed(btn):
            is_wakeword = self.wakeword_mode_wakeword.get_active()
            mode = "openwakeword" if is_wakeword else "secondary-stt"
            self.settings.set_string("wakeword-mode", mode)
            for row in self.secondary_stt_rows:
                row.set_visible(not is_wakeword)
            for row in self.wakeword_engine_rows:
                row.set_visible(is_wakeword)
        
        self.wakeword_mode_secondary.connect("toggled", on_wakeword_mode_changed)
        self.wakeword_mode_wakeword.connect("toggled", on_wakeword_mode_changed)
        
        # Set initial visibility
        is_wakeword_mode = current_mode == "openwakeword"
        for row in self.secondary_stt_rows:
            row.set_visible(not is_wakeword_mode)
        for row in self.wakeword_engine_rows:
            row.set_visible(is_wakeword_mode)

        self.Voicegroup.add(self.wakeword_row)
        # Build prompts settings 
        self.prompt = Adw.PreferencesGroup(title=_('Prompt control'))
        add_prompt_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_prompt_btn.add_css_class("flat")
        add_prompt_btn.set_tooltip_text(_("Add custom prompt"))
        add_prompt_btn.connect("clicked", self.on_add_custom_prompt)
        self.prompt.set_header_suffix(add_prompt_btn)
        self.PromptsPage.add(self.prompt)
        self.prompts_rows = []
        self.build_prompts_settings()
        # Build tools settings lazily (first time Tools page is opened)
        self.tools_page_initialized = False
        self._building_tools_page = False
        self.permissions_page_initialized = False
        self._building_permissions_page = False
        self.skills_page_initialized = False
        self.mcp_page_initialized = False
        self.tool_rows = []
        # Interface settings
        self.interface = Adw.PreferencesGroup(title=_('Interface'))
        self.general_page.add(self.interface)

        row = Adw.ActionRow(title=_("Interface Size"), subtitle=_("Adjust the size of the interface"))
        spin = Adw.SpinRow(adjustment=Gtk.Adjustment(lower=100, upper=250, value=self.controller.newelle_settings.zoom, page_increment=20, step_increment=10))
        row.add_suffix(spin)
        def update_zoom(x,y):
            self.controller.settings.set_int("zoom", spin.get_value())
            self.app.win.set_zoom(spin.get_value())
        spin.connect("input", update_zoom)
        self.interface.add(row)

        # Font customization
        font_expander = Adw.ExpanderRow(title=_("Font Customization"), subtitle=_("Customize fonts in chat messages"))

        font_entry = Gtk.Entry(text=self.controller.newelle_settings.font_family, valign=Gtk.Align.CENTER)
        font_entry.set_placeholder_text(_("System default"))
        font_entry.connect("changed", lambda e: self._update_font_setting("font-family", e.get_text()))
        font_row = Adw.ActionRow(title=_("Font Family"), subtitle=_("Font family for chat text (empty = system default)"))
        font_row.add_suffix(font_entry)
        font_expander.add_row(font_row)

        font_size_spin = Adw.SpinRow(
            title=_("Font Size"),
            subtitle=_("Font size for chat text (0 = system default)"),
            adjustment=Gtk.Adjustment(lower=0, upper=48, value=self.controller.newelle_settings.font_size, step_increment=1, page_increment=5),
        )
        font_size_spin.connect("input", lambda s, i: self._update_font_setting_int("font-size", s))
        font_expander.add_row(font_size_spin)

        line_height_spin = Adw.SpinRow(
            title=_("Line Height"),
            subtitle=_("Line height for chat text"),
            adjustment=Gtk.Adjustment(lower=1.0, upper=3.0, value=self.controller.newelle_settings.line_height, step_increment=0.05, page_increment=0.25),
            digits=2,
        )
        line_height_spin.connect("input", lambda s, i: self._update_font_setting_double("line-height", s))
        font_expander.add_row(line_height_spin)

        mono_entry = Gtk.Entry(text=self.controller.newelle_settings.monospace_font_family, valign=Gtk.Align.CENTER)
        mono_entry.set_placeholder_text(_("System default"))
        mono_entry.connect("changed", lambda e: self._update_font_setting("monospace-font-family", e.get_text()))
        mono_row = Adw.ActionRow(title=_("Monospace Font Family"), subtitle=_("Font family for code blocks (empty = system default)"))
        mono_row.add_suffix(mono_entry)
        font_expander.add_row(mono_row)

        mono_size_spin = Adw.SpinRow(
            title=_("Monospace Font Size"),
            subtitle=_("Font size for code blocks (0 = system default)"),
            adjustment=Gtk.Adjustment(lower=0, upper=48, value=self.controller.newelle_settings.monospace_font_size, step_increment=1, page_increment=5),
        )
        mono_size_spin.connect("input", lambda s, i: self._update_font_setting_int("monospace-font-size", s))
        font_expander.add_row(mono_size_spin)

        mono_lh_spin = Adw.SpinRow(
            title=_("Monospace Line Height"),
            subtitle=_("Line height for code blocks"),
            adjustment=Gtk.Adjustment(lower=1.0, upper=3.0, value=self.controller.newelle_settings.monospace_line_height, step_increment=0.05, page_increment=0.25),
            digits=2,
        )
        mono_lh_spin.connect("input", lambda s, i: self._update_font_setting_double("monospace-line-height", s))
        font_expander.add_row(mono_lh_spin)

        self.interface.add(font_expander)

        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        options = style_scheme_manager.get_scheme_ids()
        if options is not None:
            row = Adw.ComboRow(title=_("Editor color scheme"), subtitle=_("Change the color scheme of the editor and codeblocks"), )
            opts = tuple()
            for option in options:
                opts += ((option, option),)
            helper = ComboRowHelper(row, opts, self.settings.get_string("editor-color-scheme"))
            helper.connect("changed", lambda x,y: self.settings.set_string("editor-color-scheme", y))
            self.interface.add(row)
        row = Adw.ActionRow(title=_("Hidden files"), subtitle=_("Show hidden files"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("hidden-files", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title=_("Hide History on Launch"), subtitle=_("Hide the history sidebar when the application starts"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("hide-history-on-launch", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title=_("Remember assistant profile per chat"), subtitle=_("When changing chat, the profile corresponding to the last generation is selected"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("remember-profile", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title=_("Send with ENTER"), subtitle=_("If enabled, messages will be sent with ENTER, to go to a new line use CTRL+ENTER. If disabled, messages will be sent with SHIFT+ENTER, and newline with enter"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("send-on-enter", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)
 
        row = Adw.ActionRow(title=_("Display LaTeX"), subtitle=_("Display LaTeX formulas in chat"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("display-latex", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title=_("Expand reasoning by default"), subtitle=_("Expand the reasoning widget by default and keep it expanded when finished"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("expand-reasoning", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(
            title=_("Compact mode"),
            subtitle=_("Group tool calls from each agent iteration into one expandable box"),
        )
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("compact-mode", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(
            title=_("Compact input bar"),
            subtitle=_("Show only the mic and send buttons next to the text, and move the other controls into a popover"),
        )
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("compact-input-bar", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title=_("Reverse Chat Order"), subtitle=_("Show most recent chats on top in chat list (change chat to apply)"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("reverse-order", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)
        
        row = Adw.ActionRow(title=_("Show Chat Warning"), subtitle=_("Show a warning at the top of the chat about AI safety"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("hide-warning", switch, 'active', Gio.SettingsBindFlags.INVERT_BOOLEAN)
        self.interface.add(row)
        
        chat_name_row = Adw.ExpanderRow(title=_("Automatically Generate Chat Names"), subtitle=_("Generate chat names automatically after the first two messages"))
        chat_name_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        chat_name_row.add_suffix(chat_name_switch)
        self.settings.bind("auto-generate-name", chat_name_switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.add_customize_prompt_content(chat_name_row, "generate_name_prompt")
        self.interface.add(chat_name_row)
        
        offers_row = Adw.ExpanderRow(title=_("Number of offers"), subtitle=_("Number of message suggestions to send to chat "))
        int_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        int_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=5, step_increment=1, page_increment=10, page_size=0))
        offers_row.add_suffix(int_spin)
        self.settings.bind("offers", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.add_customize_prompt_content(offers_row, "get_suggestions_prompt")
        self.interface.add(offers_row)
        
        row = Adw.ActionRow(title=_("Username"), subtitle=_("Change the label that appears before your message\nThis information is not sent to the LLM by default\nYou can add it to a prompt using the {USER} variable"))
        entry = Gtk.Entry(text=self.controller.newelle_settings.username, valign=Gtk.Align.CENTER)
        entry.connect("changed", lambda entry: self.settings.set_string("user-name", entry.get_text()))
        row.add_suffix(entry)
        self.settings.bind("offers", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)
        # Browser
        self.build_browser_settings()
        self.general_page.add(self.browser_group)
        # Neural Network Control
        self.neural_network = Adw.PreferencesGroup(title=_('Neural Network Control'))
        self.general_page.add(self.neural_network) 

        # Only show virtualization option if running in Flatpak
        if is_flatpak():
            row = Adw.ActionRow(title=_("Command virtualization"), subtitle=_("Run commands in a virtual machine"))
            switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            row.add_suffix(switch)
            # Set default value for the switch
            if not self.sandbox:
                switch.set_active(True)
                self.settings.set_boolean("virtualization", True)
            else:
                switch.set_active(self.settings.get_boolean("virtualization"))
            # Connect the function
            switch.connect("state-set", self.toggle_virtualization)
            self.neural_network.add(row)
        
        row = Adw.ActionRow(title=_("Parallel Tool Execution"), subtitle=_("Allow the model to execute multiple tools in parallel"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("parallel-tool-execution", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.neural_network.add(row)

        max_tool_calls_row = Adw.SpinRow(
            title=_("Maximum Tool Calls"),
            subtitle=_("Maximum number of tools the model can run for one request, including scheduled tasks"),
            adjustment=Gtk.Adjustment(
                lower=1,
                upper=300,
                step_increment=1,
                page_increment=10,
                value=self.settings.get_int("max-tool-calls"),
            ),
            digits=0,
        )
        def update_max_tool_calls(spin, _value):
            self.settings.set_int("max-tool-calls", int(spin.get_value()))
        max_tool_calls_row.connect("notify::value", update_max_tool_calls)
        self.neural_network.add(max_tool_calls_row)
        
        row = Adw.ExpanderRow(title=_("External Terminal"), subtitle=_("Choose the external terminal where to run the console commands"))
        terminal_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("external-terminal-on", terminal_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        row.add_suffix(terminal_enabled)
        entry = Gtk.Entry()
        self.settings.bind("external-terminal", entry, 'text', Gio.SettingsBindFlags.DEFAULT)
        row.add_row(entry)
        self.neural_network.add(row)
        # Context Management
        context_expander = Adw.ExpanderRow(
            title=_("Context Management"),
            subtitle=_("Control how conversation history is sent to the model"),
        )

        current_mode = self.settings.get_string("context-mode")
        context_mode_cm = Gtk.ToggleButton(label=_("Context Manager"))
        context_mode_fixed = Gtk.ToggleButton(label=_("Fixed message count"))
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, valign=Gtk.Align.CENTER)
        mode_box.add_css_class("linked")
        mode_box.append(context_mode_cm)
        mode_box.append(context_mode_fixed)

        # Initialize exclusive selection
        if current_mode == "fixed":
            context_mode_fixed.set_active(True)
            context_mode_cm.set_active(False)
        else:
            context_mode_cm.set_active(True)
            context_mode_fixed.set_active(False)

        mode_row = Adw.ActionRow(title=_("Mode"))
        mode_row.add_suffix(mode_box)
        context_expander.add_row(mode_row)

        # Fixed mode: message count
        fixed_row = Adw.ActionRow(title=_("Message count"), subtitle=_("Number of messages to keep in context"))
        fixed_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        fixed_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=90, step_increment=1, page_increment=10, page_size=0))
        fixed_row.add_suffix(fixed_spin)
        self.settings.bind("memory", fixed_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        context_expander.add_row(fixed_row)

        # Context Manager mode: max tokens
        max_adj = Gtk.Adjustment(lower=1000, upper=1000000, step_increment=1000, page_increment=10000)
        max_adj.set_value(self.settings.get_int("context-max"))
        max_row = Adw.SpinRow(
            title=_("Max Context Size"),
            subtitle=_("Hard token limit — context will never exceed this"),
            adjustment=max_adj,
            digits=0,
        )
        def update_context_max(spin, _input):
            self.settings.set_int("context-max", int(spin.get_value()))
            return False
        max_row.connect("input", update_context_max)
        context_expander.add_row(max_row)

        # Context Manager mode: suggested tokens
        suggested_adj = Gtk.Adjustment(lower=1000, upper=500000, step_increment=1000, page_increment=10000)
        suggested_adj.set_value(self.settings.get_int("context-suggested"))
        suggested_row = Adw.SpinRow(
            title=_("Suggested Context Size"),
            subtitle=_("Soft token target — less relevant messages are dropped to stay near this"),
            adjustment=suggested_adj,
            digits=0,
        )
        def update_context_suggested(spin, _input):
            self.settings.set_int("context-suggested", int(spin.get_value()))
            return False
        suggested_row.connect("input", update_context_suggested)
        context_expander.add_row(suggested_row)

        # Context Manager mode: summarization toggle
        summarize_row = Adw.ActionRow(
            title=_("Summarize dropped messages"),
            subtitle=_("Use the LLM to summarize messages that were removed from context"),
        )
        summarize_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        summarize_row.add_suffix(summarize_switch)
        self.settings.bind("context-summarization", summarize_switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        context_expander.add_row(summarize_row)

        self.context_cm_rows = [max_row, suggested_row, summarize_row]
        self.context_fixed_rows = [fixed_row]

        def apply_context_mode(is_cm: bool):
            mode = "context-manager" if is_cm else "fixed"
            self.settings.set_string("context-mode", mode)
            for r in self.context_cm_rows:
                r.set_visible(is_cm)
            for r in self.context_fixed_rows:
                r.set_visible(not is_cm)

        def on_cm_toggled(btn):
            if btn.get_active():
                context_mode_fixed.set_active(False)
                apply_context_mode(True)
            else:
                # Keep one option selected at all times
                if not context_mode_fixed.get_active():
                    btn.set_active(True)

        def on_fixed_toggled(btn):
            if btn.get_active():
                context_mode_cm.set_active(False)
                apply_context_mode(False)
            else:
                # Keep one option selected at all times
                if not context_mode_cm.get_active():
                    btn.set_active(True)

        context_mode_cm.connect("toggled", on_cm_toggled)
        context_mode_fixed.connect("toggled", on_fixed_toggled)

        is_cm = current_mode != "fixed"
        for r in self.context_cm_rows:
            r.set_visible(is_cm)
        for r in self.context_fixed_rows:
            r.set_visible(not is_cm)

        self.KNOWLEDGE.add(context_expander)
        # Developer settings
        self.developer = Adw.PreferencesGroup(title=_('Developer'))
        self.general_page.add(self.developer)
        # Program Output Monitor
        row = Adw.ActionRow(title=_("Program Output Monitor"), subtitle=_("Monitor the program output in real-time, useful for debugging and seeing downloads progress"))
        button = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
        row.add_suffix(button)
        button.connect("clicked", lambda _ : self.app.win.show_stdout_monitor_dialog(self))
        self.developer.add(row)
        # Delete pip path
        row = Adw.ActionRow(title=_("Delete pip path"), subtitle=_("Remove the extra dependencies installed"))
        button = Gtk.Button(label=_("Delete"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        row.add_suffix(button)
        button.connect("clicked", lambda _ : self.delete_pip_path())
        self.developer.add(row)
        # Install pip module 
        row = Adw.ActionRow(title=_("Install pip module"), subtitle=_("Manually install pip module"))
        entry = Gtk.Entry(valign=Gtk.Align.CENTER)
        button = Gtk.Button(icon_name="download-symbolic", valign=Gtk.Align.CENTER)
        row.add_suffix(entry)
        row.add_suffix(button)
        def install_custom_module(button):
            module = entry.get_text()
            def install_thread():
                install_module(module, self.controller.pip_path, True)
            t = threading.Thread(target=install_thread)
            t.start()
            self.app.downloads_action()
        button.connect("clicked", install_custom_module)
        self.developer.add(row)
        
        if self.popup:
            self.InterfacesPage = Adw.PreferencesPage(
                icon_name="controls-big-symbolic",
                title=_("Interfaces"),
            )
            self.ExtensionsPage = Adw.PreferencesPage(
                icon_name="extension-symbolic",
                title=_("Extensions"),
            )
        else:
            self.InterfacesPage = InterfacesPage(
                self.app,
                self.controller,
            )
            self.ExtensionsPage = ExtensionPage(
                self.app,
                self.controller,
                toast_callback=self.add_toast,
            )

        self._build_navigation(startup_page)
        if self.popup:
            # Popup uses tools_group immediately when building its stack.
            self.ensure_tools_page_initialized()

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    def _build_navigation(self, startup_page):
        self.toast_overlay = Adw.ToastOverlay()
        self.split_view = Adw.NavigationSplitView()
        self.content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=150,
        )

        page_definitions = [
            ("General", _("General"), "settings-symbolic", self.general_page),
            ("LLM", _("LLM"), "brain-augemnted-symbolic", self.LLMPage),
            ("Memory", _("Knowledge"), "vcard-symbolic", self.MemoryPage),
            ("Voice", _("Voice"), "audio-input-microphone-symbolic", self.VoicePage),
            ("Prompts", _("Prompts"), "question-round-outline-symbolic", self.PromptsPage),
            ("Tools", _("Tools"), "tools-symbolic", self.ToolsPage),
            ("Permissions", _("Permissions"), "key-symbolic", self.PermissionsPage),
            ("Skills", _("Skills"), "skills-symbolic", self.SkillsPage),
            ("MCP", _("MCP Servers"), "internet-symbolic", self.MCPPage),
            ("Interfaces", _("Interfaces"), "controls-big-symbolic", self.InterfacesPage),
            ("Extensions", _("Extensions"), "extension-symbolic", self.ExtensionsPage),
        ]
        self.navigation_pages = {
            key: (title, page)
            for key, title, _icon_name, page in page_definitions
        }

        self.navigation_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            activate_on_single_click=True,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        self.navigation_list.add_css_class("navigation-sidebar")
        self.navigation_list.connect("row-selected", self.on_navigation_row_selected)
        self.navigation_list.connect("row-activated", self.on_navigation_row_activated)

        self.navigation_rows = {}
        for key, title, icon_name, page in page_definitions:
            self.content_stack.add_named(page, key)
            row = Adw.ActionRow(title=title)
            row.add_prefix(Gtk.Image(icon_name=icon_name))
            row.settings_page_key = key
            self.navigation_list.append(row)
            self.navigation_rows[key] = row

        sidebar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        sidebar_scroll.set_child(self.navigation_list)
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_toolbar.add_top_bar(Adw.HeaderBar())
        sidebar_toolbar.set_content(sidebar_scroll)
        sidebar_page = Adw.NavigationPage.new(sidebar_toolbar, _("Settings"))

        content_toolbar = Adw.ToolbarView()
        content_toolbar.add_top_bar(Adw.HeaderBar())
        content_toolbar.set_content(self.content_stack)
        self.content_navigation_page = Adw.NavigationPage.new(
            content_toolbar,
            _("General"),
        )

        self.split_view.set_sidebar(sidebar_page)
        self.split_view.set_content(self.content_navigation_page)
        self.toast_overlay.set_child(self.split_view)
        self.set_content(self.toast_overlay)

        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 700sp")
        )
        breakpoint.add_setter(self.split_view, "collapsed", True)
        self.add_breakpoint(breakpoint)

        selected_key = startup_page if startup_page in self.navigation_rows else "General"
        self.navigation_list.select_row(self.navigation_rows[selected_key])
        if startup_page in self.navigation_rows:
            self.split_view.set_show_content(True)

    def on_navigation_row_selected(self, _listbox, row):
        if row is None:
            return
        key = row.settings_page_key
        title, page = self.navigation_pages[key]
        if page == self.ToolsPage:
            self.ensure_tools_page_initialized()
        elif page == self.PermissionsPage:
            self.ensure_permissions_page_initialized()
        elif page == self.SkillsPage:
            self.ensure_skills_page_initialized()
        elif page == self.MCPPage:
            self.ensure_mcp_page_initialized()
        self.content_stack.set_visible_child(page)
        self.content_navigation_page.set_title(title)

    def on_navigation_row_activated(self, _listbox, _row):
        self.split_view.set_show_content(True)

    def ensure_tools_page_initialized(self):
        if not self.tools_page_initialized:
            self.build_tools_page()

    def ensure_permissions_page_initialized(self):
        if not self.permissions_page_initialized:
            self.build_permissions_page()

    def ensure_skills_page_initialized(self):
        if not self.skills_page_initialized:
            self.build_skills_settings()

    def ensure_mcp_page_initialized(self):
        if not self.mcp_page_initialized:
            self.build_mcp_settings()

    def build_tools_page(self):
        if self.tools_page_initialized or self._building_tools_page:
            return
        self._building_tools_page = True
        self.tools_page_initialized = True
        self.tools_group = Adw.PreferencesGroup(title=_("Tools"))
        self.ToolsPage.add(self.tools_group)
        self.refresh_tools_list()
        self._building_tools_page = False

    def refresh_extension_resources(self, refreshes):
        """Refresh only settings sections affected by extension changes."""
        self.extensionloader = self.controller.extensionloader
        self.handlers = self.controller.handlers
        if "llm_handlers" in refreshes:
            self.refresh_llm_rows()
        if "tools" in refreshes and self.tools_page_initialized:
            self.refresh_tools_list()
        if "prompts" in refreshes:
            self.custom_prompts = self.controller.newelle_settings.custom_prompts
            self.prompts_settings = self.controller.newelle_settings.prompts_settings
            self.prompts = self.controller.newelle_settings.prompts
            self.build_prompts_settings()
        if "interfaces" in refreshes and hasattr(self.InterfacesPage, "refresh"):
            self.InterfacesPage.refresh()

    def refresh_llm_rows(self):
        """Refresh primary and secondary LLM choices in this live window."""
        for row in self._llm_primary_rows:
            self.LLM.remove(row)
        for row in self._llm_primary_other_rows:
            self._llm_primary_other_group.remove(row)
        for row in self._llm_secondary_rows:
            self._llm_secondary_model_group.remove(row)
        for row in self._llm_secondary_other_rows:
            self._llm_secondary_other_group.remove(row)
        # Keep the catch-all expander after all regular providers.  If it stays
        # attached while rows are rebuilt, newly added providers are appended
        # after it and make “Other LLMs” jump up the list.
        self.LLM.remove(self._llm_primary_other_group)
        self._llm_secondary_model_group.remove(self._llm_secondary_other_group)

        llm_constant = self.convert_constants(AVAILABLE_LLMS)
        for settings_key in list(self.settingsrows):
            if (
                len(settings_key) == 3
                and settings_key[1] == llm_constant
                and settings_key[2] in (False, True)
            ):
                del self.settingsrows[settings_key]

        self._llm_primary_rows = []
        self._llm_primary_other_rows = []
        self._llm_secondary_rows = []
        self._llm_secondary_other_rows = []

        primary_group = Gtk.CheckButton()
        selected = self.settings.get_string("language-model")
        for model_key in AVAILABLE_LLMS:
            row = self.build_row(AVAILABLE_LLMS, model_key, selected, primary_group)
            if AVAILABLE_LLMS[model_key].get("secondary", False):
                self._llm_primary_other_group.add_row(row)
                self._llm_primary_other_rows.append(row)
            else:
                self.LLM.add(row)
                self._llm_primary_rows.append(row)
        self.LLM.add(self._llm_primary_other_group)

        secondary_group = Gtk.CheckButton()
        selected = self.settings.get_string("secondary-language-model")
        for model_key in AVAILABLE_LLMS:
            row = self.build_row(
                AVAILABLE_LLMS,
                model_key,
                selected,
                secondary_group,
                True,
            )
            if AVAILABLE_LLMS[model_key].get("secondary", False):
                self._llm_secondary_other_group.add_row(row)
                self._llm_secondary_other_rows.append(row)
            else:
                self._llm_secondary_model_group.add_row(row)
                self._llm_secondary_rows.append(row)
        self._llm_secondary_model_group.add_row(self._llm_secondary_other_group)

    @staticmethod
    def _suggest_duplicated_llm_key(source_key: str) -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", source_key).strip("._-")
        base = (base or "llm") + "_copy"
        candidate = base
        suffix = 2
        while candidate in AVAILABLE_LLMS:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def on_duplicate_llm(self, _button):
        """Open the dialog used to create a custom copy of an LLM handler."""
        duplicable = self.handlers.get_duplicable_llms()
        if not duplicable:
            dialog = Adw.MessageDialog(
                transient_for=self,
                modal=True,
                heading=_("No handlers can be duplicated"),
                body=_("No LLM handler exposes duplication settings."),
            )
            dialog.add_response("close", _("Close"))
            dialog.set_close_response("close")
            dialog.connect("response", lambda current, _response: current.destroy())
            dialog.present()
            return

        dialog = Gtk.Window(
            title=_("Add LLM Provider"),
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
        )
        dialog.set_default_size(600, 560)
        header = Adw.HeaderBar(css_classes=["flat"])
        dialog.set_titlebar(header)
        cancel_button = Gtk.Button(label=_("Cancel"), css_classes=["flat"])
        cancel_button.connect("clicked", lambda _clicked: dialog.close())
        add_button = Gtk.Button(
            label=_("Add"), css_classes=["suggested-action"]
        )
        header.pack_start(cancel_button)
        header.pack_end(add_button)

        page = Adw.PreferencesPage()
        details_group = Adw.PreferencesGroup(
            title=_("Provider"),
            description=_("Choose a handler implementation and identify the new provider."),
        )
        page.add(details_group)

        source_row = Adw.ComboRow(
            title=_("Copy LLM Handler"),
            subtitle=_("The implementation used by this provider"),
        )
        source_options = tuple(
            (descriptor["title"], source_key)
            for source_key, descriptor, _settings in duplicable
        )
        default_source = (
            "openai"
            if any(source_key == "openai" for _title, source_key in source_options)
            else source_options[0][1]
        )
        source_helper = ComboRowHelper(source_row, source_options, default_source)
        details_group.add(source_row)

        name_row = Adw.EntryRow(title=_("Handler Name"))
        key_row = Adw.EntryRow(title=_("Handler Key"))
        description_row = Adw.EntryRow(title=_("Description"))
        details_group.add(name_row)
        details_group.add(key_row)
        details_group.add(description_row)

        validation_row = Adw.ActionRow()
        validation_row.add_css_class("error")
        validation_row.set_visible(False)
        details_group.add(validation_row)

        duplication_group = Adw.PreferencesGroup(
            title=_("Connection"),
            description=_("Settings needed to create this provider."),
        )
        page.add(duplication_group)
        scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scrolled.set_child(page)
        dialog.set_child(scrolled)

        duplication_by_source = {
            source_key: (descriptor, settings)
            for source_key, descriptor, settings in duplicable
        }
        duplication_rows = []
        duplication_values = {}
        setting_helpers = []
        selected_source = default_source

        def set_validation_error(message: str):
            validation_row.set_title(message)
            validation_row.set_visible(bool(message))

        def validate(*_args):
            name = name_row.get_text().strip()
            key = key_row.get_text().strip()
            message = ""
            if not name:
                message = _("Enter a handler name.")
            elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key):
                message = _("Use only letters, numbers, '.', '_' and '-' in the key.")
            elif key in AVAILABLE_LLMS:
                message = _("This handler key is already in use.")
            set_validation_error(message)
            add_button.set_sensitive(not message)

        def add_duplication_setting(setting: dict):
            if setting.get("type") == "nested":
                for nested in setting.get("extra_settings", []):
                    add_duplication_setting(nested)
                return
            setting_key = setting.get("key")
            if not setting_key:
                return
            value = setting.get("default")
            duplication_values[setting_key] = value

            if setting.get("type") in ("entry", "multilineentry"):
                setting_row = Adw.EntryRow(
                    title=setting.get("title", setting_key),
                    text="" if value is None else str(value),
                )
                setting_row.connect(
                    "changed",
                    lambda current, current_key=setting_key: duplication_values.__setitem__(
                        current_key, current.get_text()
                    ),
                )
            elif setting.get("type") == "toggle":
                setting_row = Adw.ActionRow(
                    title=setting.get("title", setting_key),
                    subtitle=setting.get("description", ""),
                )
                toggle = Gtk.Switch(valign=Gtk.Align.CENTER, active=bool(value))
                toggle.connect(
                    "notify::active",
                    lambda current, _pspec, current_key=setting_key: duplication_values.__setitem__(
                        current_key, current.get_active()
                    ),
                )
                setting_row.add_suffix(toggle)
            elif setting.get("type") == "combo":
                setting_row = Adw.ComboRow(
                    title=setting.get("title", setting_key),
                    subtitle=setting.get("description", ""),
                )
                helper = ComboRowHelper(
                    setting_row, setting.get("values", ()), value
                )
                helper.connect(
                    "changed",
                    lambda _helper, current_value, current_key=setting_key: duplication_values.__setitem__(
                        current_key, current_value
                    ),
                )
                setting_helpers.append(helper)
            elif setting.get("type") in ("range", "spin"):
                digits = setting.get("round-digits", 0)
                adjustment = Gtk.Adjustment(
                    value=value,
                    lower=setting.get("min", 0),
                    upper=setting.get("max", 100),
                    step_increment=setting.get("step", 1),
                    page_increment=setting.get("page", 10),
                )
                setting_row = Adw.SpinRow(
                    title=setting.get("title", setting_key),
                    subtitle=setting.get("description", ""),
                    adjustment=adjustment,
                    digits=digits,
                )

                def on_value_changed(current, _pspec, current_key=setting_key):
                    current_value = current.get_value()
                    if current.get_digits() == 0:
                        current_value = int(current_value)
                    duplication_values[current_key] = current_value

                setting_row.connect("notify::value", on_value_changed)
            else:
                return

            if setting.get("type") == "entry":
                setting_row.set_show_apply_button(False)
            duplication_group.add(setting_row)
            duplication_rows.append(setting_row)

        def select_source(source_key: str):
            nonlocal selected_source
            selected_source = source_key
            for old_row in duplication_rows:
                duplication_group.remove(old_row)
            duplication_rows.clear()
            duplication_values.clear()
            setting_helpers.clear()

            descriptor, settings = duplication_by_source[source_key]
            name_row.set_text(_("{0} Copy").format(descriptor["title"]))
            key_row.set_text(self._suggest_duplicated_llm_key(source_key))
            description_row.set_text(
                _("Custom provider based on {0}").format(descriptor["title"])
            )
            for setting in settings:
                add_duplication_setting(setting)
            duplication_group.set_visible(bool(duplication_rows))
            validate()

        source_helper.connect(
            "changed", lambda _helper, source_key: select_source(source_key)
        )
        name_row.connect("changed", validate)
        key_row.connect("changed", validate)
        select_source(selected_source)

        def create_duplicate(_clicked):
            try:
                self.handlers.duplicate_llm(
                    source=selected_source,
                    key=key_row.get_text(),
                    title=name_row.get_text(),
                    description=description_row.get_text(),
                    duplication_values=duplication_values,
                )
            except ValueError as error:
                set_validation_error(str(error))
                add_button.set_sensitive(False)
                return
            self.refresh_llm_rows()
            dialog.close()

        add_button.connect("clicked", create_duplicate)
        # Keep helpers alive for as long as their GTK signal handlers are used.
        dialog._llm_duplication_helpers = [source_helper, setting_helpers]
        dialog.present()

    def on_delete_duplicated_llm(self, _button, key: str):
        descriptor = AVAILABLE_LLMS.get(key)
        if descriptor is None or not descriptor.get("duplicated", False):
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=_("Delete LLM Provider?"),
            body=_("Delete {0} and all of its saved settings?").format(
                descriptor["title"]
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance(
            "delete", Adw.ResponseAppearance.DESTRUCTIVE
        )

        def on_response(current, response):
            if response == "delete" and self.handlers.delete_duplicated_llm(key):
                self.refresh_llm_rows()
                if self.popup:
                    self.app.win.update_available_models()
            current.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def build_permissions_page(self):
        if self.permissions_page_initialized or self._building_permissions_page:
            return
        self._building_permissions_page = True
        self.permissions_page_initialized = True
        self.build_file_permissions_settings()
        self.build_command_permissions_settings()
        self.build_path_security_settings()
        self._building_permissions_page = False

    def _perf_add(self, name: str):
        if self.p is not None:
            self.p.add(name)

    def _perf_print(self):
        if self.p is not None:
            self.p.print_differences()

    def refresh_tools_list(self):
        if not self.tools_page_initialized and not self._building_tools_page:
            self.ensure_tools_page_initialized()
        if not self.tools_page_initialized:
            return
        for row in self.tool_rows:
            self.tools_group.remove(row)
        self.tool_rows = []
        
        tools_settings = self.controller.newelle_settings.tools_settings_dict
        # Get all tools
        tools = self.controller.tools.get_all_tools()
        
        # Organize tools by group
        groups = {}
        orphans = []
        for tool in tools:
            if hasattr(tool, "tools_group") and tool.tools_group:
                if tool.tools_group not in groups:
                    groups[tool.tools_group] = []
                groups[tool.tools_group].append(tool)
            else:
                orphans.append(tool)
        
        # Create group rows
        for group_name, group_tools in groups.items():
            tool_count = len(group_tools)
            tools_string = _("tools") if tool_count != 1 else _("tool")
            group_row = Adw.ExpanderRow(
                title=group_name,
                subtitle=("{} {}").format(tool_count, tools_string)
            )
            # Add folder icon to distinguish groups from individual tools
            group_icon = Gtk.Image(icon_name="folder-symbolic", css_classes=["dim-label"])
            group_row.add_prefix(group_icon)
            
            tool_switches = []
            
            # Add tools to group
            for tool in group_tools:
                row, toggle = self.create_tool_row(tool, tools_settings)
                group_row.add_row(row)
                tool_switches.append(toggle)

            # Check if all enabled to set group toggle state
            all_enabled = all(t.get_active() for t in tool_switches)
            
            group_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
            group_toggle.set_active(all_enabled)
            group_toggle.connect("state-set", self.toggle_group, group_tools, tool_switches)
            group_row.add_suffix(group_toggle)
            
            self.tools_group.add(group_row)
            self.tool_rows.append(group_row)

        # Add orphans
        if orphans:
            if groups:
                # Add a separator to distinguish if there are groups
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_margin_top(12)
                sep.set_margin_bottom(12)
                self.tools_group.add(sep)
                self.tool_rows.append(sep)

            for tool in orphans:
                row, unused_toggle = self.create_tool_row(tool, tools_settings)
                self.tools_group.add(row)
                self.tool_rows.append(row)

    def create_tool_row(self, tool, tools_settings):
        # Default values - use tool's default_on attribute
        is_enabled = tool.default_on
        custom_prompt = None
        
        if tool.name in tools_settings:
            if "enabled" in tools_settings[tool.name]:
                is_enabled = tools_settings[tool.name]["enabled"]
            if "custom_prompt" in tools_settings[tool.name]:
                custom_prompt = tools_settings[tool.name]["custom_prompt"]
        
        # Create row
        row = Adw.ExpanderRow(title=tool.title, subtitle=tool.description)
        # Add tool icon to distinguish from groups
        icon_name = tool.icon_name if tool.icon_name else "tools-symbolic"
        tool_icon = Gtk.Image(icon_name=icon_name, css_classes=["dim-label"])
        row.add_prefix(tool_icon)

        # Small warning when the active mode overrides this tool's enablement
        mode_warning = self._get_tool_mode_warning(tool.name, is_enabled)
        if mode_warning is not None:
            self._add_mode_warning_suffix(row, mode_warning)

        # Toggle
        toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        toggle.set_active(is_enabled)
        toggle.connect("state-set", self.toggle_tool, tool.name)
        row.add_suffix(toggle)

        # Lazy load toggle
        is_lazy = tool.default_lazy_load
        if tool.name in tools_settings and "lazy_load" in tools_settings[tool.name]:
            is_lazy = tools_settings[tool.name]["lazy_load"]
        lazy_row = Adw.ActionRow(title=_("Lazy load"), subtitle=_("Show compact description to reduce context usage"))
        lazy_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        lazy_toggle.set_active(is_lazy)
        lazy_toggle.connect("state-set", self.toggle_tool_lazy_load, tool.name)
        lazy_row.add_suffix(lazy_toggle)
        row.add_row(lazy_row)
        
        # Generate default prompt for this tool
        default_prompt_obj = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.schema
        }
        default_prompt = json.dumps(default_prompt_obj, indent=2)
        
        entry = MultilineEntry()
        entry.set_text(custom_prompt if custom_prompt else default_prompt)
        entry.tool_name = tool.name
        entry.default_prompt = default_prompt
        entry.set_on_change(self.update_tool_prompt)

        box = Gtk.Box(spacing=6)
        box.append(entry)
        
        # Star button to reset
        reset_button = Gtk.Button(icon_name="star-filled-rounded-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER)
        reset_button.connect("clicked", self.reset_tool_prompt, entry)
        box.append(reset_button)
        
        row.add_row(box)
        
        return row, toggle

    def toggle_group(self, switch, state, tools, tool_switches):
        # Update UI first (this will trigger toggle_tool for each switch)
        for s in tool_switches:
            if s.get_active() != state:
                s.set_active(state)

    def toggle_tool(self, switch, state, tool_name):
        tools_settings = self.controller.newelle_settings.tools_settings_dict
        
        if tool_name not in tools_settings:
            tool = self.controller.tools.get_tool(tool_name)
            default_on = tool.default_on if tool else True
            tools_settings[tool_name] = {"enabled": default_on, "custom_prompt": None}
            
        tools_settings[tool_name]["enabled"] = state
        self.settings.set_string("tools-settings", json.dumps(tools_settings))

    def toggle_tool_lazy_load(self, switch, state, tool_name):
        tools_settings = self.controller.newelle_settings.tools_settings_dict

        if tool_name not in tools_settings:
            tool = self.controller.tools.get_tool(tool_name)
            default_on = tool.default_on if tool else True
            tools_settings[tool_name] = {"enabled": default_on, "custom_prompt": None}

        tools_settings[tool_name]["lazy_load"] = state
        self.settings.set_string("tools-settings", json.dumps(tools_settings))

    def update_tool_prompt(self, entry):
        tool_name = entry.tool_name
        text = entry.get_text()
        
        tools_settings = self.controller.newelle_settings.tools_settings_dict
            
        if tool_name not in tools_settings:
            tool = self.controller.tools.get_tool(tool_name)
            default_on = tool.default_on if tool else True
            tools_settings[tool_name] = {"enabled": default_on, "custom_prompt": None}

        if text == entry.default_prompt:
            tools_settings[tool_name]["custom_prompt"] = None
        else:
            tools_settings[tool_name]["custom_prompt"] = text

        self.settings.set_string("tools-settings", json.dumps(tools_settings))

    def reset_tool_prompt(self, button, entry):
        entry.set_text(entry.default_prompt)
        self.update_tool_prompt(entry)

    # --- File Permissions settings ---

    def build_file_permissions_settings(self):
        self.file_permissions_group = Adw.PreferencesGroup(
            title=_("File Permissions"),
            description=_("Control which directories the agent can read from or write to")
        )
        self.PermissionsPage.add(self.file_permissions_group)

        add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        add_button.set_tooltip_text(_("Add custom directory rule"))
        add_button.connect("clicked", self._on_add_file_permission_clicked)
        self.file_permissions_group.set_header_suffix(add_button)

        self.file_permission_rows = []
        self._refresh_file_permissions_list()

    def _get_file_permissions(self):
        try:
            return json.loads(self.settings.get_string("file-permissions"))
        except Exception:
            return [
                {"path": "*", "read": "allow", "write": "ask"},
                {"path": "{{main_path}}", "read": "allow", "write": "ask"},
            ]

    def _save_file_permissions(self, rules):
        self.settings.set_string("file-permissions", json.dumps(rules))

    def _refresh_file_permissions_list(self):
        for row in self.file_permission_rows:
            self.file_permissions_group.remove(row)
        self.file_permission_rows = []

        rules = self._get_file_permissions()
        for idx, rule in enumerate(rules):
            row = self._create_file_permission_row(rule, idx)
            self.file_permissions_group.add(row)
            self.file_permission_rows.append(row)

    def _display_name_for_path(self, path):
        if path == "*":
            return _("All Files")
        if path == "{{main_path}}":
            return _("Current Work Directory")
        return path

    def _create_file_permission_row(self, rule, idx):
        path = rule.get("path", "*")
        display_name = self._display_name_for_path(path)
        is_builtin = path in ("*", "{{main_path}}")

        if is_builtin:
            icon_name = "internet-symbolic" if path == "*" else "folder-visiting-symbolic"
        else:
            icon_name = "folder-symbolic"

        row = Adw.ExpanderRow(title=display_name)
        if not is_builtin:
            row.set_subtitle(path)
        prefix_icon = Gtk.Image(icon_name=icon_name, css_classes=["dim-label"])
        row.add_prefix(prefix_icon)

        mode_labels = [_("Block Everything"), _("Ask"), _("Allow Everything")]
        mode_values = ["block", "ask", "allow"]

        # Read permission combo
        read_row = Adw.ActionRow(title=_("Read"))
        read_combo = Gtk.ComboBoxText()
        for label in mode_labels:
            read_combo.append_text(label)
        current_read = rule.get("read", "allow")
        read_combo.set_active(mode_values.index(current_read) if current_read in mode_values else 2)
        read_combo.set_valign(Gtk.Align.CENTER)
        read_combo.connect("changed", self._on_file_permission_changed, idx, "read", mode_values)
        read_row.add_suffix(read_combo)
        row.add_row(read_row)

        # Write permission combo
        write_row = Adw.ActionRow(title=_("Write"))
        write_combo = Gtk.ComboBoxText()
        for label in mode_labels:
            write_combo.append_text(label)
        current_write = rule.get("write", "ask")
        write_combo.set_active(mode_values.index(current_write) if current_write in mode_values else 1)
        write_combo.set_valign(Gtk.Align.CENTER)
        write_combo.connect("changed", self._on_file_permission_changed, idx, "write", mode_values)
        write_row.add_suffix(write_combo)
        row.add_row(write_row)

        if not is_builtin:
            remove_row = Adw.ActionRow(title=_("Remove rule"))
            remove_button = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
            remove_button.connect("clicked", self._on_remove_file_permission_clicked, idx)
            remove_row.add_suffix(remove_button)
            row.add_row(remove_row)

        return row

    def _on_file_permission_changed(self, combo, idx, operation, mode_values):
        rules = self._get_file_permissions()
        if idx < len(rules):
            active = combo.get_active()
            if 0 <= active < len(mode_values):
                rules[idx][operation] = mode_values[active]
                self._save_file_permissions(rules)

    def _on_add_file_permission_clicked(self, button):
        dialog = Gtk.FileDialog(title=_("Select Directory"))
        dialog.select_folder(self, None, self._on_file_permission_folder_selected)

    def _on_file_permission_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return

        path = folder.get_path()
        rules = self._get_file_permissions()

        for rule in rules:
            if rule.get("path") == path:
                toast = Adw.Toast(title=_("A rule for this directory already exists"))
                self.add_toast(toast)
                return

        rules.append({"path": path, "read": "allow", "write": "ask"})
        self._save_file_permissions(rules)
        self._refresh_file_permissions_list()

    def _on_remove_file_permission_clicked(self, button, idx):
        rules = self._get_file_permissions()
        if idx < len(rules):
            removed = rules.pop(idx)
            self._save_file_permissions(rules)
            self._refresh_file_permissions_list()
            toast = Adw.Toast(title=_("Rule for '{}' removed").format(removed.get("path", "")))
            self.add_toast(toast)

    # --- Command Execution Permissions ---

    def build_command_permissions_settings(self):
        from ..utility.command_permissions import CommandPermissionManager

        self.cmd_perms_group = Adw.PreferencesGroup(
            title=_("Command Execution Permissions"),
            description=_("Control which commands can run automatically via pattern rules")
        )
        self.PermissionsPage.add(self.cmd_perms_group)

        autorun_row = Adw.ExpanderRow(title=_("Auto-run commands"), subtitle=_("Automatically execute commands (subject to permission rules below)"))
        autorun_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        autorun_row.add_suffix(autorun_switch)
        autorun_spin = Adw.SpinRow(title=_("Max number of commands"), subtitle=_("Maximum number of commands that the bot will write after a single user request"), adjustment=Gtk.Adjustment(lower=0, upper=30,  page_increment=1, value=self.settings.get_int("max-run-times"), step_increment=1))
        def update_autorun_spin(spin, input):
            self.settings.set_int("max-run-times", int(spin.get_value()))
            return False
        autorun_spin.connect("input", update_autorun_spin)
        autorun_row.add_row(autorun_spin)
        self.settings.bind("auto-run", autorun_switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.cmd_perms_group.add(autorun_row)

        add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        add_button.set_tooltip_text(_("Add command pattern rule"))
        add_button.connect("clicked", self._on_add_command_permission_clicked)
        self.cmd_perms_group.set_header_suffix(add_button)

        self.cmd_permission_rows = []
        self._refresh_command_permissions_list()

        default_row = Adw.ExpanderRow(title=_("Default behavior for unknown commands"), subtitle=_("Action when no pattern matches"))
        default_combo = Gtk.ComboBoxText()
        for label, value in [("Ask", "ask"), ("Allow", "allow"), ("Block", "block")]:
            default_combo.append(value, label)
        current_default = self.settings.get_string("default-risk-level")
        default_combo.set_active_id(current_default if current_default else "ask")
        default_combo.set_valign(Gtk.Align.CENTER)
        default_combo.connect("changed", self._on_default_risk_changed)
        default_row.add_suffix(default_combo)
        self.cmd_perms_group.add(default_row)

        from ..utility.command_permissions import BUILTIN_RISK_RULES, RiskLevel
        info_row = Adw.ExpanderRow(title=_("Built-in risk rules"), subtitle=_("Pre-defined patterns that classify commands by risk level"))
        safe_count = sum(1 for _, r, _ in BUILTIN_RISK_RULES if r == RiskLevel.SAFE)
        mod_count = sum(1 for _, r, _ in BUILTIN_RISK_RULES if r == RiskLevel.MODERATE)
        dan_count = sum(1 for _, r, _ in BUILTIN_RISK_RULES if r == RiskLevel.DANGEROUS)
        crit_count = sum(1 for _, r, _ in BUILTIN_RISK_RULES if r == RiskLevel.CRITICAL)

        for level, count in [(RiskLevel.SAFE, safe_count), (RiskLevel.MODERATE, mod_count), (RiskLevel.DANGEROUS, dan_count), (RiskLevel.CRITICAL, crit_count)]:
            l_row = Adw.ActionRow(title=level.value.capitalize())
            actions = {RiskLevel.SAFE: "allow", RiskLevel.MODERATE: "ask", RiskLevel.DANGEROUS: "ask", RiskLevel.CRITICAL: "block"}
            l_row.set_subtitle(f"{count} patterns — auto-{actions[level]}")
            info_row.add_row(l_row)

        self.cmd_perms_group.add(info_row)

    def _get_command_permissions(self):
        try:
            return json.loads(self.settings.get_string("command-execution-permissions"))
        except Exception:
            return []

    def _save_command_permissions(self, rules):
        self.settings.set_string("command-execution-permissions", json.dumps(rules))
        from ..utility.command_permissions import CommandPermissionManager
        CommandPermissionManager.invalidate_cache()

    def _refresh_command_permissions_list(self):
        for row in self.cmd_permission_rows:
            self.cmd_perms_group.remove(row)
        self.cmd_permission_rows = []

        rules = self._get_command_permissions()
        for idx, rule in enumerate(rules):
            row = self._create_command_permission_row(rule, idx)
            self.cmd_perms_group.add(row)
            self.cmd_permission_rows.append(row)

    def _create_command_permission_row(self, rule, idx):
        pattern = rule.get("pattern", "")
        action = rule.get("action", "ask")

        display_pattern = pattern if pattern else _("(empty pattern)")
        action_labels = {"allow": _("Allow"), "ask": _("Ask"), "block": _("Block")}
        display_action = action_labels.get(action, action)

        row = Adw.ExpanderRow(title=display_pattern, subtitle=display_action)
        prefix_icon = Gtk.Image(icon_name="system-run-symbolic", css_classes=["dim-label"])
        row.add_prefix(prefix_icon)

        pattern_row = Adw.EntryRow(title=_("Pattern (regex)"), text=pattern)
        pattern_row.connect("changed", self._on_cmd_permission_pattern_changed, idx)
        row.add_row(pattern_row)

        action_row = Adw.ActionRow(title=_("Action"))
        action_combo = Gtk.ComboBoxText()
        for label, value in [("Allow", "allow"), ("Ask", "ask"), ("Block", "block")]:
            action_combo.append(value, label)
        action_combo.set_active_id(action if action else "ask")
        action_combo.set_valign(Gtk.Align.CENTER)
        action_combo.connect("changed", self._on_cmd_permission_action_changed, idx)
        action_row.add_suffix(action_combo)
        row.add_row(action_row)

        remove_row = Adw.ActionRow(title=_("Remove rule"))
        remove_button = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        remove_button.connect("clicked", self._on_remove_cmd_permission_clicked, idx)
        remove_row.add_suffix(remove_button)
        row.add_row(remove_row)

        return row

    def _on_cmd_permission_pattern_changed(self, entry, idx):
        rules = self._get_command_permissions()
        if idx < len(rules):
            rules[idx]["pattern"] = entry.get_text()
            self._save_command_permissions(rules)

    def _on_cmd_permission_action_changed(self, combo, idx):
        rules = self._get_command_permissions()
        if idx < len(rules):
            rules[idx]["action"] = combo.get_active_id()
            self._save_command_permissions(rules)
            self._refresh_command_permissions_list()

    def _on_add_command_permission_clicked(self, button):
        rules = self._get_command_permissions()
        rules.append({"pattern": "", "action": "ask"})
        self._save_command_permissions(rules)
        self._refresh_command_permissions_list()

    def _on_remove_cmd_permission_clicked(self, button, idx):
        rules = self._get_command_permissions()
        if idx < len(rules):
            rules.pop(idx)
            self._save_command_permissions(rules)
            self._refresh_command_permissions_list()

    def _on_default_risk_changed(self, combo):
        self.settings.set_string("default-risk-level", combo.get_active_id() or "ask")
        from ..utility.command_permissions import CommandPermissionManager
        CommandPermissionManager.invalidate_cache()

    # --- Path Security Levels ---

    def build_path_security_settings(self):
        self.path_security_group = Adw.PreferencesGroup(
            title=_("Path Security Levels"),
            description=_("Set trust levels for directories — affects auto-run behavior")
        )
        self.PermissionsPage.add(self.path_security_group)

        add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        add_button.set_tooltip_text(_("Add path security rule"))
        add_button.connect("clicked", self._on_add_path_security_clicked)
        self.path_security_group.set_header_suffix(add_button)

        self.path_security_rows = []
        self._refresh_path_security_list()

    def _get_path_security(self):
        try:
            return json.loads(self.settings.get_string("path-security-levels"))
        except Exception:
            return [
                {"path": "{{main_path}}", "level": "trusted"},
                {"path": "/tmp", "level": "sandboxed"},
            ]

    def _save_path_security(self, rules):
        self.settings.set_string("path-security-levels", json.dumps(rules))
        from ..utility.command_permissions import CommandPermissionManager
        CommandPermissionManager.invalidate_cache()

    def _refresh_path_security_list(self):
        for row in self.path_security_rows:
            self.path_security_group.remove(row)
        self.path_security_rows = []

        rules = self._get_path_security()
        for idx, rule in enumerate(rules):
            row = self._create_path_security_row(rule, idx)
            self.path_security_group.add(row)
            self.path_security_rows.append(row)

    def _display_name_for_security_path(self, path):
        if path == "{{main_path}}":
            return _("Current Work Directory")
        return path

    def _create_path_security_row(self, rule, idx):
        path = rule.get("path", "")
        level = rule.get("level", "sandboxed")
        display_path = self._display_name_for_security_path(path)
        is_builtin = path == "{{main_path}}"
        level_labels = {"yolo": _("YOLO"), "trusted": _("Trusted"), "sandboxed": _("Sandboxed"), "restricted": _("Restricted")}
        display_level = level_labels.get(level, level)

        icon_name = "folder-visiting-symbolic" if is_builtin else "folder-symbolic"
        row = Adw.ExpanderRow(title=display_path, subtitle=display_level)
        prefix_icon = Gtk.Image(icon_name=icon_name, css_classes=["dim-label"])
        row.add_prefix(prefix_icon)

        if not is_builtin:
            path_row = Adw.EntryRow(title=_("Path"), text=path)
            path_row.connect("changed", self._on_path_security_path_changed, idx)
            row.add_row(path_row)

        security_row = Adw.ActionRow(title=_("Security level"))
        security_combo = Gtk.ComboBoxText()
        for label, value in [("YOLO", "yolo"), ("Trusted", "trusted"), ("Sandboxed", "sandboxed"), ("Restricted", "restricted")]:
            security_combo.append(value, label)
        security_combo.set_active_id(level if level else "sandboxed")
        security_combo.set_valign(Gtk.Align.CENTER)
        security_combo.connect("changed", self._on_path_security_level_changed, idx)
        security_row.add_suffix(security_combo)
        row.add_row(security_row)

        desc_row = Adw.ActionRow(title=_("Effect"))
        level_descriptions = {
            "yolo": _("All commands are auto-executed without confirmation"),
            "trusted": _("Commands classified as safe are auto-run"),
            "sandboxed": _("All commands require confirmation"),
            "restricted": _("No commands are auto-run, even safe ones"),
        }
        desc_row.set_subtitle(level_descriptions.get(level, ""))
        row.add_row(desc_row)

        if not is_builtin:
            remove_row = Adw.ActionRow(title=_("Remove rule"))
            remove_button = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
            remove_button.connect("clicked", self._on_remove_path_security_clicked, idx)
            remove_row.add_suffix(remove_button)
            row.add_row(remove_row)

        return row

    def _on_path_security_path_changed(self, entry, idx):
        rules = self._get_path_security()
        if idx < len(rules):
            rules[idx]["path"] = entry.get_text()
            self._save_path_security(rules)
            self._refresh_path_security_list()

    def _on_path_security_level_changed(self, combo, idx):
        rules = self._get_path_security()
        if idx < len(rules):
            rules[idx]["level"] = combo.get_active_id()
            self._save_path_security(rules)
            self._refresh_path_security_list()

    def _on_add_path_security_clicked(self, button):
        dialog = Gtk.FileDialog(title=_("Select Directory"))
        dialog.select_folder(self, None, self._on_path_security_folder_selected)

    def _on_path_security_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return

        path = folder.get_path()
        rules = self._get_path_security()

        for rule in rules:
            rule_path = rule.get("path", "")
            if rule_path == "{{main_path}}":
                try:
                    main_path = self.settings.get_string("path")
                    rule_path = os.path.expanduser(main_path)
                except Exception:
                    pass
            if rule_path == path:
                toast = Adw.Toast(title=_("A rule for this directory already exists"))
                self.add_toast(toast)
                return

        rules.append({"path": path, "level": "sandboxed"})
        self._save_path_security(rules)
        self._refresh_path_security_list()

    def _on_remove_path_security_clicked(self, button, idx):
        rules = self._get_path_security()
        if idx < len(rules):
            removed = rules.pop(idx)
            self._save_path_security(rules)
            self._refresh_path_security_list()
            toast = Adw.Toast(title=_("Rule for '{}' removed").format(removed.get("path", "")))
            self.add_toast(toast)

    # --- Skills settings ---

    def build_skills_settings(self):
        if self.skills_page_initialized:
            return
        self.skills_page_initialized = True

        self.skills_marketplace_initialized = False
        self.skills_creator_initialized = False
        self.skills_tabs_group = Adw.PreferencesGroup()
        self.skills_tabs_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
        )
        self.skills_view_stack = Adw.ViewStack(vhomogeneous=False)
        self.skills_view_stack.connect(
            "notify::visible-child-name",
            self._on_skills_tab_changed,
        )

        self.installed_skills_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.marketplace_skills_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.create_skills_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.skills_view_stack.add_titled_with_icon(
            self.installed_skills_page,
            name="installed",
            title=_("Installed"),
            icon_name="skills-symbolic",
        )
        self.skills_view_stack.add_titled_with_icon(
            self.marketplace_skills_page,
            name="marketplace",
            title=_("Marketplace"),
            icon_name="folder-download-symbolic",
        )
        self.skills_view_stack.add_titled_with_icon(
            self.create_skills_page,
            name="create",
            title=_("Create"),
            icon_name="document-edit-symbolic",
        )

        self.skills_view_switcher = Adw.ViewSwitcher(
            stack=self.skills_view_stack,
            policy=Adw.ViewSwitcherPolicy.WIDE,
            halign=Gtk.Align.CENTER,
        )
        self.skills_tabs_box.append(self.skills_view_switcher)
        self.skills_tabs_box.append(self.skills_view_stack)
        self.skills_tabs_group.add(self.skills_tabs_box)
        self.SkillsPage.add(self.skills_tabs_group)

        self.skills_group = Adw.PreferencesGroup(
            title=_("Installed Skills"),
            description=_("Manage Agent Skills (SKILL.md files)")
        )
        self.installed_skills_page.append(self.skills_group)

        actions_row = Adw.ActionRow(title=_("Skills folder"), subtitle=self.controller.skills_path)
        open_button = Gtk.Button(icon_name="folder-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        open_button.set_tooltip_text(_("Open skills folder"))
        open_button.connect("clicked", lambda btn: open_folder(self.controller.skills_path))
        actions_row.add_suffix(open_button)

        add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        add_button.set_tooltip_text(_("Add skill from folder"))
        add_button.connect("clicked", self._on_add_skill_clicked)
        actions_row.add_suffix(add_button)

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        refresh_button.set_tooltip_text(_("Refresh skills"))
        refresh_button.connect("clicked", self._on_refresh_skills_clicked)
        actions_row.add_suffix(refresh_button)

        self.skills_group.add(actions_row)

        self.skills_rows = []
        self.refresh_skills_list()
        self.skills_view_stack.set_visible_child_name("installed")

    def _on_skills_tab_changed(self, stack, _pspec):
        visible_page = stack.get_visible_child_name()
        if visible_page == "marketplace" and not self.skills_marketplace_initialized:
            self.skills_marketplace_initialized = True
            from .skills_catalog import SkillsCatalogView

            self.skills_catalog_group = Adw.PreferencesGroup(
                title=_("Skills Marketplace"),
                description=_("Search and install community skills from SkillsMP"),
            )
            self.skills_catalog = SkillsCatalogView(
                parent=self,
                controller=self.controller,
                on_installed=self._on_catalog_skill_installed,
            )
            self.skills_catalog_group.add(self.skills_catalog)
            self.marketplace_skills_page.append(self.skills_catalog_group)
        elif visible_page == "create" and not self.skills_creator_initialized:
            self._ensure_skill_creator()

    def _make_weak_skills_refresh_callback(self):
        settings_ref = weakref.ref(self)

        def refresh_if_open():
            settings = settings_ref()
            if settings is not None and settings.get_visible():
                settings.refresh_skills_list()

        return refresh_if_open

    def _ensure_skill_creator(self):
        if self.skills_creator_initialized:
            return self.skills_creator
        self.skills_creator_initialized = True
        from .skill_creator import SkillCreatorView

        self.skills_creator = SkillCreatorView(
            host=self,
            controller=self.controller,
            on_saved=self._make_weak_skills_refresh_callback(),
            on_open_window=self._on_open_skill_creator_window,
        )
        self.create_skills_page.append(self.skills_creator)
        return self.skills_creator

    def _register_skill_editor_window(self):
        self._open_skill_editor_count = (
            getattr(self, "_open_skill_editor_count", 0) + 1
        )
        self.set_modal(False)
        settings_ref = weakref.ref(self)

        def editor_closed():
            settings = settings_ref()
            if settings is None:
                return
            settings._open_skill_editor_count = max(
                0,
                getattr(settings, "_open_skill_editor_count", 1) - 1,
            )
            if settings._open_skill_editor_count == 0 and settings.get_visible():
                settings.set_modal(True)

        return editor_closed

    def _on_open_skill_creator_window(self, editor):
        from .skill_creator import SkillEditorWindow

        self.create_skills_page.remove(editor)
        editor.set_open_window_callback(None)

        placeholder = Adw.PreferencesGroup()
        placeholder_row = Adw.ActionRow(
            title=_("Skill editor is open in another window"),
            subtitle=_("The editor keeps working if you close Settings."),
        )
        placeholder_row.add_prefix(
            Gtk.Image(icon_name="document-edit-symbolic")
        )
        present_button = Gtk.Button(
            label=_("Show Editor"),
            icon_name="window-new-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["suggested-action"],
        )
        placeholder_row.add_suffix(present_button)
        placeholder.add(placeholder_row)
        self.skill_creator_placeholder = placeholder
        self.create_skills_page.append(placeholder)

        settings_ref = weakref.ref(self)

        def return_editor(returned_editor):
            settings = settings_ref()
            if settings is None or not settings.get_visible():
                return False
            settings._reattach_skill_creator(returned_editor)
            return True

        window = SkillEditorWindow(
            application=self.app,
            editor=editor,
            return_editor=return_editor,
            on_closed=self._register_skill_editor_window(),
        )
        self.skill_editor_window = window
        present_button.connect("clicked", lambda _button: window.present())
        window.present()

    def _reattach_skill_creator(self, editor):
        placeholder = getattr(self, "skill_creator_placeholder", None)
        if placeholder is not None and placeholder.get_parent() is not None:
            self.create_skills_page.remove(placeholder)
        editor.set_host(self)
        editor.set_windowed(False)
        editor.set_open_window_callback(self._on_open_skill_creator_window)
        self.create_skills_page.append(editor)
        self.skills_creator = editor
        self.skill_editor_window = None
        self.refresh_skills_list()

    def _on_edit_skill_clicked(self, _button, skill):
        from .skill_creator import SkillCreatorView, SkillEditorWindow

        editor = SkillCreatorView(
            host=None,
            controller=self.controller,
            on_saved=self._make_weak_skills_refresh_callback(),
        )
        window = SkillEditorWindow(
            application=self.app,
            editor=editor,
            on_closed=self._register_skill_editor_window(),
            title=_("Edit {}").format(skill.name),
        )
        if editor.load_skill(skill):
            window.present()
        else:
            window.close()
            self.add_toast(Adw.Toast(title=_("Could not open skill for editing")))

    def refresh_skills_list(self):
        for row in self.skills_rows:
            self.skills_group.remove(row)
        self.skills_rows = []

        skill_manager = self.controller.skill_manager
        for skill in skill_manager.skills.values():
            row = self._create_skill_row(skill)
            self.skills_group.add(row)
            self.skills_rows.append(row)

    def _create_skill_row(self, skill):
        row = Adw.ExpanderRow(title=skill.name, subtitle=skill.description)
        icon = Gtk.Image(icon_name="skills-symbolic", css_classes=["dim-label"])
        row.add_prefix(icon)

        toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        toggle.set_active(self.controller.skill_manager.is_skill_enabled(skill.name))
        toggle.connect("state-set", self._on_skill_toggled, skill.name)
        row.add_suffix(toggle)

        info_row = Adw.ActionRow(title=_("Location"), subtitle=skill.location)
        edit_button = Gtk.Button(
            label=_("Edit"),
            icon_name="document-edit-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        edit_button.set_tooltip_text(_("Edit skill"))
        edit_button.connect("clicked", self._on_edit_skill_clicked, skill)
        info_row.add_suffix(edit_button)
        open_button = Gtk.Button(
            icon_name="folder-visiting-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        open_button.set_tooltip_text(_("Open skill folder"))
        open_button.connect(
            "clicked",
            lambda _button, path=skill.base_dir: open_folder(path),
        )
        info_row.add_suffix(open_button)
        row.add_row(info_row)

        resource_row = Adw.ActionRow(
            title=_("Bundled resources"),
            subtitle=_("Expand to count files"),
        )
        row.add_row(resource_row)
        row.connect(
            "notify::expanded",
            self._on_skill_row_expanded,
            skill,
            resource_row,
        )

        remove_row = Adw.ActionRow(title=_("Remove skill"))
        remove_button = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        remove_button.connect("clicked", self._on_remove_skill_clicked, skill.name)
        remove_row.add_suffix(remove_button)
        row.add_row(remove_row)

        return row

    def _on_skill_row_expanded(self, row, _pspec, skill, resource_row):
        if not row.get_expanded() or getattr(row, "_resources_loading", False):
            return
        row._resources_loading = True
        resource_row.set_subtitle(_("Counting files…"))

        def worker():
            count = len(self.controller.skill_manager._list_resources(skill.base_dir))
            GLib.idle_add(self._finish_skill_resource_count, resource_row, count)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_skill_resource_count(self, resource_row, count):
        resource_row.set_subtitle(
            str(count) + " " + (_("files") if count != 1 else _("file"))
        )
        return False

    def _on_catalog_skill_installed(self):
        self.refresh_skills_list()

    def _on_skill_toggled(self, switch, state, skill_name):
        self.controller.skill_manager.set_skill_enabled(skill_name, state)

    def _on_add_skill_clicked(self, button):
        dialog = Gtk.FileDialog(title=_("Select Skill Folder"))
        dialog.select_folder(self, None, self._on_skill_folder_selected)

    def _on_skill_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return

        path = folder.get_path()
        skill_md = os.path.join(path, "SKILL.md")
        if not os.path.isfile(skill_md):
            toast = Adw.Toast(title=_("Selected folder does not contain a SKILL.md file"))
            self.add_toast(toast)
            return

        skill = self.controller.skill_manager.add_skill_from_path(path)
        if skill is not None:
            self.refresh_skills_list()
            toast = Adw.Toast(title=_("Skill '{}' added").format(skill.name))
            self.add_toast(toast)
        else:
            toast = Adw.Toast(title=_("Failed to add skill"))
            self.add_toast(toast)

    def _on_remove_skill_clicked(self, button, skill_name):
        if self.controller.skill_manager.remove_skill(skill_name):
            self.refresh_skills_list()
            toast = Adw.Toast(title=_("Skill '{}' removed").format(skill_name))
            self.add_toast(toast)

    def _on_refresh_skills_clicked(self, button):
        self.controller.skill_manager.discover()
        self.refresh_skills_list()
        toast = Adw.Toast(title=_("Skills refreshed"))
        self.add_toast(toast)

    # --- MCP settings ---

    def _on_mcp_application_connected(self):
        self.refresh_mcp_servers_list()
        self.refresh_tools_list()

    def build_mcp_settings(self):
        if self.mcp_page_initialized:
            return
        self.mcp_page_initialized = True
        from .mcp_catalog import ConnectApplicationView

        self.mcp_catalog_group = Adw.PreferencesGroup(
            title=_("Connect Application"),
            description=_("Choose an application from the MCP catalog"),
        )
        self.mcp_catalog = ConnectApplicationView(
            parent=self,
            controller=self.controller,
            on_connected=self._on_mcp_application_connected,
        )
        self.mcp_catalog_group.add(self.mcp_catalog)

        self.mcp_group = Adw.PreferencesGroup(title=_("MCP Servers"), description=_("Manage Model Context Protocol servers"))
        self.MCPPage.add(self.mcp_group)
        
        # List of servers
        self.servers_list_group = Adw.ExpanderRow(title=_("Servers"), subtitle=_("List of configured MCP servers"))
        
        self.mcp_server_rows = []
        self.refresh_mcp_servers_list()
        
        # Add server form
        add_row = Adw.ExpanderRow(title=_("Add Server"), subtitle=_("Add a new MCP server"), icon_name="list-add-symbolic")
        
        # Server type selector. stdio spawns a local command: outside flatpak it
        # runs directly; inside flatpak it runs on the host via flatpak-spawn,
        # which requires sandbox escape (granted by the manifest's talk-name).
        self.mcp_server_type = "http"
        can_use_stdio = (not is_flatpak()) or can_escape_sandbox()
        
        if can_use_stdio:
            type_row = Adw.ActionRow(title=_("Server Type"), subtitle=_("HTTP or local command (stdio)"))
            type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
            
            self.mcp_type_http = Gtk.ToggleButton(label="HTTP", active=True)
            self.mcp_type_http.add_css_class("flat")
            self.mcp_type_stdio = Gtk.ToggleButton(label="Stdio", group=self.mcp_type_http)
            self.mcp_type_stdio.add_css_class("flat")
            
            type_box.append(self.mcp_type_http)
            type_box.append(self.mcp_type_stdio)
            type_row.add_suffix(type_box)
            add_row.add_row(type_row)
        
        # Title entry (optional) - common to both types
        title_row = Adw.ActionRow(title=_("Title"), subtitle=_("Display name for the server"))
        title_row.add_css_class("property")
        self.mcp_title_entry = Gtk.Entry(valign=Gtk.Align.CENTER, placeholder_text=_("My MCP Server"), hexpand=True, width_chars=30)
        title_row.add_suffix(self.mcp_title_entry)
        add_row.add_row(title_row)
        
        # === HTTP-specific fields ===
        self.mcp_http_rows = []
        
        # URL entry (required for HTTP)
        url_row = Adw.ActionRow(title=_("URL"), subtitle=_("Server endpoint URL (required)"))
        url_row.add_css_class("property")
        self.mcp_url_entry = Gtk.Entry(valign=Gtk.Align.CENTER, placeholder_text="http://localhost:8000/mcp", hexpand=True, width_chars=30)
        url_row.add_suffix(self.mcp_url_entry)
        add_row.add_row(url_row)
        self.mcp_http_rows.append(url_row)
        
        # Authentication section (nested expander for optional auth settings)
        auth_row = Adw.ExpanderRow(title=_("Authentication"), subtitle=_("Optional authentication settings"), icon_name="dialog-password-symbolic")
        
        # Auth method selector: None, Bearer token, OAuth (automatic)
        self.mcp_auth_method = "none"
        auth_method_row = Adw.ActionRow(title=_("Method"), subtitle=_("Authentication method"))
        auth_method_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        self.mcp_auth_none = Gtk.ToggleButton(label=_("None"), active=True)
        self.mcp_auth_none.add_css_class("flat")
        self.mcp_auth_bearer = Gtk.ToggleButton(label=_("Bearer Token"), group=self.mcp_auth_none)
        self.mcp_auth_bearer.add_css_class("flat")
        self.mcp_auth_oauth = Gtk.ToggleButton(label=_("OAuth (automatic)"), group=self.mcp_auth_none)
        self.mcp_auth_oauth.add_css_class("flat")
        auth_method_box.append(self.mcp_auth_none)
        auth_method_box.append(self.mcp_auth_bearer)
        auth_method_box.append(self.mcp_auth_oauth)
        auth_method_row.add_suffix(auth_method_box)
        auth_row.add_row(auth_method_row)
        
        # Bearer token entry (visible when Bearer Token selected)
        token_row = Adw.ActionRow(title=_("Bearer Token"), subtitle=_("Authentication token"))
        self.mcp_token_entry = Gtk.Entry(valign=Gtk.Align.CENTER, placeholder_text=_("Token"), visibility=False, hexpand=True, width_chars=25)
        token_row.add_suffix(self.mcp_token_entry)
        show_token_btn = Gtk.Button(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=_("Show/Hide token"))
        show_token_btn.connect("clicked", lambda btn: self.mcp_token_entry.set_visibility(not self.mcp_token_entry.get_visibility()))
        token_row.add_suffix(show_token_btn)
        auth_row.add_row(token_row)
        self.mcp_token_row = token_row
        
        # Client ID entry (visible when Bearer Token selected, for pre-registered OAuth)
        client_id_row = Adw.ActionRow(title=_("Client ID"), subtitle=_("OAuth client identifier (pre-registered)"))
        self.mcp_client_id_entry = Gtk.Entry(valign=Gtk.Align.CENTER, placeholder_text=_("client-id"), hexpand=True, width_chars=25)
        client_id_row.add_suffix(self.mcp_client_id_entry)
        auth_row.add_row(client_id_row)
        self.mcp_client_id_row = client_id_row
        
        # OAuth info (visible when OAuth selected)
        oauth_info_row = Adw.ActionRow(title=_("OAuth"), subtitle=_("Uses Dynamic Client Registration. A browser window will open for authentication."))
        oauth_info_row.set_visible(False)
        auth_row.add_row(oauth_info_row)
        self.mcp_oauth_info_row = oauth_info_row
        
        def on_auth_method_changed(btn):
            if self.mcp_auth_none.get_active():
                self.mcp_auth_method = "none"
            elif self.mcp_auth_bearer.get_active():
                self.mcp_auth_method = "bearer"
            else:
                self.mcp_auth_method = "oauth"
            self.mcp_token_row.set_visible(self.mcp_auth_method == "bearer")
            self.mcp_client_id_row.set_visible(self.mcp_auth_method == "bearer")
            self.mcp_oauth_info_row.set_visible(self.mcp_auth_method == "oauth")
        
        self.mcp_auth_none.connect("toggled", on_auth_method_changed)
        self.mcp_auth_bearer.connect("toggled", on_auth_method_changed)
        self.mcp_auth_oauth.connect("toggled", on_auth_method_changed)
        on_auth_method_changed(None)
        
        add_row.add_row(auth_row)
        self.mcp_http_rows.append(auth_row)
        
        # Advanced section (nested expander for headers)
        advanced_row = Adw.ExpanderRow(title=_("Advanced"), subtitle=_("Custom headers and advanced settings"), icon_name="preferences-other-symbolic")
        
        # Custom headers (optional) - text view for JSON
        headers_row = Adw.ActionRow(title=_("Custom Headers"), subtitle=_("JSON format, e.g. {\"X-Api-Key\": \"value\"}"))
        headers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        
        # ScrolledWindow for text view
        headers_scroll = Gtk.ScrolledWindow(vexpand=False, hexpand=True, min_content_height=60, max_content_height=100)
        headers_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self.mcp_headers_text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
        self.mcp_headers_text.set_size_request(250, 60)
        self.mcp_headers_text.get_buffer().set_text("{}")
        headers_scroll.set_child(self.mcp_headers_text)
        headers_box.append(headers_scroll)
        
        headers_row.add_suffix(headers_box)
        advanced_row.add_row(headers_row)
        
        add_row.add_row(advanced_row)
        self.mcp_http_rows.append(advanced_row)
        
        # === Stdio-specific fields ===
        self.mcp_stdio_rows = []
        
        # Command entry (required for stdio)
        if is_flatpak():
            cmd_subtitle = _("Executable path or command name (runs on the host system)")
        else:
            cmd_subtitle = _("Executable path or command name")
        cmd_row = Adw.ActionRow(title=_("Command"), subtitle=cmd_subtitle)
        cmd_row.add_css_class("property")
        self.mcp_command_entry = Gtk.Entry(valign=Gtk.Align.CENTER, placeholder_text="npx", hexpand=True, width_chars=30)
        cmd_row.add_suffix(self.mcp_command_entry)
        add_row.add_row(cmd_row)
        self.mcp_stdio_rows.append(cmd_row)
        cmd_row.set_visible(False)
        
        # Arguments entry (optional for stdio)
        args_row = Adw.ActionRow(title=_("Arguments"), subtitle=_("Space-separated command arguments"))
        args_row.add_css_class("property")
        self.mcp_args_entry = Gtk.Entry(valign=Gtk.Align.CENTER, placeholder_text="-y @modelcontextprotocol/server-filesystem /path", hexpand=True, width_chars=30)
        args_row.add_suffix(self.mcp_args_entry)
        add_row.add_row(args_row)
        self.mcp_stdio_rows.append(args_row)
        args_row.set_visible(False)
        
        # Environment variables (optional for stdio)
        env_row = Adw.ExpanderRow(title=_("Environment Variables"), subtitle=_("Optional environment variables"), icon_name="utilities-terminal-symbolic")
        env_row.set_visible(False)
        
        env_inner_row = Adw.ActionRow(title=_("Variables"), subtitle=_("JSON format, e.g. {\"API_KEY\": \"value\"}"))
        env_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        
        env_scroll = Gtk.ScrolledWindow(vexpand=False, hexpand=True, min_content_height=60, max_content_height=100)
        env_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self.mcp_env_text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
        self.mcp_env_text.set_size_request(250, 60)
        self.mcp_env_text.get_buffer().set_text("{}")
        env_scroll.set_child(self.mcp_env_text)
        env_box.append(env_scroll)
        
        env_inner_row.add_suffix(env_box)
        env_row.add_row(env_inner_row)
        
        add_row.add_row(env_row)
        self.mcp_stdio_rows.append(env_row)
        
        # Toggle visibility based on server type
        if can_use_stdio:
            def on_type_changed(btn):
                is_stdio = self.mcp_type_stdio.get_active()
                self.mcp_server_type = "stdio" if is_stdio else "http"
                for row in self.mcp_http_rows:
                    row.set_visible(not is_stdio)
                for row in self.mcp_stdio_rows:
                    row.set_visible(is_stdio)
            
            self.mcp_type_http.connect("toggled", on_type_changed)
            self.mcp_type_stdio.connect("toggled", on_type_changed)
        
        # Add button row with spinner
        add_btn_row = Adw.ActionRow()
        add_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, halign=Gtk.Align.END, margin_top=6, margin_bottom=6)
        
        self.mcp_add_spinner = Gtk.Spinner()
        add_btn_box.append(self.mcp_add_spinner)
        
        self.mcp_add_button = Gtk.Button(label=_("Add Server"), valign=Gtk.Align.CENTER)
        self.mcp_add_button.add_css_class("suggested-action")
        self.mcp_add_button.add_css_class("pill")
        add_btn_box.append(self.mcp_add_button)
        
        add_btn_row.add_suffix(add_btn_box)
        add_row.add_row(add_btn_row)
        
        def add_server(btn):
            title = self.mcp_title_entry.get_text().strip() or None
            
            if self.mcp_server_type == "stdio":
                command = self.mcp_command_entry.get_text().strip()
                command = os.path.expanduser(command)
                if not command:
                    self.app.win.show_error_dialog(_("Error"), _("Command is required for stdio servers"), parent=self)
                    return
                
                args_text = self.mcp_args_entry.get_text().strip()
                args = args_text.split() if args_text else []
                
                # Parse environment variables
                env_buffer = self.mcp_env_text.get_buffer()
                env_text = env_buffer.get_text(env_buffer.get_start_iter(), env_buffer.get_end_iter(), False).strip()
                env = None
                if env_text and env_text != "{}":
                    try:
                        env = json.loads(env_text)
                        if not isinstance(env, dict):
                            self.app.win.show_error_dialog(_("Error"), _("Environment variables must be a JSON object"), parent=self)
                            return
                    except json.JSONDecodeError as e:
                        self.app.win.show_error_dialog(_("Error"), _("Invalid JSON in environment variables: ") + str(e), parent=self)
                        return
                
                self._disable_mcp_form()
                
                def add_thread():
                    try:
                        mcp_handler = self.controller.get_mcp_integration()
                        added = mcp_handler.add_mcp_server(
                            title=title,
                            server_type="stdio",
                            command=command,
                            args=args,
                            env=env
                        )
                        self.settings.set_string("mcp-servers", json.dumps(mcp_handler.mcp_servers))
                        if not added:
                            GLib.idle_add(self.app.win.show_error_dialog, _("Error"), _("Failed to add MCP server"), self)
                        GLib.idle_add(self.refresh_mcp_servers_list)
                        GLib.idle_add(self.refresh_tools_list)
                    except Exception as e:
                        traceback.print_exc()
                        err_msg = self._mcp_error_message(e)
                        GLib.idle_add(self.app.win.show_error_dialog, _("Error"), _("Failed to add MCP server: {}").format(err_msg), self)
                    finally:
                        GLib.idle_add(self._enable_mcp_form)
                        GLib.idle_add(self._clear_mcp_form)
                t = threading.Thread(target=add_thread)
                t.start()
            else:
                url = self.mcp_url_entry.get_text().strip()
                if not url:
                    self.app.win.show_error_dialog(_("Error"), _("URL is required for HTTP servers"), parent=self)
                    return
                
                bearer_token = self.mcp_token_entry.get_text().strip() or None
                client_id = self.mcp_client_id_entry.get_text().strip() or None
                oauth_mode = self.mcp_auth_method == "oauth"
                
                # Parse custom headers
                headers_buffer = self.mcp_headers_text.get_buffer()
                headers_text = headers_buffer.get_text(headers_buffer.get_start_iter(), headers_buffer.get_end_iter(), False).strip()
                custom_headers = None
                if headers_text and headers_text != "{}":
                    try:
                        custom_headers = json.loads(headers_text)
                        if not isinstance(custom_headers, dict):
                            self.app.win.show_error_dialog(_("Error"), _("Custom headers must be a JSON object"), parent=self)
                            return
                    except json.JSONDecodeError as e:
                        self.app.win.show_error_dialog(_("Error"), _("Invalid JSON in custom headers: ") + str(e), parent=self)
                        return
                
                self._disable_mcp_form()
                
                def add_thread():
                    try:
                        mcp_handler = self.controller.get_mcp_integration()
                        if oauth_mode:
                            from ..integrations.mcp_oauth import run_oauth_flow
                            config_dir = self.controller.config_dir
                            success, err_msg = run_oauth_flow(url, config_dir)
                            if not success:
                                GLib.idle_add(self.app.win.show_error_dialog, _("OAuth Error"), err_msg or _("Authentication failed"), self)
                                return
                        added = mcp_handler.add_mcp_server(
                            url=url,
                            title=title,
                            bearer_token=bearer_token if not oauth_mode else None,
                            client_id=client_id if not oauth_mode else None,
                            custom_headers=custom_headers,
                            server_type="http",
                            oauth_mode=oauth_mode
                        )
                        self.settings.set_string("mcp-servers", json.dumps(mcp_handler.mcp_servers))
                        if not added:
                            GLib.idle_add(self.app.win.show_error_dialog, _("Error"), _("Failed to add MCP server"), self)
                        GLib.idle_add(self.refresh_mcp_servers_list)
                        GLib.idle_add(self.refresh_tools_list)
                    except Exception as e:
                        err_msg = str(e)
                        cause = getattr(e, "__cause__", None)
                        if cause:
                            err_msg = str(cause)
                        for attr in ("exceptions", "__context__"):
                            inner = getattr(e, attr, None)
                            if inner and isinstance(inner, (list, tuple)) and len(inner) > 0:
                                err_msg = str(inner[0])
                                break
                            elif inner:
                                err_msg = str(inner)
                                break
                        if "401" in err_msg or "Unauthorized" in err_msg:
                            err_msg = _(
                                "This server requires authentication. Select 'OAuth (automatic)' in the "
                                "Authentication section and try again to sign in with your browser, "
                                "or provide a Bearer token if you have one."
                            )
                        else:
                            err_msg = _("Failed to add MCP server: {}").format(err_msg)
                        GLib.idle_add(self.app.win.show_error_dialog, _("Error"), err_msg, self)
                    finally:
                        GLib.idle_add(self._enable_mcp_form)
                        GLib.idle_add(self._clear_mcp_form)
                t = threading.Thread(target=add_thread)
                t.start()
        
        self.mcp_add_button.connect("clicked", add_server)
        self.mcp_group.add(add_row)
        self.mcp_group.add(self.servers_list_group)
        self.MCPPage.add(self.mcp_catalog_group)
    
    def _disable_mcp_form(self):
        """Disable all MCP form fields"""
        self.mcp_add_button.set_sensitive(False)
        self.mcp_add_spinner.start()
        self.mcp_url_entry.set_sensitive(False)
        self.mcp_title_entry.set_sensitive(False)
        self.mcp_token_entry.set_sensitive(False)
        self.mcp_client_id_entry.set_sensitive(False)
        self.mcp_headers_text.set_sensitive(False)
        self.mcp_command_entry.set_sensitive(False)
        self.mcp_args_entry.set_sensitive(False)
        self.mcp_env_text.set_sensitive(False)
    
    def _enable_mcp_form(self):
        """Enable all MCP form fields"""
        self.mcp_add_button.set_sensitive(True)
        self.mcp_add_spinner.stop()
        self.mcp_url_entry.set_sensitive(True)
        self.mcp_title_entry.set_sensitive(True)
        self.mcp_token_entry.set_sensitive(True)
        self.mcp_client_id_entry.set_sensitive(True)
        self.mcp_headers_text.set_sensitive(True)
        self.mcp_command_entry.set_sensitive(True)
        self.mcp_args_entry.set_sensitive(True)
        self.mcp_env_text.set_sensitive(True)
    
    def _clear_mcp_form(self):
        """Clear all MCP form fields"""
        self.mcp_url_entry.set_text("")
        self.mcp_title_entry.set_text("")
        self.mcp_token_entry.set_text("")
        self.mcp_client_id_entry.set_text("")
        self.mcp_headers_text.get_buffer().set_text("{}")
        self.mcp_command_entry.set_text("")
        self.mcp_args_entry.set_text("")
        self.mcp_env_text.get_buffer().set_text("{}")

    def _mcp_error_message(self, exc):
        """Extract a readable message from an MCP exception.

        asyncio wraps failures in an ExceptionGroup, which formats as
        'TaskGroup (1 sub exception)'. Recurse into any nested groups to surface
        the real underlying cause (e.g. FileNotFoundError for a missing command)
        instead of that opaque wrapper.
        """
        try:
            group_types = (ExceptionGroup, BaseExceptionGroup)
        except NameError:  # Python < 3.11
            group_types = ()
        messages = []
        seen = set()

        def walk(e):
            if id(e) in seen:
                return
            seen.add(id(e))
            if group_types and isinstance(e, group_types):
                for sub in e.exceptions:
                    walk(sub)
                return
            text = str(e).strip()
            if not text:
                text = type(e).__name__
            missing_command_text = text.casefold()
            if isinstance(e, FileNotFoundError) or any(
                marker in missing_command_text
                for marker in ("no such file or directory", "command not found")
            ):
                from .mcp_catalog import _missing_command_name

                command = _missing_command_name(e, "the configured command")
                text = _(
                    "The MCP server could not start because the command '{}' was not found. "
                    "Install it or add it to PATH, then try again."
                ).format(command)
            if text not in messages:
                messages.append(text)

        walk(exc)
        return "; ".join(messages) if messages else str(exc)

    def _get_mcp_cached_tool_count(self, identifier):
        """Return the number of cached tools for a given server identifier."""
        mcp_handler = self.controller.get_mcp_integration()
        if mcp_handler is None:
            return 0
        count = 0
        for tool in mcp_handler.tools:
            info = mcp_handler.tools_dict.get(tool.name, {})
            if mcp_handler._get_server_identifier(info) == identifier:
                count += 1
        return count

    def _refresh_single_mcp_server(self, btn, spinner, identifier):
        """Re-fetch tools for a single server and update the cache."""
        btn.set_sensitive(False)
        spinner.start()

        def refresh_thread():
            mcp_handler = self.controller.get_mcp_integration()
            if mcp_handler is None:
                GLib.idle_add(spinner.stop)
                GLib.idle_add(btn.set_sensitive, True)
                return
            # Remove old tools for this server
            mcp_handler.tools = [
                t for t in mcp_handler.tools
                if mcp_handler._get_server_identifier(mcp_handler.tools_dict.get(t.name, {})) != identifier
            ]
            mcp_handler.tools_dict = {
                k: v for k, v in mcp_handler.tools_dict.items()
                if mcp_handler._get_server_identifier(v) != identifier
            }
            # Re-fetch for the matching server
            for server in mcp_handler.mcp_servers:
                server_info = mcp_handler._get_server_info(server)
                if mcp_handler._get_server_identifier(server_info) == identifier:
                    try:
                        if server_info.get("type") == "stdio":
                            tools = mcp_handler.sync_get_tools_stdio(
                                server_info["command"],
                                server_info.get("args") or [],
                                server_info.get("env"),
                            )
                        else:
                            tools = mcp_handler.sync_get_tools(
                                server_info["url"],
                                server_info=server_info,
                                client_id=server_info.get("client_id")
                            )
                        mcp_handler.tools.extend(tools)
                        for tool in tools:
                            mcp_handler.tools_dict[tool.name] = server_info
                    except Exception as e:
                        print(f"Refresh error for {identifier}: {e}")
                    break
            mcp_handler._save_cache()
            if hasattr(mcp_handler, "ui_controller"):
                GLib.idle_add(mcp_handler.ui_controller.require_tool_update)
            GLib.idle_add(self.refresh_mcp_servers_list)
            GLib.idle_add(self.refresh_tools_list)
            GLib.idle_add(spinner.stop)
            GLib.idle_add(btn.set_sensitive, True)

        threading.Thread(target=refresh_thread, daemon=True).start()

    def _reauth_oauth_mcp_server(self, btn, identifier, url):
        """Re-run OAuth flow for an OAuth-protected MCP server."""
        if not url:
            return
        btn.set_sensitive(False)

        def reauth_thread():
            from ..integrations.mcp_oauth import run_oauth_flow
            config_dir = self.controller.config_dir
            success, err_msg = run_oauth_flow(url, config_dir)
            if success:
                mcp_handler = self.controller.get_mcp_integration()
                if mcp_handler:
                    mcp_handler.tools = [
                        t for t in mcp_handler.tools
                        if mcp_handler._get_server_identifier(mcp_handler.tools_dict.get(t.name, {})) != identifier
                    ]
                    mcp_handler.tools_dict = {
                        k: v for k, v in mcp_handler.tools_dict.items()
                        if mcp_handler._get_server_identifier(v) != identifier
                    }
                    for server in mcp_handler.mcp_servers:
                        server_info = mcp_handler._get_server_info(server)
                        if mcp_handler._get_server_identifier(server_info) == identifier:
                            try:
                                tools = mcp_handler.sync_get_tools(url, server_info=server_info)
                                mcp_handler.tools.extend(tools)
                                for tool in tools:
                                    mcp_handler.tools_dict[tool.name] = server_info
                            except Exception as e:
                                print(f"Re-auth refresh error: {e}")
                            break
                    mcp_handler._save_cache()
                    if hasattr(mcp_handler, "ui_controller"):
                        GLib.idle_add(mcp_handler.ui_controller.require_tool_update)
                GLib.idle_add(self.refresh_mcp_servers_list)
                GLib.idle_add(self.refresh_tools_list)
                GLib.idle_add(lambda: self.add_toast(Adw.Toast(title=_("Re-authentication successful"))))
            else:
                GLib.idle_add(self.app.win.show_error_dialog, _("OAuth Error"), err_msg or _("Re-authentication failed"), self)
            GLib.idle_add(btn.set_sensitive, True)

        threading.Thread(target=reauth_thread, daemon=True).start()

    def refresh_mcp_servers_list(self):
        for row in self.mcp_server_rows:
             self.servers_list_group.remove(row)
        self.mcp_server_rows = []

        servers = json.loads(self.settings.get_string("mcp-servers"))
        self.controller.newelle_settings.mcp_servers_dict = servers
        for server in servers:
            # Handle old format (string URL), HTTP, and stdio servers
            if isinstance(server, str):
                identifier = server
                title = server[:30]
                subtitle = None
                server_type = "http"
            else:
                server_type = server.get("type", "http")
                if server_type == "stdio":
                    command = server.get("command", "")
                    args = server.get("args", [])
                    identifier = f"stdio:{command}:{':'.join(args)}"
                    title = server.get("title") or command
                    subtitle = f"stdio: {command} {' '.join(args)}"[:50]
                else:
                    identifier = server.get("url", "")
                    title = server.get("title") or identifier[:30]
                    subtitle = identifier if title != identifier[:30] else None
            
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            
            # Cached tool count badge
            tool_count = self._get_mcp_cached_tool_count(identifier)
            if tool_count > 0:
                count_label = Gtk.Label(
                    label=_("{0} tools").format(tool_count),
                    valign=Gtk.Align.CENTER,
                )
                count_label.add_css_class("dim-label")
                count_label.add_css_class("caption")
                row.add_suffix(count_label)

            # Type indicator
            type_label = Gtk.Label(label=server_type.upper(), valign=Gtk.Align.CENTER)
            type_label.add_css_class("dim-label")
            type_label.add_css_class("caption")
            row.add_suffix(type_label)

            # Refresh button with spinner
            refresh_spinner = Gtk.Spinner()
            row.add_suffix(refresh_spinner)
            refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=_("Refresh tools"))
            refresh_btn.connect("clicked", self._refresh_single_mcp_server, refresh_spinner, identifier)
            row.add_suffix(refresh_btn)
            
            # Re-authenticate button for OAuth servers
            is_oauth = isinstance(server, dict) and server.get("oauth_mode")
            if is_oauth:
                reauth_btn = Gtk.Button(icon_name="emblem-locked-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=_("Re-authenticate"))
                reauth_btn.connect("clicked", self._reauth_oauth_mcp_server, identifier, server.get("url", ""))
                row.add_suffix(reauth_btn)
            
            delete_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            delete_btn.add_css_class("destructive-action")
            delete_btn.connect("clicked", self.remove_mcp_server, server, identifier)
            row.add_suffix(delete_btn)
            self.servers_list_group.add_row(row)
            self.mcp_server_rows.append(row)

    def remove_mcp_server(self, btn, server, identifier):
        servers = self.controller.newelle_settings.mcp_servers_dict
        servers.remove(server)
        self.settings.set_string("mcp-servers", json.dumps(servers))
        self.controller.newelle_settings.mcp_servers_dict = servers
        mcp_handler = self.controller.get_mcp_integration()
        mcp_handler.remove_mcp_server(identifier)
        self.refresh_mcp_servers_list()
        self.refresh_tools_list()

    def _get_active_mode_name(self):
        """Return the active mode name, or None when the Normal mode is active."""
        mode_manager = getattr(self.controller, "mode_manager", None)
        if mode_manager is None:
            return None
        name = mode_manager.get_active_mode_name()
        if name == DEFAULT_MODE_NAME:
            return None
        return name

    def _get_prompt_mode_warning(self, prompt_key, base_enabled):
        """Build the mode warning for a prompt row, None if the active mode does not change it."""
        mode_name = self._get_active_mode_name()
        if mode_name is None:
            return None
        mode_manager = self.controller.mode_manager
        base_text = self.prompts.get(prompt_key, "")
        resolved_enabled = mode_manager.resolve_prompt_enabled(prompt_key, base_enabled)
        resolved_text = mode_manager.resolve_prompt_text(prompt_key, base_text)
        details = []
        if resolved_enabled != base_enabled:
            details.append(_("forced on") if resolved_enabled else _("forced off"))
        if resolved_text != base_text:
            details.append(_("text replaced"))
        if not details:
            return None
        return _('The "{}" mode overrides this prompt: {}').format(mode_name, ", ".join(details))

    def _get_tool_mode_warning(self, tool_name, base_enabled):
        """Build the mode warning for a tool row, None if the active mode does not change it."""
        mode_name = self._get_active_mode_name()
        if mode_name is None:
            return None
        resolved_enabled = self.controller.mode_manager.resolve_tool_enabled(tool_name, base_enabled)
        if resolved_enabled == base_enabled:
            return None
        detail = _("forced on") if resolved_enabled else _("forced off")
        return _('The "{}" mode overrides this tool: {}').format(mode_name, detail)

    def _add_mode_warning_suffix(self, row, tooltip):
        """Add a small warning icon to a row, hinting the active mode overrides its value."""
        icon = Gtk.Image(icon_name="warning-outline-symbolic", css_classes=["warning"])
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_tooltip_text(tooltip)
        row.add_suffix(icon)

    def build_prompts_settings(self):
        self.prompts_settings = self.controller.newelle_settings.prompts_settings
        for prompt in self.prompts_rows:
            self.prompt.remove(prompt)
        self.prompts_rows = []

        ordered_prompts = self._get_ordered_prompts()
        for prompt in ordered_prompts:
            is_active = False
            if prompt["setting_name"] in self.prompts_settings:
                is_active = self.prompts_settings[prompt["setting_name"]]
            else:
                is_active = prompt["default"]
            if not prompt["show_in_settings"]:
                continue
            row = Adw.ExpanderRow(title=prompt["title"], subtitle=prompt["description"])

            drag_handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
            drag_handle.add_css_class("dim-label")
            drag_handle.set_valign(Gtk.Align.CENTER)
            row.add_prefix(drag_handle)

            if prompt["editable"]:
                self.add_customize_prompt_content(row, prompt["key"], prompt["title"])
            mode_warning = self._get_prompt_mode_warning(prompt["key"], is_active)
            if mode_warning is not None:
                self._add_mode_warning_suffix(row, mode_warning)
            switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            switch.set_active(is_active)
            switch.connect("notify::active", self.update_prompt, prompt["setting_name"])
            row.add_suffix(switch)

            if prompt.get("user_custom"):
                delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
                delete_btn.add_css_class("flat")
                delete_btn.add_css_class("destructive-action")
                delete_btn.set_valign(Gtk.Align.CENTER)
                delete_btn.set_tooltip_text(_("Delete custom prompt"))
                delete_btn.connect("clicked", self.on_delete_custom_prompt, prompt["key"])
                row.add_suffix(delete_btn)

            drag_source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            drag_source.connect("prepare", self._on_prompt_drag_prepare, prompt["key"])
            drag_source.connect("drag-begin", self._on_prompt_drag_begin, prompt["title"])
            row.add_controller(drag_source)

            drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            drop_target.connect("enter", self._on_prompt_drop_enter, row)
            drop_target.connect("leave", self._on_prompt_drop_leave, row)
            drop_target.connect("drop", self._on_prompt_drop, prompt["key"])
            row.add_controller(drop_target)

            self.prompt.add(row)
            self.prompts_rows.append(row)

    def _get_ordered_prompts(self):
        try:
            order = json.loads(self.settings.get_string("prompts-order"))
        except (json.JSONDecodeError, Exception):
            order = []
        if not order:
            return list(AVAILABLE_PROMPTS)
        ordered = []
        key_to_prompt = {p["key"]: p for p in AVAILABLE_PROMPTS}
        for key in order:
            if key in key_to_prompt:
                ordered.append(key_to_prompt.pop(key))
        for prompt in AVAILABLE_PROMPTS:
            if prompt["key"] in key_to_prompt:
                ordered.append(prompt)
        return ordered

    def _save_prompts_order(self, ordered_prompts):
        order = [p["key"] for p in ordered_prompts]
        self.settings.set_string("prompts-order", json.dumps(order))

    def _on_prompt_drag_prepare(self, drag_source, x, y, key):
        value = GObject.Value(GObject.TYPE_STRING, key)
        return Gdk.ContentProvider.new_for_value(value)

    def _on_prompt_drag_begin(self, drag_source, drag, title):
        icon = Gtk.DragIcon.get_for_drag(drag)
        label = Gtk.Label(
            label=title,
            css_classes=["card"],
            margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
        )
        icon.set_child(label)

    def _on_prompt_drop_enter(self, drop_target, x, y, row):
        row.add_css_class("prompt-drop-target")
        return Gdk.DragAction.MOVE

    def _on_prompt_drop_leave(self, drop_target, row):
        row.remove_css_class("prompt-drop-target")

    def _on_prompt_drop(self, drop_target, source_key, x, y, target_key):
        for row in self.prompts_rows:
            row.remove_css_class("prompt-drop-target")
        if source_key == target_key:
            return False
        ordered = self._get_ordered_prompts()
        source_idx = next((i for i, p in enumerate(ordered) if p["key"] == source_key), None)
        target_idx = next((i for i, p in enumerate(ordered) if p["key"] == target_key), None)
        if source_idx is None or target_idx is None:
            return False
        item = ordered.pop(source_idx)
        ordered.insert(target_idx, item)
        self._save_prompts_order(ordered)
        self.build_prompts_settings()
        return True

    def build_browser_settings(self):
        # Browser settings
        self.browser_group = Adw.PreferencesGroup(title=_('Browser'), description=_(_("Settings for the browser")))
        
        # External Browser toggle 
        external_browser_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("external-browser", external_browser_toggle, 'active', Gio.SettingsBindFlags.DEFAULT)
        row = Adw.ActionRow(title=_("Use external browser"), subtitle=_("Use an external browser to open links instead of integrated one"))
        row.add_suffix(external_browser_toggle)
        self.browser_group.add(row)

        # Persist browser session toggle 
        persist_browser_toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("browser-session-persist", persist_browser_toggle, 'active', Gio.SettingsBindFlags.DEFAULT)
        row = Adw.ActionRow(title=_("Persist browser session"), subtitle=_("Persist browser session between restarts. Turning this off requires restarting the program"))
        row.add_suffix(persist_browser_toggle)
        self.browser_group.add(row)

        # Delete browser session row 
        row = Adw.ActionRow(title=_("Delete browser data"), subtitle=_("Delete browser session and data"))
        delete_button = Gtk.Button(label=_("Delete"), valign=Gtk.Align.CENTER)
        delete_button.connect("clicked", self.delete_browser_session)
        row.add_suffix(delete_button)
        self.browser_group.add(row)
        
        # Starting page 
        row = Adw.ActionRow(title=_("Initial browser page"), subtitle=_("The page where the browser will start"))
        entry = Gtk.Entry(valign=Gtk.Align.CENTER)
        self.settings.bind("initial-browser-page", entry, 'text', Gio.SettingsBindFlags.DEFAULT)
        row.add_suffix(entry)
        self.browser_group.add(row)
        
        # Search string 
        row = Adw.ActionRow(title=_("Search string"), subtitle=_("The search string used in the browser, %s is replaced with the query"))
        entry = Gtk.Entry(valign=Gtk.Align.CENTER)
        self.settings.bind("browser-search-string", entry, 'text', Gio.SettingsBindFlags.DEFAULT)
        row.add_suffix(entry)
        self.browser_group.add(row)

    def delete_browser_session(self, button:Gtk.Button):
        os.remove(self.controller.config_dir + "/bsession.json")
        os.remove(self.controller.config_dir + "/bsession.json.cookies")
        button.set_sensitive(False) 

    def build_rag_settings(self):
        def update_scale(scale, label, setting_value, type):
            value = scale.get_value()
            if type is float:
                self.settings.set_double(setting_value, value)
            elif type is int:
                value = int(value)
                self.settings.set_int(setting_value, value)
            label.set_text(str(value))

        self.RAG = Adw.PreferencesGroup(title=_('Document Sources (RAG)'), description=_("Include content from your documents in the responses"))
        tts_program = Adw.ExpanderRow(title=_('Document Analyzer'), subtitle=_("The document analyzer uses multiple techniques to extract relevant information about your documents"))
        #tts_program.add_action(memory_enabled)
        self.RAG.add(tts_program)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("rag-model")
        for key in AVAILABLE_RAGS:
           row = self.build_row(AVAILABLE_RAGS, key, selected, group) 
           tts_program.add_row(row)
       
        rag_on_docuements = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("rag-on-documents", rag_on_docuements, 'active', Gio.SettingsBindFlags.DEFAULT)
        rag_row = Adw.ExpanderRow(title=_("Read documents if unsupported"), subtitle=_("If the LLM does not support reading documents, relevant information about documents sent in the chat will be given to the LLM using your Document Analyzer."))
        rag_row.add_suffix(rag_on_docuements)
        self.RAG.add(rag_row)
         
        rag_limit = Adw.SpinRow(title=_("Maximum tokens for RAG"), subtitle=_("The maximum amount of tokens to be used for RAG. If the documents do not exceed this token count,\ndump all of them in the context"), adjustment=Gtk.Adjustment(lower=0, upper=50000, step_increment=100, page_increment=1000, value=self.settings.get_int("documents-context-limit")), digits=0)
        def update_rag_limit(spin, _):
             self.settings.set_int("documents-context-limit", int(spin.get_value()))
        rag_limit.connect("notify::value", update_rag_limit)
        rag_row.add_row(rag_limit)

        # Document folder 
        rag_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("rag-on", rag_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        document_folder = Adw.ExpanderRow(title=_("Document Folder"), subtitle=_("Put the documents you want to query in your document folder. The document analyzer will find relevant information in them if this option is enabled"))
        document_folder.add_suffix(rag_enabled)
        # Document folder rows 
        folder = Adw.ActionRow(title="Open your document folder", subtitle=_("Put all the documents you want to index in this folder"))
        folder_button = Gtk.Button(icon_name="folder-symbolic", css_classes=["flat"])
        folder_button.connect("clicked", lambda _: open_folder(os.path.join(self.directory, "documents")))
        folder.add_suffix(folder_button)
        document_folder.add_row(folder)
        
        # Custom folders management
        self.custom_folders_list = self.settings.get_strv("custom-document-folders")
        
        # Add custom folders expander
        self.custom_folders_row = Adw.ExpanderRow(title=_("Custom Document Folders"), subtitle=_("Add additional folders to index for document analysis"))
        document_folder.add_row(self.custom_folders_row)
        
        # Add folder button as suffix of expander
        add_folder_button = Gtk.Button(label=_("Add Folder"), css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
        add_folder_button.connect("clicked", self.on_add_custom_folder)
        self.custom_folders_row.add_suffix(add_folder_button)
        
        # Container for custom folder rows
        self.custom_folder_rows = []
        self.refresh_custom_folders_list(self.custom_folders_row)
        
        self.rag_handler = self.get_object(AVAILABLE_RAGS, selected) 
        self.rag_handler.set_handlers(self.handlers.llm, self.handlers.embedding)
        self.rag_index = self.create_extra_setting(self.rag_handler.get_index_row(), self.rag_handler, AVAILABLE_RAGS) 
        document_folder.add_row(self.rag_index)
        self.document_folder = document_folder

        self.RAG.add(document_folder)
        self.MemoryPage.add(self.RAG)
    
    def update_rag_index(self):
        self.rag_handler = self.get_object(AVAILABLE_RAGS, self.settings.get_string("rag-model"))
        self.rag_handler.set_handlers(self.handlers.llm, self.handlers.embedding)
        self.document_folder.remove(self.rag_index)
        self.rag_index = self.create_extra_setting(self.rag_handler.get_index_row(), self.rag_handler, AVAILABLE_RAGS)
        self.document_folder.add_row(self.rag_index)

    def on_add_custom_folder(self, button):
        """Callback for adding a custom folder"""
        dialog = Gtk.FileChooserDialog(
            title=_("Select Folder"),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self
        )
        dialog.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("_Add"), Gtk.ResponseType.ACCEPT)
        
        def on_response(dialog_widget, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                folder_path = dialog.get_file().get_path()
                if folder_path and folder_path not in self.custom_folders_list:
                    self.custom_folders_list.append(folder_path)
                    self.settings.set_strv("custom-document-folders", self.custom_folders_list)
                    # Refresh the list to show the new folder
                    self.refresh_custom_folders_list(self.custom_folders_row)
            dialog.destroy()
        
        dialog.connect("response", on_response)
        dialog.show()


    def on_remove_custom_folder(self, button, folder_path, parent_row):
        """Callback for removing a custom folder"""
        if folder_path in self.custom_folders_list:
            self.custom_folders_list.remove(folder_path)
            self.settings.set_strv("custom-document-folders", self.custom_folders_list)
            # Find and remove the row from the UI
            for row in self.custom_folder_rows:
                if hasattr(row, 'folder_path') and row.folder_path == folder_path:
                    # Get the parent expander and remove the row
                    parent = row.get_parent()
                    if parent:
                        parent.remove(row)
                    self.custom_folder_rows.remove(row)
                    break

    def on_open_custom_folder(self, button, folder_path):
        """Callback for opening a custom folder"""
        open_folder(folder_path)

    def refresh_custom_folders_list(self, parent_expander):
        """Refresh the UI to show current custom folders"""
        # Clear existing folder rows
        for row in self.custom_folder_rows:
            parent = row.get_parent()
            if parent:
                parent.remove(row)
        self.custom_folder_rows.clear()
        
        # Add rows for each custom folder
        for folder_path in self.custom_folders_list:
            folder_row = Adw.ActionRow(
                title=_("Custom Folder"),
                subtitle=folder_path
            )
            folder_row.folder_path = folder_path  # Store path for removal
            
            # Open button
            open_button = Gtk.Button(icon_name="folder-symbolic", css_classes=["flat"])
            open_button.connect("clicked", lambda b, path=folder_path: self.on_open_custom_folder(b, path))
            
            # Remove button
            remove_button = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"])
            remove_button.connect("clicked", lambda b, path=folder_path, row=folder_row: self.on_remove_custom_folder(b, path, row))
            
            folder_row.add_suffix(open_button)
            folder_row.add_suffix(remove_button)
            
            parent_expander.add_row(folder_row)
            self.custom_folder_rows.append(folder_row)

    def build_auto_stt(self):
        auto_stt_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("automatic-stt", auto_stt_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.auto_stt.add_suffix(auto_stt_enabled) 
        def update_scale(scale, label, setting_value, type):
            value = scale.get_value()
            if type is float:
                self.settings.set_double(setting_value, value)
            elif type is int:
                value = int(value)
                self.settings.set_int(setting_value, value)
            label.set_text(str(value))

        # Silence Threshold
        silence_threshold = Adw.ActionRow(title=_("Silence threshold"), subtitle=_("Silence threshold in seconds, percentage of the volume to be considered silence"))
        threshold = Gtk.Scale(digits=0, round_digits=2)
        threshold.set_range(0, 0.5)
        threshold.set_size_request(120, -1)
        th = self.settings.get_double("stt-silence-detection-threshold")
        label = Gtk.Label(label=str(th))
        threshold.set_value(th)
        threshold.connect("value-changed", update_scale, label, "stt-silence-detection-threshold", float)
        box = Gtk.Box()
        box.append(threshold)
        box.append(label)
        silence_threshold.add_suffix(box)
        # Silence time 
        silence_time = Adw.ActionRow(title=_("Silence time"), subtitle=_("Silence time in seconds before recording stops automatically"))
        time_scale = Gtk.Scale(digits=0, round_digits=0)
        time_scale.set_range(0, 10)
        time_scale.set_size_request(120, -1)
        value = self.settings.get_int("stt-silence-detection-duration")
        time_scale.set_value(value)
        label = Gtk.Label(label=str(value))
        time_scale.connect("value-changed", update_scale, label, "stt-silence-detection-duration", int)
        box = Gtk.Box()
        box.append(time_scale)
        box.append(label)
        silence_time.add_suffix(box)
        self.auto_stt.add_row(silence_threshold) 
        self.auto_stt.add_row(silence_time)

    def update_prompt(self, switch: Gtk.Switch, state, key: str):
        """Update the prompt in the settings

        Args:
            switch: the switch widget
            key: the key of the prompt
        """
        self.prompts_settings[key] = switch.get_active()
        self.settings.set_string("prompts-settings", json.dumps(self.prompts_settings))

    def build_row(self, constants: dict[str, Any], key: str, selected: str, group: Gtk.CheckButton, secondary: bool = False) -> Adw.ActionRow | Adw.ExpanderRow:
        """Build the row for every handler

        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            key: key of the specified handler
            selected: the key of the selected handler
            group: the check group for che checkbox in the row
            secondary: if to use secondary settings

        Returns:
            The created row
        """
        model = constants[key]
        handler = self.get_object(constants, key, secondary)
        constants_key = self.convert_constants(constants)
        settings_row_key = (key, constants_key, secondary)
        # Check if the model is the currently selected
        active = False
        if model["key"] == selected:
            active = True
        # Define the type of row
        self.settingsrows[settings_row_key] = {}
        extra_settings = handler.get_extra_settings()
        if len(extra_settings) > 0:
             row = Adw.ExpanderRow(title=model["title"], subtitle=model["description"])
             self.settingsrows[settings_row_key]["extra_settings_loaded"] = False
             self.settingsrows[settings_row_key]["pending_extra_settings"] = extra_settings
             row.connect("notify::expanded", self.on_row_expanded_build_settings, constants, handler)
        else:
            row = Adw.ActionRow(title=model["title"], subtitle=model["description"])
            self.settingsrows[settings_row_key]["extra_settings_loaded"] = True
        self.settingsrows[settings_row_key]["row"] = row
        self.settingsrows[settings_row_key]["extra_settings"] = []
        handler.set_extra_settings_update(
            lambda _: GLib.idle_add(self.on_setting_change, constants, handler, handler.key, True)
        )
        
        # Add extra buttons 
        self.queue_download_button(handler, row)
        self.add_flatpak_waning_button(handler, row)
       
        # Add copy settings button if it's secondary 
        if secondary:
            button = Gtk.Button(css_classes=["flat"], icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
            button.connect("clicked", self.copy_settings, constants, handler)
            row.add_suffix(button)
        if constants == AVAILABLE_LLMS and model.get("duplicated", False):
            delete_button = Gtk.Button(
                css_classes=["flat", "error"],
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Delete this LLM provider"),
            )
            delete_button.connect("clicked", self.on_delete_duplicated_llm, key)
            if isinstance(row, Adw.ExpanderRow):
                row.add_action(delete_button)
            else:
                row.add_suffix(delete_button)
        # Add check button
        button = Gtk.CheckButton(name=key, group=group, active=active)
        button.connect("toggled", self.choose_row, constants, secondary)
        self.settingsrows[settings_row_key]["button"] = button
        self._apply_handler_row_blocked_style(button, handler)
        row.add_prefix(button)

        if "website" in model:
            wbbutton = self.create_web_button(model["website"])
            row.add_suffix(wbbutton)
        return row

    def copy_settings(self, button, constants: dict[str, Any], handler: Handler):
        """Copy the settings"""
        primary = self.get_object(constants, handler.key, False)
        secondary = self.get_object(constants, handler.key, True)
        for setting in primary.get_all_settings():
            secondary.set_setting(setting, primary.get_setting(setting))
        self.on_setting_change(constants, handler, "", True)

    def _update_font_setting(self, key, value):
        self.settings.set_string(key, value)
        setattr(self.controller.newelle_settings, key.replace("-", "_"), value)
        self.app.win.update_font_settings()

    def _update_font_setting_int(self, key, spin):
        val = int(spin.get_value())
        self.settings.set_int(key, val)
        setattr(self.controller.newelle_settings, key.replace("-", "_"), val)
        self.app.win.update_font_settings()
        return False

    def _update_font_setting_double(self, key, spin):
        val = spin.get_value()
        self.settings.set_double(key, val)
        setattr(self.controller.newelle_settings, key.replace("-", "_"), val)
        self.app.win.update_font_settings()
        return False

    def get_object(self, constants, key, secondary=False):
        return self.handlers.get_object(constants, key, secondary)

    def convert_constants(self, constants):
        return self.handlers.convert_constants(constants)

    def get_constants_from_object(self, handler):
        return self.handlers.get_constants_from_object(handler)

    def _handler_selection_blocked(self, handler: Handler) -> bool:
        return (not self.sandbox and handler.requires_sandbox_escape()) or not handler.is_installed()

    def _apply_handler_row_blocked_style(self, button: Gtk.CheckButton, handler: Handler) -> None:
        button.set_opacity(0.45 if self._handler_selection_blocked(handler) else 1.0)

    def _restore_previous_handler_row(
        self,
        constants: dict[str, Any],
        secondary: bool,
        setting_name: str,
        failed_button: Gtk.CheckButton,
    ) -> None:
        prev_key = self.settings.get_string(setting_name)
        failed_button.set_active(False)
        sk = (prev_key, self.convert_constants(constants), secondary)
        if sk in self.settingsrows:
            self.settingsrows[sk]["button"].set_active(True)

    def _show_optional_deps_popover(self, anchor: Gtk.Widget) -> None:
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        label = Gtk.Label(wrap=True, max_width_chars=44)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_label(_("Click the download button on this row to install optional dependencies."))
        popover.set_child(label)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def choose_row(self, button, constants : dict, secondary=False):
        """Called by GTK the selected h
        andler is changed

        Args:
            button (): the button that triggered the change
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
        """
        setting_name = ""
        if constants == AVAILABLE_LLMS:
            if secondary:
                setting_name = "secondary-language-model"
            else:
                setting_name = "language-model"
        elif constants == AVAILABLE_TTS:
            setting_name = "tts"
        elif constants == AVAILABLE_STT:
            if secondary:
                setting_name = "secondary-stt-engine"
            else:
                setting_name = "stt-engine"
        elif constants == AVAILABLE_MEMORIES:
            setting_name = "memory-model"
        elif constants == AVAILABLE_EMBEDDINGS:
            setting_name = "embedding-model"
        elif constants == AVAILABLE_RAGS:
            setting_name = "rag-model"
        elif constants == AVAILABLE_WEBSEARCH:
            setting_name = "websearch-model"
        elif constants == AVAILABLE_IMAGE_GENERATORS:
            setting_name = "image-generator"
        else:
            return

        if not button.get_active():
            return

        handler = self.get_object(constants, button.get_name(), secondary)

        if not self.sandbox and handler.requires_sandbox_escape():
            self._restore_previous_handler_row(constants, secondary, setting_name, button)
            self.show_flatpak_sandbox_notice()
            return

        if not handler.is_installed():
            self._restore_previous_handler_row(constants, secondary, setting_name, button)
            self._show_optional_deps_popover(button)
            return

        self.settings.set_string(setting_name, button.get_name())
        if constants == AVAILABLE_LLMS and self.popup:
            self.app.win.update_available_models()
        if constants == AVAILABLE_RAGS or constants == AVAILABLE_EMBEDDINGS:
            self.app.win.update_settings()
            self.update_rag_index()

    def add_extra_settings(self, constants : dict[str, Any], handler : Handler, row : Adw.ExpanderRow, nested_settings : list | None = None, settings : list | None = None):
        self.extra_settings_builder.add_extra_settings(constants, handler, row, nested_settings, settings)

    def on_row_expanded_build_settings(self, row, _pspec, constants, handler):
        self.extra_settings_builder.on_row_expanded_build_settings(row, _pspec, constants, handler)

    def queue_download_button(self, handler: Handler, row: Adw.ActionRow | Adw.ExpanderRow):
        """Queue download button creation to run incrementally on the GTK main loop."""
        self._pending_download_button_rows.append((handler, row))
        if not self._download_button_queue_scheduled:
            self._download_button_queue_scheduled = True
            GLib.idle_add(self.process_download_button_queue)

    def process_download_button_queue(self):
        if not self._pending_download_button_rows:
            self._download_button_queue_scheduled = False
            return False
        handler, row = self._pending_download_button_rows.pop(0)
        self.add_download_button(handler, row)
        # Process one row per idle tick to avoid blocking the first render.
        return True
    
    def create_extra_setting(self, setting : dict, handler: Handler, constants : dict[str, Any]) -> Adw.ExpanderRow | Adw.ActionRow:
        return self.extra_settings_builder.create_extra_setting(setting, handler, constants)
    
    def add_customize_prompt_content(self, row, prompt_name, prompt_title=""):
        """Add a MultilineEntry to edit a prompt from the given prompt name

        Args:
            row (): row of the prompt 
            prompt_name (): name of the prompt 
            prompt_title (): title of the prompt for the expand dialog
        """
        box = Gtk.Box(spacing=6)
        entry = MultilineEntry()
        entry.set_hexpand(True)
        entry.set_text(self.prompts[prompt_name])
        entry.set_name(prompt_name)
        entry.set_on_change(self.edit_prompt)

        expand_button = Gtk.Button(icon_name="window-maximize-symbolic")
        expand_button.add_css_class("flat")
        expand_button.set_valign(Gtk.Align.CENTER)
        expand_button.set_tooltip_text(_("Expand editor"))
        expand_button.connect("clicked", self._on_expand_prompt, entry, prompt_title or prompt_name)

        restore_button = Gtk.Button(icon_name="star-filled-rounded-symbolic")
        restore_button.add_css_class("flat")
        restore_button.set_valign(Gtk.Align.CENTER)
        restore_button.set_tooltip_text(_("Restore default"))
        restore_button.connect("clicked", self.restore_prompt, entry)

        buttons = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
        buttons.append(expand_button)
        buttons.append(restore_button)

        box.append(entry)
        box.append(buttons)
        row.add_row(box)

    def _on_expand_prompt(self, button, entry, prompt_title):
        dialog = Gtk.Window()
        dialog.set_title(_("Edit Prompt"))
        dialog.set_transient_for(self.app.win)
        dialog.set_modal(True)
        dialog.set_default_size(700, 500)

        header = Adw.HeaderBar(css_classes=["flat"])
        dialog.set_titlebar(header)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda *_: dialog.close())

        save_btn = Gtk.Button(label=_("Save"))
        save_btn.add_css_class("suggested-action")
        header.pack_start(cancel_btn)
        header.pack_end(save_btn)

        if prompt_title:
            title_label = Gtk.Label(
                label=prompt_title,
                css_classes=["heading"],
                halign=Gtk.Align.START,
                margin_start=18,
                margin_top=12,
            )
            header.set_title_widget(title_label)

        scrolled = Gtk.ScrolledWindow(
            hexpand=True,
            vexpand=True,
            margin_top=6,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        textview = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD,
            monospace=True,
            top_margin=12,
            bottom_margin=12,
            left_margin=12,
            right_margin=12,
            css_classes=["card"],
        )
        buf = textview.get_buffer()
        buf.set_text(entry.get_text())
        scrolled.set_child(textview)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(scrolled)
        dialog.set_child(content)

        def on_save(_):
            new_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            entry.set_text(new_text)
            self.edit_prompt(entry)
            dialog.close()

        save_btn.connect("clicked", on_save)
        dialog.present()

    def edit_prompt(self, entry):
        """Called when the MultilineEntry is changed

        Args:
            entry : the MultilineEntry 
        """
        prompt_name = entry.get_name()
        prompt_text = entry.get_text()

        if prompt_text == PROMPTS[prompt_name]:
            if prompt_name in self.custom_prompts:
                del self.custom_prompts[prompt_name]
            self.prompts[prompt_name] = PROMPTS[prompt_name]
        else:
            self.custom_prompts[prompt_name] = prompt_text
            self.prompts[prompt_name] = prompt_text
        self.settings.set_string("custom-prompts", json.dumps(self.custom_prompts))

    def restore_prompt(self, button, entry):
        """Called when the prompt restore is called

        Args:
            button (): the clicked button 
            entry (): the MultilineEntry associated with the prompt
        """
        prompt_name = entry.get_name()
        entry.set_text(PROMPTS[prompt_name])

    def on_add_custom_prompt(self, button):
        dialog = Gtk.Window()
        dialog.set_title(_("Add Custom Prompt"))
        dialog.set_transient_for(self)
        dialog.set_modal(True)
        dialog.set_default_size(500, 450)

        header = Adw.HeaderBar(css_classes=["flat"])
        dialog.set_titlebar(header)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda *_: dialog.close())

        add_btn = Gtk.Button(label=_("Add"))
        add_btn.add_css_class("suggested-action")
        header.pack_start(cancel_btn)
        header.pack_end(add_btn)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)

        name_row = Adw.EntryRow(title=_("Name"))
        content.append(name_row)

        desc_row = Adw.EntryRow(title=_("Description"))
        content.append(desc_row)

        text_label = Gtk.Label(label=_("Prompt Text"), halign=Gtk.Align.START)
        content.append(text_label)

        scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        textview = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD,
            monospace=True,
            top_margin=12, bottom_margin=12,
            left_margin=12, right_margin=12,
            css_classes=["card"],
        )
        scrolled.set_child(textview)
        content.append(scrolled)

        dialog.set_child(content)

        def on_add(_):
            title = name_row.get_text().strip()
            description = desc_row.get_text().strip()
            buf = textview.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
            if not title or not text:
                return
            key = f"user_custom_{int(time.time() * 1000)}"
            user_prompts = json.loads(self.settings.get_string("user-custom-prompts"))
            user_prompts.append({
                "key": key,
                "title": title,
                "description": description,
                "text": text,
            })
            self.settings.set_string("user-custom-prompts", json.dumps(user_prompts))
            PROMPTS[key] = text
            AVAILABLE_PROMPTS.append({
                "key": key,
                "setting_name": key,
                "title": title,
                "description": description,
                "editable": True,
                "show_in_settings": True,
                "default": True,
                "user_custom": True,
            })
            self.prompts[key] = text
            self.prompts_settings[key] = True
            self.settings.set_string("prompts-settings", json.dumps(self.prompts_settings))
            self.build_prompts_settings()
            dialog.close()

        add_btn.connect("clicked", on_add)
        dialog.present()

    def on_delete_custom_prompt(self, button, key):
        user_prompts = json.loads(self.settings.get_string("user-custom-prompts"))
        user_prompts = [p for p in user_prompts if p["key"] != key]
        self.settings.set_string("user-custom-prompts", json.dumps(user_prompts))
        if key in PROMPTS:
            del PROMPTS[key]
        for i, p in enumerate(AVAILABLE_PROMPTS):
            if p["key"] == key:
                AVAILABLE_PROMPTS.pop(i)
                break
        if key in self.prompts:
            del self.prompts[key]
        if key in self.custom_prompts:
            del self.custom_prompts[key]
            self.settings.set_string("custom-prompts", json.dumps(self.custom_prompts))
        if key in self.prompts_settings:
            del self.prompts_settings[key]
            self.settings.set_string("prompts-settings", json.dumps(self.prompts_settings))
        order = json.loads(self.settings.get_string("prompts-order"))
        if key in order:
            order.remove(key)
            self.settings.set_string("prompts-order", json.dumps(order))
        self.build_prompts_settings()



    def toggle_virtualization(self, toggle, status):
        """Called when virtualization is toggled, also checks if there are enough permissions. If there aren't show a warning

        Args:
            toggle (): 
            status (): 
        """
        if not self.sandbox and not status:
            self.show_flatpak_sandbox_notice()            
            toggle.set_active(True)
            self.settings.set_boolean("virtualization", True)
        else:
            self.settings.set_boolean("virtualization", status)

    def _on_extra_settings_rebuild(self, constants: dict[str, Any], _handler: Handler):
        if constants == AVAILABLE_RAGS:
            GLib.idle_add(self.update_rag_index)

    def on_setting_change(self, constants: dict[str, Any], handler: Handler, key: str, force_change : bool = False):
        self.extra_settings_builder.on_setting_change(constants, handler, key, force_change)

    def setting_change_entry(self, entry, constants, handler : Handler):
        self.extra_settings_builder.setting_change_entry(entry, constants, handler)

    def setting_change_multilinentry(self, entry):
        self.extra_settings_builder.setting_change_multilinentry(entry)

    def setting_change_toggle(self, toggle, state, constants, handler):
        self.extra_settings_builder.setting_change_toggle(toggle, state, constants, handler)

    def setting_change_scale(self, scale, scroll, value, constants, handler):
        self.extra_settings_builder.setting_change_scale(scale, scroll, value, constants, handler)

    def setting_change_spin(self, row, pspec, constants, handler):
        self.extra_settings_builder.setting_change_spin(row, pspec, constants, handler)

    def setting_change_combo(self, helper, value, constants, handler):
        self.extra_settings_builder.setting_change_combo(helper, value, constants, handler)

    def add_download_button(self, handler : Handler, row : Adw.ActionRow | Adw.ExpanderRow): 
        """Add download button for an handler dependencies. If clicked it will call handler.install()

        Args:
            handler: an instance of the handler
            row: row where to add teh button
        """
        actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        if not handler.is_installed():
            if get_download_manager().has_active(handler.get_install_source_id()):
                spinner = Gtk.Spinner(spinning=True)
                actionbutton.set_child(spinner)
                actionbutton.add_css_class("accent")
                actionbutton.connect("clicked", lambda _ : self.app.downloads_action())
            else:
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
                actionbutton.connect("clicked", self.install_model, handler)
                actionbutton.add_css_class("accent")
                actionbutton.set_child(icon)
            if type(row) is Adw.ActionRow:
                row.add_suffix(actionbutton)
            elif type(row) is Adw.ExpanderRow:
                row.add_action(actionbutton)

    def add_flatpak_waning_button(self, handler : Handler, row : Adw.ExpanderRow | Adw.ActionRow | Adw.ComboRow):
        """Add flatpak warning button in case the application does not have enough permissions
        On click it will show a warning about this issue and how to solve it

        Args:
            handler: an instance of the handler
            row: the row where to add the button
        """
        actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        if handler.requires_sandbox_escape() and not self.sandbox:
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="warning-outline-symbolic"))
            actionbutton.connect("clicked", self.show_flatpak_sandbox_notice)
            actionbutton.add_css_class("error")
            actionbutton.set_child(icon)
            if type(row) is Adw.ActionRow:
                row.add_suffix(actionbutton)
            elif type(row) is Adw.ExpanderRow:
                row.add_action(actionbutton)
            elif type(row) is Adw.ComboRow:
                row.add_suffix(actionbutton)

    def install_model(self, button: Gtk.Button, handler):
        """Display a spinner and trigger the dependency download on another thread

        Args:
            button (): the specified button
            handler (): handler of the model
        """
        spinner = Gtk.Spinner(spinning=True)
        button.set_child(spinner)
        button.disconnect_by_func(self.install_model)
        button.connect("clicked", lambda _x: self.app.downloads_action())
        t = threading.Thread(target=self.install_model_async, args= (button, handler))
        t.start() 

    def install_model_async(self, button, model):
        """Install the model dependencies, called on another thread

        Args:
            button (): button  
            model (): a handler instance
        """
        try:
            constants = self.get_constants_from_object(model)
            title = constants.get(model.key, {}).get("title", model.key)
            model.install_with_progress(_("Install {name}").format(name=title))
        except Exception as error:
            print(f"Error installing {model.key}: {error}")
        GLib.idle_add(self.update_ui_after_install, button, model)

    def update_ui_after_install(self, button, model):
        """Update the UI after a model installation

        Args:
            button (): button 
            model (): a handler instance 
        """
        if model.is_installed():
            self.on_setting_change(self.get_constants_from_object(model), model, "", True)
            button.set_child(None)
            button.set_sensitive(False)
        else:
            button.set_child(
                Gtk.Image.new_from_gicon(
                    Gio.ThemedIcon(name="folder-download-symbolic")
                )
            )
            button.set_sensitive(True)
            button.connect("clicked", self.install_model, model)
        checkbutton = self.settingsrows[(model.key, self.convert_constants(self.get_constants_from_object(model)), model.is_secondary())]["button"]
        self._apply_handler_row_blocked_style(checkbutton, model)

    def download_setting(self, button: Gtk.Button, setting, handler: Handler, uninstall=False):
        """Download the setting for the given handler

        Args:
            button (): button pressed
            setting (): setting to download
            handler (): handler to download the setting for
        """

        if uninstall:
            return
        box = Gtk.Box(homogeneous=True, spacing=4)
        box.set_orientation(Gtk.Orientation.VERTICAL)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic" if "download-icon" not in setting else setting["download-icon"]))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        progress = Gtk.ProgressBar(hexpand=False)
        progress.set_size_request(4, 4)
        box.append(icon)
        box.append(progress)
        button.set_child(box)
        button.disconnect_by_func(self.download_setting)
        button.connect("clicked", lambda x: setting["callback"](setting["key"]))
        th = threading.Thread(target=self.download_setting_thread, args=(handler, setting, button, progress))
        self.model_threads[(setting["key"]), handler.key] = [th, 0]
        th.start()

    def update_download_status_setting(self, handler, setting, progressbar):
        """Periodically update the progressbar for the download

        Args:
            model (): model that is being downloaded
            filesize (): filesize of the download
            progressbar (): the bar to update
        """
        while (setting["key"], handler.key) in self.downloading and self.downloading[(setting["key"], handler.key)]:
            try:
                perc = setting["download_percentage"](setting["key"])
                GLib.idle_add(progressbar.set_fraction, perc)
            except Exception as e:
                print(e)
            time.sleep(1)

    def download_setting_thread(self, handler: Handler, setting: dict, button: Gtk.Button, progressbar: Gtk.ProgressBar):
        self.model_threads[(setting["key"], handler.key)][1] = threading.current_thread().ident
        self.downloading[(setting["key"], handler.key)] = True
        th = threading.Thread(target=self.update_download_status_setting, args=(handler, setting, progressbar))
        th.start()
        setting["callback"](setting["key"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        button.add_css_class("error")
        button.set_child(icon)
        self.downloading[(setting["key"], handler.key)] = False

    def create_web_button(self, website, folder=False) -> Gtk.Button:
        """Create an icon to open a specified website or folder

        Args:
            website (): The website/folder path to open
            folder (): if it is a folder, defaults to False

        Returns:
            The created button
        """
        wbbutton = Gtk.Button(icon_name="internet-symbolic" if not folder else "search-folder-symbolic")
        wbbutton.add_css_class("flat")
        wbbutton.set_valign(Gtk.Align.CENTER)
        wbbutton.set_name(website)
        if not folder:
            wbbutton.connect("clicked", lambda _: open_website(website))
        else:
            wbbutton.connect("clicked", lambda _: open_folder(website))
        return wbbutton

    def show_flatpak_sandbox_notice(self, el=None):
        """Create a MessageDialog that explains the issue with missing permissions on flatpak

        Args:
            el (): 
        """
        # Create a modal window with the warning
        dialog = Adw.MessageDialog(
            title="Permission Error",
            modal=True,
            transient_for=self,
            destroy_with_parent=True
        )

        # Imposta il contenuto della finestra
        dialog.set_heading(_("Not enough permissions"))

        # Aggiungi il testo dell'errore
        dialog.set_body_use_markup(True)
        dialog.set_body(_("Newelle does not have enough permissions to run commands on your system, please run the following command"))
        dialog.add_response("close", _("Understood"))
        dialog.set_default_response("close")
        dialog.set_extra_child(CopyBox("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle", "bash"))
        dialog.set_close_response("close")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', lambda dialog, response_id: dialog.destroy())
        # Show the window
        dialog.present()

    def delete_pip_path(self):
        """Delete the pip path folder"""
        shutil.rmtree(self.controller.pip_path)
        dialog = Adw.MessageDialog(title=_("Pip path deleted"), body=_("The pip path has been deleted, you can now reinstall the dependencies. This operation requires a restart of the application."))
        dialog.add_response("close", _("Understood"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', lambda dialog, response_id: dialog.destroy())
        dialog.present()

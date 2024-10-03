from typing import Any
import gi
import re, threading, os, json, time, ctypes
from subprocess import Popen 
from gi.repository import Gtk, Adw, Gio, GLib

from .translator import TranslatorHandler
from .smart_prompt import SmartPromptHandler
from .avatar import AvatarHandler

from .stt import STTHandler
from .tts import TTSHandler
from .constants import AVAILABLE_AVATARS, AVAILABLE_LLMS, AVAILABLE_TRANSLATORS, AVAILABLE_TTS, AVAILABLE_STT, PROMPTS, AVAILABLE_PROMPTS, AVAILABLE_SMART_PROMPTS
from gpt4all import GPT4All
from .llm import GPT4AllHandler, LLMHandler
from .gtkobj import ComboRowHelper, CopyBox, MultilineEntry
from .extra import can_escape_sandbox, override_prompts, human_readable_size

class Settings(Adw.PreferencesWindow):
    def __init__(self,app,headless=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sandbox = can_escape_sandbox()
        self.settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        if not headless:
            self.set_transient_for(app.win)
        self.set_modal(True)
        self.downloading = {}
        self.slider_labels = {}
        self.local_models = json.loads(self.settings.get_string("available-models"))
        self.directory = GLib.get_user_config_dir()
        self.gpt = GPT4AllHandler(self.settings, os.path.join(self.directory, "models"))
        # Load custom prompts
        self.custom_prompts = json.loads(self.settings.get_string("custom-prompts"))
        self.prompts = override_prompts(self.custom_prompts, PROMPTS)
        self.sandbox = can_escape_sandbox()
        # Page building
        self.general_page = Adw.PreferencesPage()
        
        # Dictionary containing all the rows for settings update
        self.settingsrows = {}
        # Build the LLMs settings
        self.LLM = Adw.PreferencesGroup(title=_('Language Model'))
        self.general_page.add(self.LLM)
        self.llmbuttons = [];
        group = Gtk.CheckButton()
        selected = self.settings.get_string("language-model")
        for model_key in AVAILABLE_LLMS:
           row = self.build_row(AVAILABLE_LLMS, model_key, selected, group)
           self.LLM.add(row)
        
        # Build the TTS settings
        self.TTSgroup = Adw.PreferencesGroup(title=_('Text To Speech'))
        self.general_page.add(self.TTSgroup)
        tts_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("tts-on", tts_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        tts_program = Adw.ExpanderRow(title=_('Text To Speech Program'), subtitle=_("Choose which text to speech to use"))
        tts_program.add_action(tts_enabled)
        self.TTSgroup.add(tts_program)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("tts")
        for tts_key in AVAILABLE_TTS:
           row = self.build_row(AVAILABLE_TTS, tts_key, selected, group) 
           tts_program.add_row(row)
        
        # Build the Translators settings
        group = Gtk.CheckButton()
        selected = self.settings.get_string("translator")
        tts_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("translator-on", tts_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        translator_program = Adw.ExpanderRow(title=_('Translator program'), subtitle=_("Translate the output of the LLM before passing it to the TTS Program"))
        translator_program.add_action(tts_enabled)
        for translator_key in AVAILABLE_TRANSLATORS:
            row = self.build_row(AVAILABLE_TRANSLATORS, translator_key, selected, group)
            translator_program.add_row(row)
        self.TTSgroup.add(translator_program)
        
        # Build the Speech to Text settings
        self.STTgroup = Adw.PreferencesGroup(title=_('Speech to Text'))
        self.general_page.add(self.STTgroup)
        stt_engine = Adw.ExpanderRow(title=_('Speech To Text Engine'), subtitle=_("Choose which speech recognition engine you want"))
        self.STTgroup.add(stt_engine)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("stt-engine")
        for stt_key in AVAILABLE_STT:
            row = self.build_row(AVAILABLE_STT, stt_key, selected, group)
            stt_engine.add_row(row)
        
        # Build the AVATAR settings
        self.avatargroup = Adw.PreferencesGroup(title=_('Avatar'))
        self.general_page.add(self.avatargroup)
        avatar_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("avatar-on", avatar_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        avatar = Adw.ExpanderRow(title=_('Avatar model'), subtitle=_("Choose which avatar model to choose"))
        avatar.add_action(avatar_enabled)
        self.avatargroup.add(avatar)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("avatar-model")
        for avatar_key in AVAILABLE_AVATARS:
           row = self.build_row(AVAILABLE_AVATARS, avatar_key, selected, group) 
           avatar.add_row(row)
        
        # Build the Smart Prompt settings
        self.smartpromptgroup = Adw.PreferencesGroup(title=_('Smart Prompt'))
        self.general_page.add(self.smartpromptgroup)
        smart_prompt_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("smart-prompt-on", smart_prompt_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)
        smartprompt = Adw.ExpanderRow(title=_('Smart Prompt selector'), subtitle=_("Choose which smart prompt model to choose"))
        smartprompt.add_action(smart_prompt_enabled)
        self.smartpromptgroup.add(smartprompt)
        group = Gtk.CheckButton()
        selected = self.settings.get_string("smart-prompt")
        for smart_prompt_key in AVAILABLE_SMART_PROMPTS:
           row = self.build_row(AVAILABLE_SMART_PROMPTS, smart_prompt_key, selected, group) 
           smartprompt.add_row(row)

        # Interface settings
        self.interface = Adw.PreferencesGroup(title=_('Interface'))
        self.general_page.add(self.interface)

        row = Adw.ActionRow(title=_("Hidden files"), subtitle=_("Show hidden files"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("hidden-files", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title=_("Number of offers"), subtitle=_("Number of message suggestions to send to chat "))
        int_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        int_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=5, step_increment=1, page_increment=10, page_size=0))
        row.add_suffix(int_spin)
        self.settings.bind("offers", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        # Prompts settings
        self.prompt = Adw.PreferencesGroup(title=_('Prompt control'))
        self.general_page.add(self.prompt)

        row = Adw.ActionRow(title=_("Auto-run commands"), subtitle=_("Commands that the bot will write will automatically run"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("auto-run", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        self.__prompts_entries = {}
        for prompt in AVAILABLE_PROMPTS:
            if not prompt["show_in_settings"]:
                continue
            row = Adw.ExpanderRow(title=prompt["title"], subtitle=prompt["description"])
            if prompt["editable"]:
                self.add_customize_prompt_content(row, prompt["key"])
            switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            row.add_suffix(switch)
            self.settings.bind(prompt["setting_name"], switch, 'active', Gio.SettingsBindFlags.DEFAULT)
            self.prompt.add(row)
        self.neural_network = Adw.PreferencesGroup(title=_('Neural Network Control'))
        self.general_page.add(self.neural_network) 

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
        
        row = Adw.ExpanderRow(title=_("External Terminal"), subtitle=_("Choose the external terminal where to run the console commands"))
        entry = Gtk.Entry()
        self.settings.bind("external-terminal", entry, 'text', Gio.SettingsBindFlags.DEFAULT)
        row.add_row(entry)
        self.neural_network.add(row)
        # Set default value for the switch        
        row = Adw.ActionRow(title=_("Program memory"), subtitle=_("How long the program remembers the chat "))
        int_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        int_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=30, step_increment=1, page_increment=10, page_size=0))
        row.add_suffix(int_spin)
        self.settings.bind("memory", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.neural_network.add(row)

        self.message = Adw.PreferencesGroup(title=_('The change will take effect after you restart the program.'))
        self.general_page.add(self.message)

        self.add(self.general_page)


    def build_row(self, constants: dict[str, Any], key: str, selected: str, group: Gtk.CheckButton) -> Adw.ActionRow | Adw.ExpanderRow:
        """Build the row for every handler

        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            key: key of the specified handler
            selected: the key of the selected handler
            group: the check group for che checkbox in the row

        Returns:
            The created row
        """
        model = constants[key]
        handler = self.get_object(constants, key)
        # Check if the model is the currently selected
        active = False
        if model["key"] == selected:
            active = True
        # Define the type of row
        self.settingsrows[(key, self.convert_constants(constants))] = {}
        if len(handler.get_extra_settings()) > 0 or key == "local":
             row = Adw.ExpanderRow(title=model["title"], subtitle=model["description"])
             if key != "local":
                 self.add_extra_settings(constants, handler, row)
             else:
                self.llmrow = row
                threading.Thread(target=self.build_local).start()
        else:
            row = Adw.ActionRow(title=model["title"], subtitle=model["description"])
        self.settingsrows[(key, self.convert_constants(constants))]["row"] = row
        
        # Add extra buttons 
        threading.Thread(target=self.add_download_button, args=(handler, row)).start()
        self.add_flatpak_waning_button(handler, row)
        
        # Add check button
        button = Gtk.CheckButton(name=key, group=group, active=active)
        button.connect("toggled", self.choose_row, constants)
        self.settingsrows[(key, self.convert_constants(constants))]["button"] = button 
        if not self.sandbox and handler.requires_sandbox_escape() or not handler.is_installed():
            button.set_sensitive(False)
        row.add_prefix(button)
        return row

    def get_object(self, constants: dict[str, Any], key:str) -> (LLMHandler | TTSHandler | STTHandler | AvatarHandler | TranslatorHandler | SmartPromptHandler):
        """Get an handler instance for the specified handler key

        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            key: key of the specified handler

        Raises:
            Exception: if the constant is not valid 

        Returns:
            The created handler           
        """
        if constants == AVAILABLE_LLMS:
            model = constants[key]["class"](self.settings, os.path.join(self.directory, "pip"))
        elif constants == AVAILABLE_STT:
            model = constants[key]["class"](self.settings,os.path.join(self.directory, "models"))
        elif constants == AVAILABLE_TTS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_AVATARS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_TRANSLATORS:
            model = constants[key]["class"](self.settings, self.directory)
        elif constants == AVAILABLE_SMART_PROMPTS:
            model = constants[key]["class"](self.settings, self.directory)
        else:
            raise Exception("Unknown constants")
        return model
    
    def convert_constants(self, constants: str | dict[str, Any]) -> (str | dict):
        """Get an handler instance for the specified handler key

        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            key: key of the specified handler

        Raises:
            Exception: if the constant is not valid 

        Returns:
            The created handler           
        """
        if type(constants) is str:
            match constants:
                case "tts":
                    return AVAILABLE_TTS
                case "stt":
                    return AVAILABLE_STT
                case "llm":
                    return AVAILABLE_LLMS
                case "avatar":
                    return AVAILABLE_AVATARS
                case "translator":
                    return AVAILABLE_TRANSLATORS
                case "smart-prompt":
                    return AVAILABLE_SMART_PROMPTS
                case _:
                    raise Exception("Unknown constants")
        else:
            if constants == AVAILABLE_LLMS:
                return "llm"
            elif constants == AVAILABLE_STT:
                return "stt"
            elif constants == AVAILABLE_TTS:
                return "tts"
            elif constants == AVAILABLE_AVATARS:
                return "avatar"
            elif constants == AVAILABLE_TRANSLATORS:
                return "translator"
            elif constants == AVAILABLE_SMART_PROMPTS:
                return "smart-prompt"
            else:
                raise Exception("Unknown constants")

    def get_constants_from_object(self, handler: TTSHandler | STTHandler | LLMHandler | AvatarHandler | TranslatorHandler | SmartPromptHandler) -> dict[str, Any]:
        """Get the constants from an hander

        Args:
            handler: the handler 

        Raises:
            Exception: if the handler is not known

        Returns: AVAILABLE_LLMS, AVAILABLE_STT, AVAILABLE_TTS based on the type of the handler 
        """
        if issubclass(type(handler), TTSHandler):
            return AVAILABLE_TTS
        elif issubclass(type(handler), STTHandler):
            return AVAILABLE_STT
        elif issubclass(type(handler), LLMHandler):
            return AVAILABLE_LLMS
        elif issubclass(type(handler), AvatarHandler):
            return AVAILABLE_AVATARS
        elif issubclass(type(handler), TranslatorHandler):
            return AVAILABLE_TRANSLATORS
        elif issubclass(type(handler), SmartPromptHandler):
            return AVAILABLE_SMART_PROMPTS
        else:
            raise Exception("Unknown handler")

    def choose_row(self, button, constants : dict):
        """Called by GTK the selected handler is changed

        Args:
            button (): the button that triggered the change
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
        """
        setting_name = ""
        if constants == AVAILABLE_LLMS:
            setting_name = "language-model"
        elif constants == AVAILABLE_TTS:
            setting_name = "tts"
        elif constants == AVAILABLE_STT:
            setting_name = "stt-engine"
        elif constants == AVAILABLE_AVATARS:
            setting_name = "avatar-model"
        elif constants == AVAILABLE_TRANSLATORS:
            setting_name = "translator"
        elif constants == AVAILABLE_SMART_PROMPTS:
            setting_name = "smart-prompt"
        else:
            return
        self.settings.set_string(setting_name, button.get_name())


    def add_extra_settings(self, constants : dict[str, Any], handler : LLMHandler | TTSHandler | STTHandler | AvatarHandler | TranslatorHandler | SmartPromptHandler, row : Adw.ExpanderRow):
        """Buld the extra settings for the specified handler. The extra settings are specified by the method get_extra_settings 
            Extra settings format:
            Required parameters:
            - title: small title for the setting 
            - description: description for the setting
            - default: default value for the setting
            - type: What type of row to create, possible rows:
                - entry: input text 
                - toggle: bool
                - combo: for multiple choice
                    - values: list of touples of possible values (display_value, actual_value)
                - range: for number input with a slider 
                    - min: minimum value
                    - max: maximum value 
                    - round: how many digits to round 
            Optional parameters:
                - folder: add a button that opens a folder with the specified path
                - website: add a button that opens a website with the specified path
                - update_settings (bool) if reload the settings in the settings page for the specified handler after that setting change
        Args:
            constants: The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            handler: An instance of the handler
            row: row where to add the settings
        """
        self.settingsrows[(handler.key, self.convert_constants(constants))]["extra_settings"] = []
        for setting in handler.get_extra_settings():
            if setting["type"] == "entry":
                r = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
                value = handler.get_setting(setting["key"])
                value = str(value)
                entry = Gtk.Entry(valign=Gtk.Align.CENTER, text=value, name=setting["key"])
                entry.connect("changed", self.setting_change_entry, constants, handler)
                r.add_suffix(entry)
            elif setting["type"] == "toggle":
                r = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
                value = handler.get_setting(setting["key"])
                value = bool(value)

                toggle = Gtk.Switch(valign=Gtk.Align.CENTER, active=value, name=setting["key"])
                toggle.connect("state-set", self.setting_change_toggle, constants, handler)
                r.add_suffix(toggle)
            elif setting["type"] == "combo":
                r = Adw.ComboRow(title=setting["title"], subtitle=setting["description"], name=setting["key"])
                helper = ComboRowHelper(r, setting["values"], handler.get_setting(setting["key"]))
                helper.connect("changed", self.setting_change_combo, constants, handler)
            elif setting["type"] == "range":
                r = Adw.ActionRow(title=setting["title"], subtitle=setting["description"], valign=Gtk.Align.CENTER)
                box = Gtk.Box()
                scale = Gtk.Scale(name=setting["key"], round_digits=setting["round-digits"])
                scale.set_range(setting["min"], setting["max"]) 
                scale.set_value(round(handler.get_setting(setting["key"]), setting["round-digits"]))
                scale.set_size_request(120, -1)
                scale.connect("change-value", self.setting_change_scale, constants, handler)
                label = Gtk.Label(label=handler.get_setting(setting["key"]))
                box.append(label)
                box.append(scale)
                self.slider_labels[scale] = label
                r.add_suffix(box)
            else:
                continue
            if "website" in setting:
                wbbutton = self.create_web_button(setting["website"])
                r.add_prefix(wbbutton)
            if "folder" in setting:
                wbbutton = self.create_web_button(setting["folder"], folder=True)
                r.add_suffix(wbbutton)
            row.add_row(r)
            self.settingsrows[handler.key, self.convert_constants(constants)]["extra_settings"].append(r)


    def add_customize_prompt_content(self, row, prompt_name):
        """Add a MultilineEntry to edit a prompt from the given prompt name

        Args:
            row (): row of the prompt 
            prompt_name (): name of the prompt 
        """
        box = Gtk.Box()
        entry = MultilineEntry()
        entry.set_text(self.prompts[prompt_name])
        self.__prompts_entries[prompt_name] = entry
        entry.set_name(prompt_name)
        entry.set_on_change(self.edit_prompt)

        wbbutton = Gtk.Button(icon_name="star-filled-rounded-symbolic")
        wbbutton.add_css_class("flat")
        wbbutton.set_valign(Gtk.Align.CENTER)
        wbbutton.set_name(prompt_name)
        wbbutton.connect("clicked", self.restore_prompt)

        box.append(entry)
        box.append(wbbutton)
        row.add_row(box)

    def edit_prompt(self, entry):
        """Called when the MultilineEntry is changed

        Args:
            entry : the MultilineEntry 
        """
        prompt_name = entry.get_name()
        prompt_text = entry.get_text()

        if prompt_text == PROMPTS[prompt_name]:
            del self.custom_prompts[entry.get_name()]
        else:
            self.custom_prompts[prompt_name] = prompt_text
            self.prompts[prompt_name] = prompt_text
        self.settings.set_string("custom-prompts", json.dumps(self.custom_prompts))

    def restore_prompt(self, button):
        """Called when the prompt restore is called

        Args:
            button (): the clicked button 
        """
        prompt_name = button.get_name()
        self.prompts[prompt_name] = PROMPTS[prompt_name]
        self.__prompts_entries[prompt_name].set_text(self.prompts[prompt_name])



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

    def open_website(self, button):
        Popen(["flatpak-spawn", "--host", "xdg-open", button.get_name()])
        
    def on_setting_change(self, constants: dict[str, Any], handler: LLMHandler | TTSHandler | STTHandler, key: str, force_change : bool = False):
        
        if not force_change:
            setting_info = [info for info in handler.get_extra_settings() if info["key"] == key][0]
        else:
            setting_info = {}
        if force_change or "update_settings" in setting_info and setting_info["update_settings"]:
            # remove all the elements in the specified expander row 
            row = self.settingsrows[(handler.key, self.convert_constants(constants))]["row"]
            setting_list = self.settingsrows[(handler.key, self.convert_constants(constants))]["extra_settings"]
            for setting_row in setting_list:
                row.remove(setting_row)
            self.add_extra_settings(constants, handler, row)

    def setting_change_entry(self, entry, constants, handler : LLMHandler | TTSHandler | STTHandler | AvatarHandler | TranslatorHandler | SmartPromptHandler):
        """ Called when an entry handler setting is changed 

        Args:
            entry (): the entry whose contents are changed
            constants : The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            handler: An instance of the specified handler
        """
        name = entry.get_name()
        handler.set_setting(name, entry.get_text())
        self.on_setting_change(constants, handler, name)

    def setting_change_toggle(self, toggle, state, constants, handler):
        """Called when a toggle for the handler setting is triggered

        Args:
            toggle (): the specified toggle 
            state (): state of the toggle
            constants (): The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            handler (): an instance of the handler
        """
        enabled = toggle.get_active()
        handler.set_setting(toggle.get_name(), enabled)
        self.on_setting_change(constants, handler, toggle.get_name())

    def setting_change_scale(self, scale, scroll, value, constants, handler):
        """Called when a scale for the handler setting is changed

        Args:
            scale (): the changed scale
            scroll (): scroll value
            value (): the value 
            constants (): The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            handler (): an instance of the handler
        """
        setting = scale.get_name()
        digits = scale.get_round_digits()
        value = round(value, digits)
        self.slider_labels[scale].set_label(str(value))
        handler.set_setting(setting, value)
        self.on_setting_change(constants, handler, setting)

    def setting_change_combo(self, helper, value, constants, handler):
        """Called when a combo for the handler setting is changed

        Args:
            helper (): combo row helper 
            value (): chosen value
            constants (): The constants for the specified handler, can be AVAILABLE_TTS, AVAILABLE_STT...
            handler (): an instance of the handler
        """
        setting = helper.combo.get_name()
        handler.set_setting(setting, value)
        self.on_setting_change(constants, handler, setting)

    def add_download_button(self, handler : TTSHandler | STTHandler | LLMHandler | AvatarHandler | TranslatorHandler | SmartPromptHandler, row : Adw.ActionRow | Adw.ExpanderRow): 
        """Add download button for an handler dependencies. If clicked it will call handler.install()

        Args:
            handler: an instance of the handler
            row: row where to add teh button
        """
        actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        if not handler.is_installed():
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
            actionbutton.connect("clicked", self.install_model, handler)
            actionbutton.add_css_class("accent")
            actionbutton.set_child(icon)
            if type(row) is Adw.ActionRow:
                row.add_suffix(actionbutton)
            elif type(row) is Adw.ExpanderRow:
                row.add_action(actionbutton)

    def add_flatpak_waning_button(self, handler : LLMHandler | TTSHandler | STTHandler | AvatarHandler | TranslatorHandler | SmartPromptHandler, row : Adw.ExpanderRow | Adw.ActionRow | Adw.ComboRow):
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

    def install_model(self, button, handler):
        """Display a spinner and trigger the dependency download on another thread

        Args:
            button (): the specified button
            handler (): handler of the model
        """
        spinner = Gtk.Spinner(spinning=True)
        button.set_child(spinner)
        button.set_sensitive(False)
        t = threading.Thread(target=self.install_model_async, args= (button, handler))
        t.start()

    def install_model_async(self, button, model):
        """Install the model dependencies, called on another thread

        Args:
            button (): button  
            model (): a handler instance
        """
        model.install()
        if model.is_installed():
            self.on_setting_change(self.get_constants_from_object(model), model, "", True)
        button.set_child(None)
        button.set_sensitive(False)
        checkbutton = self.settingsrows[(model.key, self.convert_constants(self.get_constants_from_object(model)))]["button"]
        checkbutton.set_sensitive(True)
    
    def refresh_models(self, action):
        """Refresh local models for LLM

        Args:
            action (): 
        """
        models = GPT4All.list_models()
        self.settings.set_string("available-models", json.dumps(models))
        self.local_models = models

    def build_local(self):
        """Build the settings for local models"""
        # Reload available models
        if len(self.local_models) == 0:
            self.refresh_models(None)


        radio = Gtk.CheckButton()
        
        # Create refresh button
        actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="update-symbolic"))
        actionbutton.connect("clicked", self.refresh_models)
        actionbutton.add_css_class("accent")
        actionbutton.set_child(icon)
        self.llmrow.add_action(actionbutton)
        
        # Add extra settings
        self.add_extra_settings(AVAILABLE_LLMS,self.gpt,self.llmrow)
        for row in self.settingsrows["local", self.convert_constants(AVAILABLE_LLMS)]["extra_settings"]:
            if row.get_name() == "custom_model":
                button = Gtk.CheckButton()
                button.set_group(radio)
                button.set_active(self.settings.get_string("local-model") == "custom")
                button.set_name("custom")
                button.connect("toggled", self.choose_local_model)
                row.add_prefix(button)
                if len(self.gpt.get_custom_model_list()) == 0:
                    button.set_sensitive(False)
        # Create entries
        self.rows = {}
        self.model_threads = {}
         
        for model in self.local_models:
            available = self.gpt.model_available(model["filename"])
            active = False
            if model["filename"] == self.settings.get_string("local-model"):
                active = True
            # Write model description
            subtitle = _(" RAM Required: ") + str(model["ramrequired"]) + "GB"
            subtitle += "\n" + _(" Parameters: ") + model["parameters"]
            subtitle += "\n" + _(" Size: ") + human_readable_size(model["filesize"], 1)
            subtitle += "\n" + re.sub('<[^<]+?>', '', model["description"]).replace("</ul", "")
            # Configure buttons and model's row
            r = Adw.ActionRow(title=model["name"], subtitle=subtitle)
            button = Gtk.CheckButton()
            button.set_group(radio)
            button.set_active(active)
            button.set_name(model["filename"])
            button.connect("toggled", self.choose_local_model)
            # TOFIX: Causes some errors sometimes
            button.set_sensitive(available)
            actionbutton = Gtk.Button(css_classes=["flat"],
                                                valign=Gtk.Align.CENTER)
            if available:
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
                actionbutton.connect("clicked", self.remove_local_model)
                actionbutton.add_css_class("error")
            else:
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
                actionbutton.connect("clicked", self.download_local_model)
                actionbutton.add_css_class("accent")
            actionbutton.set_child(icon)
            icon.set_icon_size(Gtk.IconSize.INHERIT)

            actionbutton.set_name(model["filename"])

            self.rows[model["filename"]] = {"radio": button}

            r.add_prefix(button)
            r.add_suffix(actionbutton)
            self.llmrow.add_row(r)

    def choose_local_model(self, button):
        """Called when a local model is chosen

        Args:
            button (): 
        """
        if button.get_active():
            self.settings.set_string("local-model", button.get_name())

    def download_local_model(self, button):
        """Download the local model. Shows the progress while downloading

        Args:
            button (): button pressed
        """
        model = button.get_name()
        box = Gtk.Box(homogeneous=True, spacing=4)
        box.set_orientation(Gtk.Orientation.VERTICAL)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        progress = Gtk.ProgressBar(hexpand=False)
        progress.set_size_request(4, 4)
        box.append(icon)
        box.append(progress)
        button.set_child(box)
        button.disconnect_by_func(self.download_local_model)
        button.connect("clicked", self.remove_local_model)
        th = threading.Thread(target=self.download_model_thread, args=(model, button, progress))
        self.model_threads[model] = [th, 0]
        th.start()

    def update_download_status(self, model, filesize, progressbar):
        """Periodically update the progressbar for the download

        Args:
            model (): model that is being downloaded
            filesize (): filesize of the download
            progressbar (): the bar to update
        """
        file = os.path.join(self.gpt.modelspath, model) + ".part"
        while model in self.downloading and self.downloading[model]:
            try:
                currentsize = os.path.getsize(file)
                perc = currentsize/int(filesize)
                progressbar.set_fraction(perc)
            except Exception as e:
                print(e)
            time.sleep(1)

    def download_model_thread(self, model, button, progressbar):
        """Create the thread that downloads the local model

        Args:
            model (): model to download 
            button (): button to udpate
            progressbar (): progressbar to udpate
        """
        for x in self.local_models:
            if x["filename"] == model:
                filesize = x["filesize"]
                break
        self.model_threads[model][1] = threading.current_thread().ident
        self.downloading[model] = True
        th = threading.Thread(target=self.update_download_status, args=(model, filesize, progressbar))
        th.start()
        self.gpt.download_model(model)
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        button.add_css_class("error")
        button.set_child(icon)
        self.downloading[model] = False
        self.rows[model]["radio"].set_sensitive(True)

    def remove_local_model(self, button):
        """Remove a local model

        Args:
            button (): button for the local model
        """
        model = button.get_name()
        # Kill threads if stopping download
        if model in self.downloading and self.downloading[model]:
            self.downloading[model] = False
            if model in self.model_threads:
                thid = self.model_threads[model][1]
                # NOTE: This does only work on Linux
                res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thid), ctypes.py_object(SystemExit))
                if res > 1:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thid), 0)
        try:
            os.remove(os.path.join(self.gpt.modelspath, model))
            button.add_css_class("accent")
            if model in self.downloading:
                self.downloading[model] = False
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
            button.disconnect_by_func(self.remove_local_model)
            button.connect("clicked", self.download_local_model)
            button.add_css_class("accent")
            button.remove_css_class("error")
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            button.set_child(icon)
        except Exception as e:
            print(e)

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
        wbbutton.connect("clicked", self.open_website)
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
        dialog.set_extra_child(CopyBox("flatpak --user override --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle", "bash", parent = self))
        dialog.set_close_response("close")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', lambda dialog, response_id: dialog.destroy())
        # Show the window
        dialog.present()



class TextItemFactory(Gtk.ListItemFactory):
    def create_widget(self, item):
        label = Gtk.Label()
        return label

    def bind_widget(self, widget, item):
        widget.set_text(item)

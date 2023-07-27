import gi
import re, threading, os, json, time, ctypes, subprocess
from gi.repository import Gtk, Adw, Gio, GLib
from .constants import AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT
from gpt4all import GPT4All
from .llm import GPT4AllHandler
from .gtkobj import ComboRowHelper


def human_readable_size(size, decimal_places=2):
    size = int(size)
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

class Settings(Adw.PreferencesWindow):
    def __init__(self,app, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        self.set_transient_for(app.win)
        self.set_modal(True)
        self.downloading = {}

        self.local_models = json.loads(self.settings.get_string("available-models"))
        self.directory = GLib.get_user_config_dir()
        self.gpt = GPT4AllHandler(self.settings, os.path.join(self.directory, "models"), AVAILABLE_LLMS["local"])

        self.general_page = Adw.PreferencesPage()

        # LLM
        self.LLM = Adw.PreferencesGroup(title=_('Language Model'))
        self.general_page.add(self.LLM)
        self.llmbuttons = [];
        group = Gtk.CheckButton()
        for model_key in AVAILABLE_LLMS:
            model = AVAILABLE_LLMS[model_key]
            active = False
            if model["key"] == self.settings.get_string("language-model"):
                active = True
            if model["rowtype"] == "action":
                row = Adw.ActionRow(title=model["title"], subtitle=model["description"])
            elif model["rowtype"] == "expander":
                row = Adw.ExpanderRow(title=model["title"], subtitle=model["description"])
                if model["key"] == "local":
                    self.llmrow = row
                    thread = threading.Thread(target=self.build_local)
                    thread.start()
                else:
                    self.add_extra_settings(model, row, "llm")
            if len(model["extra_requirements"]) > 0:
                self.add_download_button(model, row, "llm")
            button = Gtk.CheckButton()
            button.set_group(group)
            button.set_active(active)
            button.set_name(model["key"])
            button.connect("toggled", self.choose_llm)
            row.add_prefix(button)
            self.LLM.add(row)

        # TTS
        self.TTSgroup = Adw.PreferencesGroup(title=_('Text To Speech'))
        self.general_page.add(self.TTSgroup)
        tts_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("tts-on", tts_enabled, 'active', Gio.SettingsBindFlags.DEFAULT)

        tts_program = Adw.ExpanderRow(title=_('Text To Speech Program'), subtitle=_("Choose which text to speech to use"))
        tts_program.add_action(tts_enabled)
        self.TTSgroup.add(tts_program)
        group = Gtk.CheckButton()
        for tts_key in AVAILABLE_TTS:
            active = False
            tts = AVAILABLE_TTS[tts_key]
            tts["key"] = tts_key
            if tts["key"] == self.settings.get_string("tts"):
                active = True
            if tts["rowtype"] == "action":
                row = Adw.ActionRow(title=tts["title"], subtitle=tts["description"])
            elif tts["rowtype"] == "expander":
                row = Adw.ExpanderRow(title=tts["title"], subtitle=tts["description"])
                if len(tts["extra_settings"]) > 0:
                    self.add_tts_settings(tts, row)
            elif tts["rowtype"] == "combo":
                row = Adw.ComboRow(title=tts["title"], subtitle=tts["description"])
                row.set_name(tts["key"])
                tts_class = tts["class"](self.settings, self.directory, tts)
                helper = ComboRowHelper(row, tts_class.get_voices(), tts_class.get_current_voice())
                helper.connect("changed", self.choose_tts_voice)
            button = Gtk.CheckButton()
            button.set_group(group)
            button.set_active(active)
            button.set_name(tts["key"])
            button.connect("toggled", self.choose_tts)
            row.add_prefix(button)
            tts_program.add_row(row)

        # Speech To Text
        self.STTgroup = Adw.PreferencesGroup(title=_('Speech to Text'))
        self.general_page.add(self.STTgroup)

        stt_engine = Adw.ExpanderRow(title=_('Speech To Text Engine'), subtitle=_("Choose which speech recognition engine you want"))
        self.STTgroup.add(stt_engine)
        group = Gtk.CheckButton()
        for stt_key in AVAILABLE_STT:
            active = False
            stt = AVAILABLE_STT[stt_key]
            stt["key"] = stt_key
            if stt_key == self.settings.get_string("stt-engine"):
                active = True
            if stt["rowtype"] == "action":
                row = Adw.ActionRow(title=stt["title"], subtitle=stt["description"])
            elif stt["rowtype"] == "expander":
                row = Adw.ExpanderRow(title=stt["title"], subtitle=stt["description"])
                self.add_extra_settings(stt, row, "stt")
            elif stt["rowtype"] == "combo":
                row = Adw.ComboRow(title=stt["title"], subtitle=stt["description"])
                """
                row.set_name(tts["key"])
                tts_class = tts["class"](self.settings, self.directory)
                helper = ComboRowHelper(row, tts_class.get_voices(), tts_class.get_current_voice())
                helper.connect("changed", self.choose_tts_voice)
                """
            button = Gtk.CheckButton()
            button.set_group(group)
            button.set_active(active)
            button.set_name(stt_key)
            button.connect("toggled", self.choose_stt)
            row.add_prefix(button)
            row.set_name(stt_key)
            stt_engine.add_row(row)
            if "website" in stt:
                wbbutton = self.create_web_button(stt["website"])
                if stt["rowtype"] == "action":
                    row.add_suffix(wbbutton)
                elif stt["rowtype"] == "expander":
                    row.add_action(wbbutton)
            if len(stt["extra_requirements"]) > 0:
                self.add_download_button(stt, row, "stt")

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

        self.prompt = Adw.PreferencesGroup(title=_('Prompt control'))
        self.general_page.add(self.prompt)

        row = Adw.ActionRow(title=_("Auto-run commands"), subtitle=_("Commands that the bot will write will automatically run"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("auto-run", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title=_("Console access"), subtitle=_("Can the program run terminal commands on the computer"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("console", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title=_("Graphs access"), subtitle=_("Can the program display graphs"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("graphic", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title=_("Basic functionality"), subtitle=_("Showing tables and code (*can work without it)"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("basic-functionality", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title=_("Show image"), subtitle=_("Show image in chat"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("show-image", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        self.neural_network = Adw.PreferencesGroup(title=_('Neural Network Control'))
        self.general_page.add(self.neural_network)

        row = Adw.ActionRow(title=_("Command virtualization"), subtitle=_("Run commands in a virtual machine"))
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("virtualization", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.neural_network.add(row)

        row = Adw.ActionRow(title=_("Program memory"), subtitle=_("How long the program remembers the chat "))
        int_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        int_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=30, step_increment=1, page_increment=10, page_size=0))
        row.add_suffix(int_spin)
        self.settings.bind("memory", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.neural_network.add(row)

        self.message = Adw.PreferencesGroup(title=_('The change will take effect after you restart the program.'))
        self.general_page.add(self.message)

        self.add(self.general_page)

    def open_website(self, button):
        subprocess.Popen(["flatpak-spawn", "--host", "xdg-open", button.get_name()])

    def add_extra_settings(self, m, row, mtype):
        if mtype == "stt":
            model = m["class"](self.settings, os.path.join(self.directory, "pip"), m)
        elif mtype == "llm":
            model = m["class"](self.settings,os.path.join(self.directory, "models"), m)
        for setting in m["extra_settings"]:
            r = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
            if "website" in setting:
                wbbutton = self.create_web_button(setting["website"])
                r.add_prefix(wbbutton)
            if setting["type"] == "entry":
                entry = Gtk.Entry()
                entry.set_valign(Gtk.Align.CENTER)
                value = model.get_setting(setting["key"])
                if value is None:
                    value = setting["default"]
                entry.set_text(value)
                entry.set_name(mtype + "//" + m["key"] + "//" + setting["key"])
                entry.connect("changed", self.setting_change)
                r.add_suffix(entry)
            row.add_row(r)

    def add_tts_settings(self, tts, row):
        model = tts["class"](self.settings, self.directory, tts)
        for setting in tts["extra_settings"]:
            r = Adw.ActionRow(title=setting["title"], subtitle=setting["description"])
            if "website" in setting:
                self.create_web_button(setting["website"])
            if setting["type"] == "entry":
                entry = Gtk.Entry()
                entry.set_valign(Gtk.Align.CENTER)
                value = model.get_setting(setting["key"])
                if value is None:
                    value = setting["default"]
                entry.set_text(value)
                entry.set_name(tts["key"] + "//" + setting["key"])
                entry.connect("changed", self.tts_setting_change)
                r.add_suffix(entry)
            row.add_row(r)

    def tts_setting_change(self, entry):
        name = entry.get_name().split("//")
        key = name[0]
        setting = name[1]
        model = AVAILABLE_TTS[key]["class"](self.settings, self.directory, AVAILABLE_TTS[key])
        model.set_setting(setting, entry.get_text())

    def stt_setting_change(self, entry):
        name = entry.get_name().split("//")
        key = name[0]
        setting = name[1]
        model = AVAILABLE_STT[key]["class"](self.settings, os.path.join(self.directory, "pip"), AVAILABLE_STT[key])
        model.set_setting(setting, entry.get_text())

    def setting_change(self, entry):
        name = entry.get_name().split("//")
        mtype = name[0]
        key = name[1]
        setting = name[2]
        if mtype == "stt":
            model = AVAILABLE_STT[key]["class"](self.settings, os.path.join(self.directory, "pip"), AVAILABLE_STT[key])
            model.set_setting(setting, entry.get_text())
        else:
            model = AVAILABLE_LLMS[key]["class"](self.settings, os.path.join(self.directory, "model"), AVAILABLE_LLMS[key])
            model.set_setting(setting, entry.get_text())

    def add_download_button(self, model, row, mtype):
        if mtype == "stt":
            m = model["class"](self.settings, os.path.join(self.directory, "pip"), model)
        elif mtype == "llm":
            m = model["class"](self.settings, os.path.join(self.directory, "models"), model)
        actionbutton = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        if not m.is_installed():
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
            actionbutton.connect("clicked", self.install_model)
            actionbutton.add_css_class("accent")
            actionbutton.set_child(icon)
            actionbutton.set_name(mtype + "//" + model["key"])
            if model["rowtype"] == "action":
                row.add_suffix(actionbutton)
            elif model["rowtype"] == "expander":
                row.add_action(actionbutton)

    def install_model(self, button):
        name = button.get_name()
        mtype = name.split("//")[0]
        key = name.split("//")[1]
        if mtype == "stt":
            stt = AVAILABLE_STT[key]
            model = stt["class"](self.settings, os.path.join(self.directory, "pip"), stt)
        elif mtype == "llm":
            llm = AVAILABLE_LLMS[key]
            model = llm["class"](self.settings, os.path.join(self.directory, "models"), llm)
        spinner = Gtk.Spinner(spinning=True)
        button.set_child(spinner)
        t = threading.Thread(target=self.install_model_async, args= (button, model))
        t.start()
    def install_model_async(self, button, model):
        model.install()
        button.set_child(None)
        button.set_sensitive(False)

    def build_local(self):
        # Reload available models
        if len(self.local_models) == 0:
            models = GPT4All.list_models()
            self.settings.set_string("available-models", json.dumps(models))
            self.local_models = models
        radio = Gtk.CheckButton()
        self.rows = {}
        self.model_threads = {}
        for model in self.local_models:
            available = self.gpt.model_available(model["filename"])
            active = False
            if model["filename"] == self.settings.get_string("local-model"):
                active = True
            # Write model description
            subtitle = _("RAM Required: ") + str(model["ramrequired"]) + "GB"
            subtitle += "\n" + _("Parameters: ") + model["parameters"]
            subtitle += "\n" + _("Size: ") + human_readable_size(model["filesize"], 1)
            subtitle += "\n" + re.sub('<[^<]+?>', '', model["description"]).replace("</ul", "")
            # Configure buttons and model's row
            r = Adw.ActionRow(title=model["name"], subtitle=subtitle)
            button = Gtk.CheckButton()
            button.set_group(radio)
            button.set_active(active)
            button.set_name(model["filename"])
            button.connect("toggled", self.choose_local_model)
            # TOFIX: Causes some errors sometimes
            #button.set_sensitive(available)
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

    def choose_llm(self, button):
        if button.get_active():
            self.settings.set_string("language-model", button.get_name())
    def choose_local_model(self, button):
        if button.get_active():
            self.settings.set_string("local-model", button.get_name())

    def choose_tts(self, button):
        if button.get_active():
            self.settings.set_string("tts", button.get_name())

    def choose_stt(self, button):
        if button.get_active():
            self.settings.set_string("stt-engine", button.get_name())

    def choose_tts_voice(self, helper, value):
        tts = AVAILABLE_TTS[helper.combo.get_name()]["class"](self.settings, self.directory, AVAILABLE_TTS[helper.combo.get_name()])
        tts.set_voice(value)

    def download_local_model(self, button):
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
        file = os.path.join(self.gpt.modelspath, model)
        while model in self.downloading and self.downloading[model]:
            try:
                currentsize = os.path.getsize(file)
                perc = currentsize/int(filesize)
                progressbar.set_fraction(perc)
            except Exception as e:
                print(e)
            time.sleep(1)

    def download_model_thread(self, model, button, progressbar):
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

    def remove_local_model(self, button):
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

    def create_web_button(self, website):
        wbbutton = Gtk.Button(icon_name="internet-symbolic")
        wbbutton.add_css_class("flat")
        wbbutton.set_valign(Gtk.Align.CENTER)
        wbbutton.set_name(website)
        wbbutton.connect("clicked", self.open_website)
        return wbbutton


class TextItemFactory(Gtk.ListItemFactory):
    def create_widget(self, item):
        label = Gtk.Label()
        return label

    def bind_widget(self, widget, item):
        widget.set_text(item)

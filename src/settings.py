import gi
import re, threading, os, json, time
from gi.repository import Gtk, Adw, Gio, GLib
from .constants import AVAILABLE_LLMS
from gpt4all import GPT4All
from .localmodels import GPT4AllHandler


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
        self.gpt = GPT4AllHandler(self.settings, os.path.join(self.directory, "models"))

        self.general_page = Adw.PreferencesPage()

        self.LLM = Adw.PreferencesGroup(title=_('Language Model'))
        self.general_page.add(self.LLM)
        self.llmbuttons = [];
        group = Gtk.CheckButton()
        for model in AVAILABLE_LLMS:
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
            button = Gtk.CheckButton()
            button.set_group(group)
            button.set_active(active)
            row.add_prefix(button)
            self.LLM.add(row)

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

    def build_local(self):
        # Reload available models
        if len(self.local_models) == 0:
            models = GPT4All.list_models()
            self.settings.set_string("available-models", json.dumps(models))
            self.local_models = models
        radio = Gtk.CheckButton()
        self.rows = {}
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
        button.connect("clicked", self.remove_local_model)
        th = threading.Thread(target=self.download_model_thread, args=(model, button, progress))
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
        self.downloading[model] = True
        th = threading.Thread(target=self.update_download_status, args=(model, filesize, progressbar))
        th.start()
        self.gpt.download_model(model)
        self.downloading[model] = False
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        button.add_css_class("error")
        button.set_child(icon)

    def remove_local_model(self, button):
        model = button.get_name()
        try:
            os.remove(os.path.join(self.gpt.modelspath, model))
            button.add_css_class("accent")
            if model in self.downloading:
                self.downloading[model] = False
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download-symbolic"))
            button.connect("clicked", self.download_local_model)
            button.add_css_class("accent")
            button.remove_css_class("error")
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            button.set_child(icon)
        except Exception as e:
            print(e)

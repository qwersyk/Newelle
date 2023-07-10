import gi
from gi.repository import Gtk, Adw, Gio


class Settings(Adw.PreferencesWindow):
    def __init__(self,app, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        self.set_transient_for(app.win)
        self.set_modal(True)

        self.general_page = Adw.PreferencesPage()
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

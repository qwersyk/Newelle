import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio


class Settings(Adw.PreferencesWindow):
    def __init__(self,app, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        self.set_transient_for(app.win)
        self.set_modal(True)

        self.general_page = Adw.PreferencesPage()
        self.interface = Adw.PreferencesGroup(title='Interface')
        self.general_page.add(self.interface)

        row = Adw.ActionRow(title="Sidebar", subtitle="Show the explorer panel")
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("file-panel", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title="Hidden files", subtitle="Show hidden files")
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("hidden-files", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        row = Adw.ActionRow(title="Number of offers", subtitle="Number of message suggestions to send to chat ")
        int_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        int_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=5, step_increment=1, page_increment=10, page_size=0))
        row.add_suffix(int_spin)
        self.settings.bind("offers", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.interface.add(row)

        self.prompt = Adw.PreferencesGroup(title='Prompt control')
        self.general_page.add(self.prompt)

        row = Adw.ActionRow(title="Console access", subtitle="Can the program run terminal commands on the computer")
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("console", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title="Internet access", subtitle="Can the program search the Internet", sensitive=False)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("search", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title="Graphs access", subtitle="Can the program display graphs")
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("graphic", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        self.neural_network = Adw.PreferencesGroup(title='Neural Network Control')
        self.general_page.add(self.neural_network)

        row = Adw.ActionRow(title="Command virtualization", subtitle="Run commands in a virtual machine")
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("virtualization", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.neural_network.add(row)

        row = Adw.ActionRow(title="Program memory", subtitle="How long the program remembers the chat ")
        int_spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
        int_spin.set_adjustment(Gtk.Adjustment(lower=0, upper=30, step_increment=1, page_increment=10, page_size=0))
        row.add_suffix(int_spin)
        self.settings.bind("memory", int_spin, 'value', Gio.SettingsBindFlags.DEFAULT)
        self.neural_network.add(row)

        self.message = Adw.PreferencesGroup(title='The change will take effect after you restart the program.')
        self.general_page.add(self.message)

        self.add(self.general_page)

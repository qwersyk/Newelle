import threading
import subprocess
import os
from gi.repository import GLib, Gtk, GtkSource, Gio, Pango, Gdk
from ...utility.system import get_spawn_command 
from ...utility.strings import add_S_to_sudo, quote_string
from .terminal_dialog import TerminalDialog

class CopyBox(Gtk.Box):
    def __init__(self, txt, lang, parent = None,id_message=-1, id_codeblock=-1, allow_edit=False, color_scheme=None):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10, css_classes=["osd", "toolbar", "code"])
        if color_scheme is None and hasattr(parent, "controller"):
            self.color_scheme = parent.controller.newelle_settings.editor_color_scheme
        else:
            self.color_scheme = color_scheme if color_scheme is not None else "Adwaita-dark"
        self.txt = txt
        self.parent = parent
        longest_line = max(txt.splitlines(), key=len)
        self.id_message = id_message
        self.id_codeblock = id_codeblock
        box = Gtk.Box(halign=Gtk.Align.END)

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-copy-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
        self.copy_button.set_child(icon)
        self.copy_button.connect("clicked", self.copy_button_clicked)

        self.sourceview = GtkSource.View(width_request=12*len(longest_line), monospace=True)
        self.scroll = Gtk.ScrolledWindow(propagate_natural_width=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)

        if allow_edit:
            self.edit_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="document-edit-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.edit_button.set_child(icon)
            self.edit_button.connect("clicked", self.edit_button_clicked)
            box.append(self.edit_button)

        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(txt, -1)
        
        lang.replace(" ", "")
        display_lang = lang
        replace_lang = [
            (["py", "py3"], "python"),
            (["bash", "shell", "console"], "sh"),
            (["javascript"], "js")
        ]
        for rep in replace_lang:
            if display_lang in rep[0]:
                display_lang = rep[1]
        self.lang = display_lang
        manager = GtkSource.LanguageManager.new()
        language = manager.get_language(display_lang)
        self.buffer.set_language(language)

        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme(self.color_scheme)
        self.buffer.set_style_scheme(style_scheme)

        self.sourceview.set_buffer(self.buffer)
        self.sourceview.set_vexpand(True)
        self.sourceview.set_vexpand(True)
        self.sourceview.set_show_line_numbers(True)
        #self.sourceview.set_background_pattern(GtkSource.BackgroundPatternType.GRID)
        self.sourceview.set_editable(False)
        style = "success"
        if lang in ["python", "python3", "cpp", "php", "objc", "go", "typescript", "lua", "perl", "r", "dart", "sql", "latex"]:
            style = "accent"
        if lang in ["java", "javascript", "kotlin", "rust", "json"]:
            style = "warning"
        if lang in ["ruby", "swift", "scala"]:
            style = "error"
        if lang in ["console"]:
            style = ""
        
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        main.set_homogeneous(True)
        label = Gtk.Label(label=lang, halign=Gtk.Align.START, margin_start=10, css_classes=[style, "heading"],wrap=False, ellipsize=Pango.EllipsizeMode.END)
        main.append(label)
        self.append(main)
        self.scroll.set_child(self.sourceview)
        self.append(self.scroll)
        main.append(box)
        if lang == "python" or lang == "python3" and parent is not None:
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_code, "python")
            self.parent = parent

            self.text_expander = Gtk.Expander(
                label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10,
                margin_end=10
            )
            self.text_expander.set_expanded(False)
            self.text_expander.set_visible(False)
            box.append(self.run_button)
            self.append(self.text_expander)

        elif lang == "console" and parent is not None:
            # Run button
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_console)
            # Run in external terminal button 
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="gnome-terminal-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.terminal_button = Gtk.Button(halign=Gtk.Align.END, margin_end=10, css_classes=["flat"])
            self.terminal_button.set_child(icon)
            self.terminal_button.connect("clicked", self.run_console_terminal)
            
            self.parent = parent

            self.text_expander = Gtk.Expander(
                label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10, margin_bottom=10,
                margin_end=10
            )
            console = "None"
            if id_message<len(self.parent.chat) and self.parent.chat[id_message]["User"]=="Console":
                console = self.parent.chat[id_message]["Message"]
            self.text_expander.set_child(
                Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=console, selectable=True))
            self.text_expander.set_expanded(False)
            box.append(self.run_button)
            box.append(self.terminal_button)
            self.append(self.text_expander)

        box.append(self.copy_button)

    def copy_button_clicked(self, widget):
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.txt))
        self.copy_button.set_icon_name("object-select-symbolic")
        GLib.timeout_add(2000, lambda : self.copy_button.set_icon_name("edit-copy-symbolic"))

    def run_console(self, widget,multithreading=False):
        if multithreading:
            code = self.parent.execute_terminal_command(self.txt)
            self.set_output(code[1])
            def update_ui():
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
                icon.set_icon_size(Gtk.IconSize.INHERIT)
                widget.set_child(icon)
                widget.set_sensitive(True)
            GLib.idle_add(update_ui)
        else:
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            widget.set_child(icon)
            widget.set_sensitive(False)
            threading.Thread(target=self.run_console, args=[widget, True]).start()
    
    def set_output(self, output):
            if self.id_message<len(self.parent.chat) and self.parent.chat[self.id_message]["User"]=="Console":
                self.parent.chat[self.id_message]["Message"] = output
            else:
                self.parent.chat.append({"User": "Console", "Message": " " + output})
            self.text_expander.set_child(
                Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=output, selectable=True))
            if self.parent.status and len(self.parent.chat)-1==self.id_message and self.id_message<len(self.parent.chat) and self.parent.chat[self.id_message]["User"]=="Console":
                self.parent.status = False
                self.parent.update_button_text()
                self.parent.scrolled_chat()
                threading.Thread(target=self.parent.send_message).start()

    def run_console_terminal(self, widget,multithreading=False):
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        widget.set_child(icon)
        widget.set_sensitive(False)
        command = "cd " + quote_string(os.getcwd()) +"; " + self.txt + "; exec bash"
        external_terminal = False
        if external_terminal:
            cmd = self.parent.external_terminal.split() 
            arguments = [s.replace("{0}", command) for s in cmd]
            subprocess.Popen(get_spawn_command() + arguments)
        else:
            terminal = TerminalDialog()
            output_dir = GLib.get_user_cache_dir()
            terminal_output = output_dir + "/terminal.log"
            def save_output(save):
                widget.set_sensitive(True)
                widget.set_icon_name("gnome-terminal-symbolic")
                if save is not None:
                    self.set_output(save)
                else:
                    return

            if not self.parent.virtualization:
                command = add_S_to_sudo(command)
                command = get_spawn_command() + ["bash", "-c", "export TERM=xterm-256color;alias sudo=\"sudo -S\";" + command]
            else:
                command = ["bash", "-c", "export TERM=xterm-256color;" + command]
            terminal.load_terminal(command)
            terminal.save_output_func(save_output)
            terminal.present()


    def run_code(self, widget, language, mutlithreading=False):
        if mutlithreading:
            if language.lower() in ["python", "python3", "py"]:
                code = self.parent.execute_terminal_command("python3 -c {}".format(quote_string(self.txt)))

            else:
                code = "ae"
            self.set_output(code[1])
            def update_ui():
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
                icon.set_icon_size(Gtk.IconSize.INHERIT)
                widget.set_child(icon)
                widget.set_sensitive(True)
            GLib.idle_add(update_ui)
        else:
            self.text_expander.set_visible(True)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            widget.set_child(icon)
            widget.set_sensitive(False)
            threading.Thread(target=self.run_code, args=[widget,language, True]).start()
            
    def edit_button_clicked(self, button):
        if self.parent is not None:
            self.parent.add_editor_tab_inline(self.id_message, self.id_codeblock, self.txt, self.lang)

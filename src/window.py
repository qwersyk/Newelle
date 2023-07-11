import time, re
import gi, os, subprocess

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import pickle
from .gtkobj import File, CopyBox, BarChartBox
from .bai import BAIChat
from gi.repository import Gtk, Adw, Pango, Gio, Gdk, GObject
import threading
import posixpath
import shlex,json

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_default_size(1400, 800) #(1500, 800) to show everything
        self.main_program_block = Adw.Flap(flap_position=Gtk.PackType.END,modal=False,swipe_to_close=False,swipe_to_open=False)
        self.main_program_block.set_name("hide")


        self.path = ".var/app/io.github.qwersyk.Newelle/data"
        if not os.path.exists(self.path):
            os.makedirs(self.path)
        self.filename = "chats.pkl"
        if os.path.exists(self.path + self.filename):
            with open(self.path + self.filename, 'rb') as f:
                self.chats = pickle.load(f)
        else:
            self.chats = [{"name": "Chat 1", "chat": []}]

        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        self.offers = settings.get_int("offers")
        self.virtualization = settings.get_boolean("virtualization")
        self.memory = settings.get_int("memory")
        self.console = settings.get_boolean("console")
        self.hidden_files = settings.get_boolean("hidden-files")
        self.chat_id = settings.get_int("chat")
        self.main_path = settings.get_string("path")
        self.auto_run = settings.get_boolean("auto-run")
        self.chat = self.chats[min(self.chat_id,len(self.chats)-1)]["chat"]
        self.graphic = settings.get_boolean("graphic")
        self.basic_functionality = settings.get_boolean("basic-functionality")
        self.show_image = settings.get_boolean("show-image")

        self.bot_prompt = """"""
        if self.console:
            self.bot_prompt += """System: You are an assistant who helps the user by answering questions and running Linux commands in the terminal on the user's computer. Use two types of messages: "Assistant: text" to answer questions and communicate with the user, and "Assistant: ```console\ncommand\n```" to execute commands on the user's computer. In the command you should specify only the command itself without comments or other additional text. Your task is to minimize the information and leave only the important. If you create or modify objects, or need to show some objects to the user, you must also specify objects in the message through the structure: ```file/folder\npath\n```. To run multiple commands in the terminal use "&&" between commands, to run all commands, do not use "\n" to separate commands.
User: Create an image 100x100 pixels
Assistant: ```console
convert -size 100x100 xc:white image.png
```
Console: Done
Assistant: The image has been created:
```image
./image.png
```

User: Open YouTube
Assistant: ```console
xdg-open https://www.youtube.com
```
Console: Done
Assistant:

User: Create folder
Assistant: ```console
mkdir folder
```
Console: Done
Assistant: The folder has been created:
```folder
./folder
```

User: What day of the week it is
Assistant: ```console
date +%A
```
Console: Tuesday
Assistant: Today is Tuesday.

User: What's the error in file 1.py
Assistant: ```console
cat 1.py
```
Console: print(math.pi)
Assistant: The error is that you forgot to import the math module

User: Create a folder and create a git project inside it.
Assistant: ```console\nmkdir folder && cd folder && git init\n```

"""
        if self.basic_functionality:
            self.bot_prompt += """User: Write the multiplication table 4 by 4
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |

User: Write example c++ code
Assistant: ```cpp\n#include<iostream>\nusing namespace std;\nint main(){\n    cout<<"Hello world!";\n    return 0;\n}\n```

User: Write example js code
Assistant: ```js\nconsole.log("Hello world!");\n```

User: Write example python code
Assistant: ```python\npython("Hello world!")\n```
User: Run this code
Assistant: ```console\npython3 -c "print('Hello world!')"\n```
"""
        if self.show_image:
            self.bot_prompt +="""System: You can also show the user an image, if needed, through a syntax like '```image\npath\n```'
"""
        if self.graphic:
            self.bot_prompt += """System: You can display the graph using this structure: ```chart\n name - value\n ... \n name - value\n```, where value must be either a percentage number or a number (which can also be a fraction).
User: Write which product Apple sold the most in 2019, which less, etc.
Assistant: ```chart\niPhone - 60%\nMacBook - 15%\niPad - 10%\nApple Watch - 10%\niMac - 5%\n```\nIn 2019, Apple sold the most iPhones.
"""
        if self.graphic and self.console:
            self.bot_prompt+="""File: /home/user/Downloads/money.txt
User: Create a graph for the report in the money.txt file
Assistant: ```console\ncat /home/user/Downloads/money.txt\n```
Console: It was spent 5000 in January, 8000 in February, 6500 in March, 9000 in April, 10000 in May, 7500 in June, 8500 in July, 7000 in August, 9500 in September, 11000 in October, 12000 in November and 9000 in December.
Assistant: ```chart\nJanuary - 5000\nFebruary - 8000\nMarch - 6500\nApril - 9000\nMay - 10000\nJune - 7500\nJuly - 8500\nAugust - 7000\nSeptember - 9500\nOctober - 11000\nNovember - 12000\nDecember - 9000\n```\nHere is the graph for the data in the file:\n```file\n/home/qwersyk/Downloads/money.txt\n```
"""
        self.extension_path = os.path.expanduser("~")+"/.var/app/io.github.qwersyk.Newelle/extension"
        self.extensions = {}
        if os.path.exists(self.extension_path):
            folder_names = [name for name in os.listdir(self.extension_path) if os.path.isdir(os.path.join(self.extension_path, name))]
            for name in folder_names:
                main_json_path = os.path.join(self.extension_path, name, "main.json")
                if os.path.exists(main_json_path):
                    with open(main_json_path, "r") as file:
                        main_json_data = json.load(file)
                        prompt = main_json_data.get("prompt")
                        name = main_json_data.get("name")
                        status = main_json_data.get("status")
                        api = main_json_data.get("api")
                        if api != None:
                            self.extensions[name] = {"api":api,"status":status,"prompt": prompt}
        if os.path.exists(os.path.expanduser(self.main_path)):
            os.chdir(os.path.expanduser(self.main_path))
        else:
            self.main_path="~"
        self.set_titlebar(Gtk.Box())
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
        self.chat_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.chat_header = Adw.HeaderBar(css_classes=["flat"])
        self.chat_header.set_title_widget(Gtk.Label(label=_("Chat"), css_classes=["title"]))
        self.flap_button_left = Gtk.ToggleButton.new()
        self.flap_button_left.set_icon_name(icon_name='sidebar-show-right-symbolic')
        self.flap_button_left.connect('clicked', self.on_flap_button_toggled)
        self.chat_header.pack_end(child=self.flap_button_left)



        self.left_panel_back_button = Gtk.Button(css_classes=["flat"])
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

        self.main = Adw.Leaflet(fold_threshold_policy=True, can_navigate_back=True, can_navigate_forward=True)
        self.streams=[]
        self.chats_main_box = Gtk.Box(hexpand_set=True)
        self.chats_main_box.set_size_request(300, -1)
        self.chats_secondary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.chat_panel_header = Adw.HeaderBar(css_classes=["flat"])
        self.chat_panel_header.set_title_widget(Gtk.Label(label=_("History"), css_classes=["title"]))
        self.chats_secondary_box.append(self.chat_panel_header)
        self.chats_secondary_box.append(Gtk.Separator())
        self.chat_panel_header.pack_end(menu_button)
        self.chats_buttons_block = Gtk.ListBox(css_classes=["separators","background"])
        self.chats_buttons_block.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chats_buttons_scroll_block = Gtk.ScrolledWindow(vexpand=True)
        self.chats_buttons_scroll_block.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chats_buttons_scroll_block.set_child(self.chats_buttons_block)
        self.chats_secondary_box.append(self.chats_buttons_scroll_block)
        button = Gtk.Button(valign=Gtk.Align.END,css_classes=["suggested-action"], margin_start=6, margin_end=6,  margin_top=6, margin_bottom=6)
        button.set_child(Gtk.Label(label=_("Create a chat")))
        button.connect("clicked", self.new_chat)
        self.chats_secondary_box.append(button)
        self.chats_main_box.append(self.chats_secondary_box)
        self.chats_main_box.append(Gtk.Separator())
        self.main.append(self.chats_main_box)
        self.main.append(self.chat_panel)
        self.main.set_visible_child(self.chat_panel)
        self.explorer_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["background"])
        self.explorer_panel.set_size_request(420, -1)
        self.explorer_panel_header = Adw.HeaderBar(css_classes=["flat"])
        self.explorer_panel.append(self.explorer_panel_header)
        self.folder_blocks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.explorer_panel.append(self.folder_blocks_panel)
        self.set_child(self.main_program_block)
        self.main_program_block.set_content(self.main)
        self.main_program_block.set_flap(self.explorer_panel)
        self.secondary_message_chat_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        self.chat_block.append(self.secondary_message_chat_block)
        self.chat_list_block = Gtk.ListBox(css_classes=["separators","background"])
        self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chat_scroll = Gtk.ScrolledWindow(vexpand=True)
        self.chat_scroll_window = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,css_classes=["background"])
        self.chat_scroll.set_child(self.chat_scroll_window)
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
        drop_target.connect('drop', self.handle_file_drag)
        self.chat_scroll.add_controller(drop_target)
        self.chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chat_scroll_window.append(self.chat_list_block)
        self.notification_block = Adw.ToastOverlay()
        self.notification_block.set_child(self.chat_scroll)

        self.secondary_message_chat_block.append(self.notification_block)
        self.chat_controls_entry_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,vexpand=True,valign=Gtk.Align.END)
        self.chat_scroll_window.append(self.chat_controls_entry_block)
        self.message_suggestion_buttons_array = []

        self.chat_stop_button = Gtk.Button(css_classes=["flat","right-angles"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-stop"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Stop"))
        box.append(label)
        self.chat_stop_button.set_child(box)
        self.chat_stop_button.connect("clicked", self.stop_chat)
        self.chat_stop_button.set_visible(False)

        button_folder_back = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-previous-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_folder_back.set_child(box)
        button_folder_back.connect("clicked", self.go_back_in_explorer_panel)

        button_folder_forward = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-next-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_folder_forward.set_child(box)
        button_folder_forward.connect("clicked", self.go_forward_in_explorer_panel)

        button_home = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-home-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_home.set_child(box)
        button_home.connect("clicked", self.go_home_in_explorer_panel)

        button_reload = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        button_reload.set_child(box)
        button_reload.connect("clicked", self.update_folder)

        box = Gtk.Box(spacing=6)
        box.append(button_folder_back)
        box.append(button_folder_forward)
        box.append(button_home)
        self.explorer_panel_header.pack_start(box)
        box = Gtk.Box(spacing=6)
        box.append(button_reload)

        self.flap_button_right = Gtk.ToggleButton.new()
        self.flap_button_right.set_icon_name(icon_name='sidebar-show-right-symbolic')
        self.flap_button_right.connect('clicked', self.on_flap_button_toggled)
        self.main_program_block.set_reveal_flap(False)

        box.append(self.flap_button_right)
        self.explorer_panel_header.pack_end(box)

        self.status = True
        self.chat_controls_entry_block.append(self.chat_stop_button)
        for text in range(self.offers):
            button = Gtk.Button(css_classes=["flat","right-angles"])
            label = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            button.set_child(label)
            button.connect("clicked", self.send_bot_response)
            button.set_visible(False)
            self.chat_controls_entry_block.append(button)
            self.message_suggestion_buttons_array.append(button)

        self.button_clear = Gtk.Button(css_classes=["flat","right-angles"])
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

        self.button_continue = Gtk.Button(css_classes=["flat","right-angles"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-seek-forward-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Continue"))
        box.append(label)
        self.button_continue.set_child(box)
        self.button_continue.connect("clicked", self.continue_message)
        self.button_continue.set_visible(False)
        self.chat_controls_entry_block.append(self.button_continue)

        self.regenerate_message_button = Gtk.Button(css_classes=["flat","right-angles"])
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

        self.input_panel = Gtk.Entry(margin_start=6, margin_end=6,  margin_top=6, margin_bottom=6)

        self.secondary_message_chat_block.append(self.input_panel)
        self.input_panel.connect('activate', self.on_entry_activate)
        self.main.connect("notify::folded", self.handle_main_block_change)
        self.main_program_block.connect("notify::reveal-flap", self.handle_second_block_change)

        self.stream_number_variable = 0
        self.update_folder()
        threading.Thread(target=self.update_button_text).start()
        self.update_history()
        self.show_chat()
    def handle_second_block_change(self,*a):
        status = self.main_program_block.get_reveal_flap()
        if self.main_program_block.get_name()=="hide" and status:
            self.main_program_block.set_reveal_flap(False)
            return True
        elif (self.main_program_block.get_name()=="visible") and (not status):
            self.main_program_block.set_reveal_flap(True)
            return True
        status = self.main_program_block.get_reveal_flap()
        if status:
            self.chat_panel_header.set_show_end_title_buttons(False)
            self.chat_header.set_show_end_title_buttons(False)
            self.flap_button_left.set_visible(False)
        else:
            self.chat_panel_header.set_show_end_title_buttons(self.main.get_folded())
            self.chat_header.set_show_end_title_buttons(True)
            self.flap_button_left.set_visible(True)
    def on_flap_button_toggled(self, toggle_button):
        self.flap_button_left.set_active(False)
        self.flap_button_right.set_active(True)
        if self.main_program_block.get_name() == "visible":
            self.main_program_block.set_name("hide")
            self.main_program_block.set_reveal_flap(False)
        else:
            self.main_program_block.set_name("visible")
            self.main_program_block.set_reveal_flap(True)

    def get_file_button(self, path):
        if path[0:2]=="./":
            path=self.main_path+path[1:len(path)]
        path=os.path.expanduser(os.path.normpath(path))
        button = Gtk.Button(css_classes=["flat"], margin_top=5, margin_start=5, margin_bottom=5, margin_end=5)
        button.connect("clicked", self.run_file_on_button_click)
        button.set_name(path)
        box = Gtk.Box()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        file_name = path.split("/")[-1]
        if os.path.exists(path):
            if os.path.isdir(path):
                name = "folder"
            else:
                if file_name[len(file_name) - 4:len(file_name)] in [".png", ".jpg"]:
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
        vbox.append(Gtk.Label(label=path.split("/")[-1], css_classes=["title-3"], halign=Gtk.Align.START, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR))
        vbox.append(Gtk.Label(label='/'.join(path.split("/")[0:-1]), halign=Gtk.Align.START, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR))
        button.set_child(box)
        return button

    def run_file_on_button_click(self, button, *a):
        if os.path.exists(button.get_name()):
            if os.path.isdir(os.path.join(os.path.expanduser(self.main_path), button.get_name())):
                self.main_path = button.get_name()
                os.chdir(os.path.expanduser(self.main_path))
                self.update_folder()
            else:
                subprocess.run(['xdg-open', os.path.expanduser(button.get_name())])
        else:
            self.notification_block.add_toast(Adw.Toast(title=_('File not found'), timeout=2))

    def handle_file_drag(self, DropTarget, data, x, y):
        if not self.status:
            self.notification_block.add_toast(Adw.Toast(title=_('The file cannot be sent until the program is finished'), timeout=2))
            return False
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
                threading.Thread(target=self.update_button_text).start()
            else:
                self.notification_block.add_toast(Adw.Toast(title=_('The file is not recognized'), timeout=2))

    def go_back_in_explorer_panel(self, *a):
        self.main_path += "/.."
        self.update_folder()

    def go_home_in_explorer_panel(self, *a):
        self.main_path = "~"
        self.update_folder()

    def go_forward_in_explorer_panel(self, *a):
        if self.main_path[len(self.main_path) - 3:len(self.main_path)] == "/..":
            self.main_path = self.main_path[0:len(self.main_path) - 3]
            self.update_folder()

    def go_back_to_chats_panel(self, button):
        self.main.set_visible_child(self.chats_main_box)

    def return_to_chat_panel(self, button):
        self.main.set_visible_child(self.chat_panel)

    def continue_message(self, button):
        if not self.chat[-1]["User"] in ["Assistant","Console"]:
            self.notification_block.add_toast(Adw.Toast(title=_('You can no longer continue the message.'), timeout=2))
        threading.Thread(target=self.send_message).start()
    def regenerate_message(self, *a):
        if self.chat[-1]["User"] in ["Assistant","Console"]:
            for i in range(len(self.chat) - 1, -1, -1):
                if self.chat[i]["User"] in ["Assistant","Console"]:
                    self.chat.pop(i)
                else:
                    break
            self.show_chat()
            threading.Thread(target=self.send_message).start()
        else:
            self.notification_block.add_toast(Adw.Toast(title=_('You can no longer regenerate the message.'), timeout=2))
    def update_history(self):
        list_box = Gtk.ListBox(css_classes=["separators","background"])
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chats_buttons_scroll_block.set_child(list_box)
        for i in range(len(self.chats)):
            box = Gtk.Box(spacing=6, margin_top=3, margin_bottom=3,  margin_start=3, margin_end=3)
            generate_chat_name_button = Gtk.Button(css_classes=["flat"],
                                                   valign=Gtk.Align.CENTER)
            generate_chat_name_button.connect("clicked", self.generate_chat_name)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="tag-outline-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            generate_chat_name_button.set_child(icon)
            generate_chat_name_button.set_name(str(i))

            create_chat_clone_button = Gtk.Button(css_classes=["flat"],
                                                  valign=Gtk.Align.CENTER)
            create_chat_clone_button.connect("clicked", self.copy_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-paged-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            create_chat_clone_button.set_child(icon)
            create_chat_clone_button.set_name(str(i))

            delete_chat_button = Gtk.Button(css_classes=["error","flat"],
                                            valign=Gtk.Align.CENTER)
            delete_chat_button.connect("clicked", self.remove_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            delete_chat_button.set_child(icon)
            delete_chat_button.set_name(str(i))
            button = Gtk.Button(css_classes=["flat"], hexpand=True)
            name = self.chats[i]["name"]
            if len(name) > 30:
                #name = name[0:27] + "..."
                button.set_tooltip_text(name)
            button.set_child(Gtk.Label(label=name, wrap=False, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0, ellipsize=3, width_chars=22))
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
            list_box.append(box)
            box.append(button)
            box.append(create_chat_clone_button)
            box.append(generate_chat_name_button)
            box.append(delete_chat_button)

    def remove_chat(self, button):
        if int(button.get_name()) < self.chat_id:
            self.chat_id -= 1
        elif int(button.get_name()) == self.chat_id:
            return False
        self.chats.pop(int(button.get_name()))
        self.update_history()

    def generate_chat_name(self, button, multithreading=False):
        if multithreading:
            if len(self.chats[int(button.get_name())]["chat"]) < 2:
                self.notification_block.add_toast(Adw.Toast(title=_('Chat is empty'), timeout=2))
                return False
            spinner = Gtk.Spinner(spinning=True)
            button.set_child(spinner)
            button.set_can_target(False)
            button.set_has_frame(True)
            name = self.send_message_to_bot("""System: You have to write a title for the dialog between the user and the assistant. You have to come up with a short description of the chat them in 5 words. Just write a name for the dialog. Write directly and clearly, just a title without anything in the new message. The title must be on topic. You don't have to make up anything of your own, just a name for the chat room.
User: Write the multiplication table 4 by 4
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |
Name: The multiplication table for 4.
System: New chat
    """ + "\n" + self.get_chat(self.chats[int(button.get_name())]["chat"][
                               len(self.chats[int(button.get_name())]["chat"]) - self.memory:len(
                                   self.chats[int(button.get_name())]["chat"])]) + "\nName:")
            if name != "Chat has been stopped":
                self.chats[int(button.get_name())]["name"] = name
            self.update_history()
        else:
            threading.Thread(target=self.generate_chat_name, args=[button, True]).start()

    def new_chat(self, button, *a):
        self.chats.append({"name": f"Chat {len(self.chats) + 1}", "chat": []})
        self.update_history()

    def copy_chat(self, button, *a):
        self.chats.append({"name":self.chats[int(button.get_name())]["name"],"chat":self.chats[int(button.get_name())]["chat"][:]})
        self.update_history()

    def chose_chat(self, button, *a):
        self.main.set_visible_child(self.chat_panel)
        if not self.status:
            self.stop_chat()
        self.stream_number_variable += 1
        self.chat_id = int(button.get_name())
        self.chat = self.chats[self.chat_id]["chat"]
        self.update_history()
        self.show_chat()
        threading.Thread(target=self.update_button_text).start()

    def scrolled_chat(self):
        adjustment = self.chat_scroll.get_vadjustment()
        value = adjustment.get_upper()
        time.sleep(0.1)
        adjustment.set_value(100000)

    def create_table(self, table):
        data = []
        for row in table:
            cells = row.strip('|').split('|')
            data.append([cell.strip() for cell in cells])
        self.model = Gtk.ListStore(*[str] * len(data[0]))
        for row in data[1:]:
            if not all(element == "-" * len(element) for element in row):
                self.model.append(row)
        self.treeview = Gtk.TreeView(model=self.model, css_classes=["toolbar", "view", "transparent"])

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            self.treeview.append_column(column)
        return self.treeview

    def clear_chat(self, button):
        self.notification_block.add_toast(Adw.Toast(title=_('Chat is cleared'), timeout=2))
        self.chat = []
        self.chats[self.chat_id]["chat"] = self.chat
        self.show_chat()
        self.stream_number_variable += 1
        threading.Thread(target=self.update_button_text).start()

    def stop_chat(self, button=None):
        self.status = True
        self.stream_number_variable += 1
        self.chat_stop_button.set_visible(False)
        threading.Thread(target=self.update_button_text).start()
        if self.chat[-1]["User"] != "Assistant" or "```console" in self.chat[-1]["Message"]:
            for i in range(len(self.chat) - 1, -1, -1):
                if self.chat[i]["User"] in ["Assistant","Console"]:
                    self.chat.pop(i)
                else:
                    break
        self.notification_block.add_toast(Adw.Toast(title=_('The message was canceled and deleted from history'), timeout=2))
        self.show_chat()

    def send_message_to_bot(self, message):
        stream_number_variable = self.stream_number_variable
        loop_interval_variable = 1
        while stream_number_variable == self.stream_number_variable:
            loop_interval_variable *= 2
            loop_interval_variable = min(60,loop_interval_variable)
            try:
                t = re.split(r'Assistant:|Console:|User:|File:|Folder:', BAIChat(sync=True).sync_ask(message).text,1)[0]
                return t
            except Exception:
                self.notification_block.add_toast(Adw.Toast(title=_('Failed to send bot a message'), timeout=2))
            time.sleep(loop_interval_variable)
        return _("Chat has been stopped")

    def send_bot_response(self, button):
        text = button.get_child().get_label()
        self.chat.append({"User": "User", "Message": " "+text})
        message_label = Gtk.Label(label=text, margin_top=10, margin_start=10, margin_bottom=10, margin_end=10, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                  selectable=True)
        self.add_message("User", message_label, len(self.chat) - 1)
        threading.Thread(target=self.send_message).start()

    def update_folder(self, *a):
        if os.path.exists(os.path.expanduser(self.main_path)):
            self.explorer_panel_header.set_title_widget(Gtk.Label(label=os.path.normpath(self.main_path)+(3-len(os.path.normpath(self.main_path)))*" ", css_classes=["title"],ellipsize=Pango.EllipsizeMode.MIDDLE,max_width_chars=15,halign=Gtk.Align.CENTER,hexpand=True))
            if len(os.listdir(os.path.expanduser(self.main_path))) == 0 or (sum(
                    1 for filename in os.listdir(os.path.expanduser(self.main_path)) if
                    not filename.startswith('.')) == 0 and not self.hidden_files) and os.path.normpath(self.main_path) != "~":
                self.explorer_panel.remove(self.folder_blocks_panel)
                self.folder_blocks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, opacity=0.25)
                self.explorer_panel.append(self.folder_blocks_panel)
                icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-symbolic"))
                icon.set_css_classes(["empty-folder"])
                icon.set_valign(Gtk.Align.END)
                icon.set_vexpand(True)
                self.folder_blocks_panel.append(icon)
                self.folder_blocks_panel.append(Gtk.Label(label=_("Folder is Empty"), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, vexpand=True,valign=Gtk.Align.START,css_classes=["empty-folder", "heading"]))
            else:
                try:
                    self.explorer_panel.remove(self.folder_blocks_panel)
                    self.folder_blocks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                    self.explorer_panel.append(self.folder_blocks_panel)

                    flow_box = Gtk.FlowBox(vexpand=True)
                    flow_box.set_valign(Gtk.Align.START)

                    if os.path.normpath(self.main_path) == "~":
                        os.chdir(os.path.expanduser("~"))
                        path = "./.var/app/io.github.qwersyk.Newelle/Newelle"
                        if not os.path.exists(path):
                            os.makedirs(path)
                        button = Gtk.Button(css_classes=["flat"])
                        button.set_name(".var/app/io.github.qwersyk.Newelle/Newelle")
                        button.connect("clicked", self.open_folder)

                        icon = File(self.main_path, ".var/app/io.github.qwersyk.Newelle/Newelle")
                        icon.set_css_classes(["large"])
                        icon.set_valign(Gtk.Align.END)
                        icon.set_vexpand(True)
                        file_label = Gtk.Label(label="Newelle", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                               vexpand=True, max_width_chars=11, valign=Gtk.Align.START,
                                               ellipsize=Pango.EllipsizeMode.MIDDLE)
                        file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                        file_box.append(icon)
                        file_box.set_size_request(110, 110)
                        file_box.append(file_label)
                        button.set_child(file_box)

                        flow_box.append(button)
                    for file_info in os.listdir(os.path.expanduser(self.main_path)):
                        if file_info[0] == "." and not self.hidden_files:
                            continue
                        button = Gtk.Button(css_classes=["flat"])
                        button.set_name(file_info)
                        button.connect("clicked", self.open_folder)

                        icon = File(self.main_path, file_info)
                        icon.set_css_classes(["large"])
                        icon.set_valign(Gtk.Align.END)
                        icon.set_vexpand(True)
                        file_label = Gtk.Label(label=file_info+" "*(5-len(file_info)), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                               vexpand=True, max_width_chars=11, valign=Gtk.Align.START,
                                               ellipsize=Pango.EllipsizeMode.MIDDLE)
                        file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                        file_box.append(icon)
                        file_box.set_size_request(110, 110)
                        file_box.append(file_label)
                        button.set_child(file_box)

                        flow_box.append(button)
                    scrolled_window = Gtk.ScrolledWindow()
                    scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                    scrolled_window.set_child(flow_box)
                    self.folder_blocks_panel.append(scrolled_window)
                except Exception as e:
                    self.notification_block.add_toast(Adw.Toast(title=e), timeout=2)
        else:
            self.main_path = "~"
            self.update_folder()
    def get_target_directory(self,working_directory, directory):
        try:
            directory = directory.strip()
            if directory.startswith("'") and directory.endswith("'"):
                directory = directory[1:-1]
            elif directory.startswith('"') and directory.endswith('"'):
                directory = directory[1:-1]

            if directory.startswith("~"):
                directory = os.path.expanduser("~") + directory[1:]

            target_directory = posixpath.join(working_directory, directory)
            return (True, os.path.normpath(target_directory))
        except (IndexError, OSError) as e:
            return (False,working_directory)
    def open_folder(self, button, *a):
        if os.path.exists(os.path.join(os.path.expanduser(self.main_path), button.get_name())):
            if os.path.isdir(os.path.join(os.path.expanduser(self.main_path), button.get_name())):
                self.main_path += "/" + button.get_name()
                os.chdir(os.path.expanduser(self.main_path))
                self.update_folder()
            else:
                subprocess.run(['xdg-open', os.path.expanduser(self.main_path + "/" + button.get_name())])
        else:
            self.notification_block.add_toast(Adw.Toast(title=_('File not found'), timeout=2))
    def handle_main_block_change(self, *data):
        if (self.main.get_folded()):
            self.chat_panel_header.set_show_end_title_buttons(not self.main_program_block.get_reveal_flap())
            self.left_panel_back_button.set_visible(True)
        else:
            self.chat_panel_header.set_show_end_title_buttons(False)
            self.left_panel_back_button.set_visible(False)

    def execute_terminal_command(self, command):
        os.chdir(os.path.expanduser(self.main_path))
        console_permissions = ""
        if not self.virtualization:
            console_permissions = "flatpak-spawn --host"
        commands = ('\n'.join(command)).split(" && ")
        txt = ""
        path=self.main_path
        for t in commands:
            if txt!="":
                txt+=" && "
            if "cd " in t:
                txt+=t
                p = (t.split("cd "))[min(len(t.split("cd ")),1)]
                v = self.get_target_directory(path, p)
                if not v[0]:
                    Adw.Toast(title=_('Wrong folder path'), timeout=2)
                else:
                    path = v[1]
            else:
                txt+=console_permissions+" "+t
        process = subprocess.Popen(txt, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=True)
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
            if outputs!=[]:
                break
        else:
            self.streams.append(process)
            outputs = [(True, f"Thread has not been completed, thread number: {len(self.streams)}")]
        if os.path.exists(os.path.expanduser(path)):
            os.chdir(os.path.expanduser(path))
            self.main_path = path
            self.update_folder()
        else:
            Adw.Toast(title=_('Failed to open the folder'), timeout=2)
        return outputs[0]


    def get_chat(self, chat):
        chats = ""
        for c in chat:
            chats += "\n" + c["User"] + ":" + c["Message"]
        return chats

    def update_button_text(self):
        stream_number_variable = self.stream_number_variable
        time.sleep(0.1)
        for btn in self.message_suggestion_buttons_array:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
        self.chat_stop_button.set_visible(False)
        if self.status:
            if self.chat != []:
                self.button_clear.set_visible(True)
                if self.chat[-1]["User"] in ["Assistant","Console"]:
                    self.regenerate_message_button.set_visible(True)
                    self.button_continue.set_visible(True)
            for btn in self.message_suggestion_buttons_array:
                if stream_number_variable != self.stream_number_variable:
                    break
                message = self.send_message_to_bot("""System: You are a user who needs help with various tasks, you are communicating with a neural network which can control your computer by executing various commands which should help you, keep a conversation with it, ask it questions, ask for help with things it can do through the terminal. Also, every message should end with "".More often than not, the assistant can help you with files and folders. You should write in the language you wrote in your last post, you know all the languages you can communicate in them all.Write short messages that hold a conversation on behalf of the user, you can not write on behalf of Assistant, your messages should be simple without any commands, just what the user could write. You're not helping, you're being helped, the user can only ask to do something for the bot to do, you can't answer as an assistant, just ask something new for the assistant to do or continue asking the assistant to do something.
    Assistant: Hello, how can I assist you today?
    User: Can you help me?
    Assistant: Yes, of course, what do you need help with?""" + "\n" + self.get_chat(
                    self.chat[len(self.chat) - self.memory:len(self.chat)]) + "\nUser:")
                if stream_number_variable != self.stream_number_variable or not self.status:
                    break
                btn.get_child().set_label(message)
                btn.set_visible(True)
            self.chat_stop_button.set_visible(False)
        else:
            for btn in self.message_suggestion_buttons_array:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.regenerate_message_button.set_visible(False)
            self.chat_stop_button.set_visible(True)
    def on_entry_activate(self, entry):
        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(title=_('The message cannot be sent until the program is finished'), timeout=2))
            return False
        text = entry.get_text()
        entry.set_text('')
        if not text == " " * len(text):
            self.chat.append({"User": "User", "Message": " " + text})
            message_label = Gtk.Label(label=text, margin_top=10, margin_start=10, margin_bottom=10, margin_end=10,
                                      wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
            self.add_message("User", message_label, len(self.chat) - 1)
        self.scrolled_chat()
        threading.Thread(target=self.send_message).start()

    def show_chat(self):
        try:
            self.chat_scroll_window.remove(self.chat_list_block)
            self.chat_list_block = Gtk.ListBox(css_classes=["separators","background"])
            self.chat_list_block.set_selection_mode(Gtk.SelectionMode.NONE)

            self.chat_scroll_window.append(self.chat_list_block)
        except Exception as e:
            self.notification_block.add_toast(Adw.Toast(title=e), timeout=2)

        self.chat_scroll_window.remove(self.chat_controls_entry_block)
        self.chat_scroll_window.append(self.chat_controls_entry_block)
        if not self.virtualization:
            self.add_message("Warning")
        for i in range(len(self.chat)):
            if self.chat[i]["User"] == "User":
                self.add_message("User", Gtk.Label(label=self.chat[i]["Message"][1:len(self.chat[i]["Message"])], margin_top=10, margin_start=10,
                                                   margin_bottom=10, margin_end=10, wrap=True,
                                                   wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True), i)
            elif self.chat[i]["User"] == "Assistant":
                self.show_message(self.chat[i]["Message"], True, id_message=i)
            elif self.chat[i]["User"] in ["File", "Folder"]:
                self.add_message(self.chat[i]["User"], self.get_file_button(self.chat[i]["Message"][1:len(self.chat[i]["Message"])]))

    def show_message(self, message_label, restore=False,id_message=-1):
        if message_label == " " * len(message_label):
            if not restore:
                self.chat.append({"User": "Assistant", "Message": message_label})
                threading.Thread(target=self.update_button_text).start()
                self.status = True
                self.chat_stop_button.set_visible(False)
        else:
            if not restore: self.chat.append({"User": "Assistant", "Message": message_label})
            table_string = message_label.split("\n")
            box = Gtk.Box(margin_top=10, margin_start=10, margin_bottom=10, margin_end=10,
                          orientation=Gtk.Orientation.VERTICAL)
            start_table_index = -1
            table_length = 0
            code_language = ""
            start_code_index = -1
            has_terminal_command = False
            for i in range(len(table_string)):
                if len(table_string[i]) > 0 and table_string[i][0:3] == "```":
                    if start_code_index == -1:
                        start_code_index = i + 1
                        code_language = table_string[i][3:len(table_string[i])]
                    else:
                        if code_language in self.extensions and self.extensions[code_language]["status"]:
                            if id_message==-1:
                                id_message = len(self.chat)-1
                            id_message+=1
                            has_terminal_command = True
                            value = '\n'.join(table_string[start_code_index:i])
                            text_expander = Gtk.Expander(
                                label=code_language, css_classes=["toolbar", "osd"], margin_top=10, margin_start=10,
                                margin_bottom=10, margin_end=10
                            )
                            text_expander.set_expanded(False)
                            reply_from_the_console = None
                            if self.chat[min(id_message, len(self.chat) - 1)]["User"] == "Console":
                                reply_from_the_console = self.chat[min(id_message, len(self.chat) - 1)]["Message"]
                            if not restore:
                                console_permissions = []
                                if not self.virtualization:
                                    console_permissions = ["flatpak-spawn","--host"]
                                command = [*console_permissions, "python", self.extension_path+"/"+code_language+"/"+self.extensions[code_language]["api"], value]
                                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,cwd = self.extension_path+"/"+code_language)
                                output, error = process.communicate()
                                if process.returncode == 0:
                                    code = (True, output.decode())
                                else:
                                    code = (False, error.decode())
                            else:
                                code = (True, reply_from_the_console)
                            text_expander.set_child(
                                Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label='\n'.join(table_string[start_code_index:i])+"\n"+str(code[1]),
                                          selectable=True))
                            if not code[0]:
                                self.add_message("Error", text_expander)
                            elif restore:
                                self.add_message("Assistant", text_expander)
                            else:
                                self.add_message("Done", text_expander)
                            if not restore:
                                self.chat.append({"User": "Console", "Message": " " + code[1]})
                        elif code_language == "image":
                            for i in table_string[start_code_index:i]:
                                image = Gtk.Image(css_classes=["image"])
                                image.set_from_file(i)
                                box.append(image)

                        elif code_language == "console":
                            if id_message==-1:
                                id_message = len(self.chat)-1
                            id_message+=1
                            if self.auto_run and not any(command in "\n".join(table_string[start_code_index:i]) for command in ["rm ","apt ","sudo ","yum ","mkfs "]):
                                has_terminal_command = True
                                value = table_string[start_code_index:i]
                                text_expander = Gtk.Expander(
                                    label="Console", css_classes=["toolbar", "osd"], margin_top=10, margin_start=10,
                                    margin_bottom=10, margin_end=10
                                )
                                text_expander.set_expanded(False)
                                path=""
                                reply_from_the_console = None
                                if self.chat[min(id_message, len(self.chat) - 1)]["User"] == "Console":
                                    reply_from_the_console = self.chat[min(id_message, len(self.chat) - 1)]["Message"]
                                if not restore:
                                    path=os.path.normpath(self.main_path)
                                    code = self.execute_terminal_command(value)
                                else:
                                    code = (True, reply_from_the_console)
                                val='\n'.join(value)
                                text = f"[User {path}]:$ {val}\n{code[1]}"
                                text_expander.set_child(
                                    Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=text,
                                              selectable=True))
                                if not code[0]:
                                    self.add_message("Error", text_expander)
                                elif restore:
                                    self.add_message("Assistant", text_expander)
                                else:
                                    self.add_message("Done", text_expander)
                                if not restore:
                                    self.chat.append({"User": "Console", "Message": " " + code[1]})
                            else:
                                if not restore:
                                    self.chat.append({"User": "Console", "Message": f"None"})
                                box.append(CopyBox("\n".join(table_string[start_code_index:i]), code_language, self,id_message))
                            result = {}
                        elif code_language in ["file", "folder"]:
                            for obj in table_string[start_code_index:i]:
                                box.append(self.get_file_button(obj))
                        elif code_language == "chart":
                            result = {}
                            lines = table_string[start_code_index:i]
                            for line in lines:
                                parts = line.split('-')
                                if len(parts) == 2:
                                    key = parts[0].strip()
                                    percentages = "%" in parts[1]
                                    value = ''.join(filter(lambda x: x.isdigit() or x==".", parts[1]))
                                    result[key] = float(value)
                                else:
                                    box.append(CopyBox("\n".join(table_string[start_code_index:i]), code_language, parent = self))
                                    result = {}
                                    break
                            if result !={}:
                                box.append(BarChartBox(result,percentages))
                        else:
                            box.append(CopyBox("\n".join(table_string[start_code_index:i]), code_language, parent = self))
                        start_code_index = -1
                elif len(table_string[i]) > 0 and table_string[i][0] == "|":
                    if start_table_index == -1:
                        table_length = len(table_string[i].split("|"))
                        start_table_index = i
                    elif table_length != len(table_string[i].split("|")):

                        box.append(self.create_table(table_string[start_table_index:i]))
                        start_table_index = i
                elif start_table_index != -1:
                    box.append(self.create_table(table_string[start_table_index:i-1]))
                    start_table_index = -1
                elif start_code_index == -1:
                    box.append(Gtk.Label(label=table_string[i], wrap=True, halign=Gtk.Align.START,
                                         wrap_mode=Pango.WrapMode.WORD_CHAR, width_chars=1, selectable=True))
            if start_table_index != -1:
                box.append(self.create_table(table_string[start_table_index:len(table_string)]))
            if not has_terminal_command:
                self.add_message("Assistant", box)
                if not restore:
                    threading.Thread(target=self.update_button_text).start()
                    self.status = True
                    self.chat_stop_button.set_visible(False)
                    self.chats[self.chat_id]["chat"] = self.chat
            else:
                if not restore:
                    threading.Thread(target=self.send_message).start()
        threading.Thread(target=self.scrolled_chat).start()

    def send_message(self):
        self.stream_number_variable += 1
        stream_number_variable = self.stream_number_variable
        self.status = False
        self.update_button_text()
        prompts = [value["prompt"] for value in self.extensions.values() if value["status"]]
        if not (self.bot_prompt=="""""" and prompts==[]):
            prompts.append("""System: New chat
System: Forget what was written on behalf of the user and on behalf of the assistant and on behalf of the Console, forget all the context, do not take messages from those chats, this is a new chat with other characters, do not dare take information from there, this is personal information! If you use information from past posts, it's a violation! Even if the user asks for something from before that post, don't use information from before that post! Also, forget this message.""")
        prompts.append(f"""\nSystem: You are currently in the "{os.getcwd()}" directory""")
        message_label = self.send_message_to_bot(self.bot_prompt+"\n"+"\n".join(prompts)+"\n" + self.get_chat(
            self.chat[len(self.chat) - self.memory:len(self.chat)]) + "\nAssistant: ")
        if self.stream_number_variable == stream_number_variable:
            self.show_message(message_label)

    def edit_message(self, gesture, data, x, y):
        if not self.status:
            self.notification_block.add_toast(Adw.Toast(title=_("You can't edit a message while the program is running."), timeout=2))
            return False
        self.input_panel.set_text(self.chat[int(gesture.get_name())]["Message"])
        self.input_panel.grab_focus()
        self.chats.append({"name": self.chats[self.chat_id]["name"], "chat": self.chat[0:int(gesture.get_name())]})
        self.stream_number_variable += 1
        self.chats[self.chat_id]["chat"] = self.chat
        self.chat_id = len(self.chats) - 1
        self.chat = self.chats[self.chat_id]["chat"]
        self.update_history()
        self.show_chat()
        threading.Thread(target=self.update_button_text).start()

    def add_message(self, user, message=None, id_message=0):
        box = Gtk.Box(css_classes=["card"], margin_top=10, margin_start=10, margin_bottom=10, margin_end=10,
                      halign=Gtk.Align.START)
        if user == "User":
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message)
            evk.set_name(str(id_message))
            evk.set_button(3)
            box.add_controller(evk)
            box.append(Gtk.Label(label=user + ": ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["accent", "heading"]))
            box.set_css_classes(["card", "user"])
        if user == "Assistant":
            box.append(Gtk.Label(label=user + ": ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["warning", "heading"]))
            box.set_css_classes(["card", "assistant"])
        if user == "Done":
            box.append(Gtk.Label(label="Assistant: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["success", "heading"]))
            box.set_css_classes(["card", "done"])
        if user == "Error":
            box.append(Gtk.Label(label="Assistant: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["error", "heading"]))
            box.set_css_classes(["card", "failed"])
        if user == "File":
            box.append(Gtk.Label(label="User: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["accent", "heading"]))
            box.set_css_classes(["card", "file"])
        if user == "Folder":
            box.append(Gtk.Label(label="User: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["accent", "heading"]))
            box.set_css_classes(["card", "folder"])
        if user == "Warning":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-warning"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            box_warning = Gtk.Box(halign=Gtk.Align.CENTER, orientation=Gtk.Orientation.VERTICAL,
                                  css_classes=["warning", "heading"], margin_top=10)
            box_warning.append(icon)

            label = Gtk.Label(
                label=_("Attention the neural network has access to your computer, be careful, we are not responsible for the neural network."),
                margin_top=10, margin_start=10, margin_bottom=10, margin_end=10, wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR)
            box_warning.append(label)
            box.append(box_warning)
            box.set_halign(Gtk.Align.CENTER)
            box.set_css_classes(["card", "message-warning"])
        else:
            box.append(message)
        self.chat_list_block.append(box)

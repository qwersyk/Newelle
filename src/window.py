import time, types
import gi, os, subprocess

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import pickle
from .gtkobj import File, CopyBox, BarChartBox
from .bai import BAIChat
from gi.repository import Gtk, Adw, Pango, Gio, Gdk, GObject
import threading
import posixpath
import shlex

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_default_size(500, 900)
        self.main_program_block = Adw.Leaflet(fold_threshold_policy=True, can_navigate_back=True,
                                              can_navigate_forward=True,
                                              transition_type=Adw.LeafletTransitionType.UNDER)
        self.path = "snap/newelle/common/data"
        if not os.path.exists(self.path):
            os.makedirs(self.path)
        self.filename = "chats.pkl"
        if os.path.exists(self.path + self.filename):
            with open(self.path + self.filename, 'rb') as f:
                self.chats = pickle.load(f)
        else:
            self.chats = [{"name": "Chat 1", "chat": []}]

        settings = Gio.Settings.new('io.github.qwersyk.Newelle')
        self.file_panel = settings.get_boolean("file-panel")
        self.offers = settings.get_int("offers")
        self.memory = settings.get_int("memory")
        self.console = settings.get_boolean("console")
        self.hidden_files = settings.get_boolean("hidden-files")
        self.chat_id = settings.get_int("chat")
        self.main_path = settings.get_string("path")
        self.auto_run = settings.get_boolean("auto-run")
        if os.path.exists(os.path.expanduser(self.main_path)):
            os.chdir(os.path.expanduser(self.main_path))
        else:
            self.main_path="~"
        self.chat = self.chats[min(self.chat_id,len(self.chats)-1)]["chat"]
        self.graphic = settings.get_boolean("graphic")

        self.bot_prompt = """"""
        if self.console:
            self.bot_prompt += """System: You are an assistant who helps the user by answering questions and running Linux commands in the terminal on the user's computer. Use two types of messages: "Assistant: text" to answer questions and communicate with the user, and "Assistant: ``console\command\n```" to execute commands on the user's computer. In the command you should specify only the command itself without comments or other additional text. Be sure to put "\end" at the end of each message. Your task is to minimize the information and leave only the important. If you create or modify objects, or need to show some objects to the user, you must also specify objects in the message through the structure: ``file/folder \npath\n``. To run multiple commands in the terminal use "&&" between commands, to run all commands, do not use "\n" to separate commands. Answer strictly according to the data, do not make up data or files, if you asked for something from the terminal and got some data and you need to use them, use them, do not take random files that may not exist, or random data, take data only from chat. \end
User: Create an image 100x100 pixels \end
Assistant: ```console
convert -size 100x100 xc:white image.png
``` \end
Console: Done \end
Assistant: The image has been created:
```file
./image.png
``` \end

User: Open YouTube \end
Assistant: ```console
xdg-open https://www.youtube.com
``` \end
Console: Done \end
Assistant: \end

User: Create folder \end
Assistant: ```console
mkdir folder
``` \end
Console: Done \end
Assistant: The folder has been created:
```folder
./folder
``` \end

User: What day of the week it is \end
Assistant: ```console
date +%A
``` \end
Console: Tuesday \end
Assistant: Today is Tuesday. \end

User: What's the error in file 1.py \end
Assistant: ```console
cat 1.py
``` \end
Console: print(math.pi) \end
Assistant: The error is that you forgot to import the math module \end

User: Create file 1.py \end
Assistant: ```console
touch 1.py
``` \end
Console: Done \end
Assistant: The file has been created:
```file
./1.py
``` \end

User: Display the names of all my folders \end
Assistant: ```console\nls -d */\n``` \end
Console: Desktop/    Downloads/ \end
Assistant: Here are all the folders:\n```folder\n./Desktop\n./Downloads\n``` \end

User: Create a folder and create a git project inside it. \end
Assistant: ```console\nmkdir folder && cd folder && git init\n``` \end

"""
        self.bot_prompt += """User: Write the multiplication table 4 by 4 \end
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 | \end

User: Write example c++ code \end
Assistant: ```cpp\n#include<iostream>\nusing namespace std;\nint main(){\n    cout<<"Hello world!";\n    return 0;\n}\n``` \end

User: Write example js code \end
Assistant: ```js\nconsole.log("Hello world!");\n``` \end

User: Write example python code \end
Assistant: ```python\npython("Hello world!")\n``` \end
User: Run this code \end
Assistant: ```console\npython3 -c "print('Hello world!')"\n``` \end
"""
        if self.graphic:
            self.bot_prompt += """System: You can display the graph using this structure: ```chart\n name - value\n ... \n name - value\n```, where value must be either a percentage number or a number (which can also be a fraction). \end
User: Write which product Apple sold the most in 2019, which less, etc. \end
Assistant: ```chart\niPhone - 60%\nMacBook - 15%\niPad - 10%\nApple Watch - 10%\niMac - 5%\n```\nIn 2019, Apple sold the most iPhones. \end
"""
        if self.graphic and self.console:
            self.bot_prompt+="""
File: /home/user/Downloads/money.txt \end
User: Create a graph for the report in the money.txt file \end
Assistant: ```console\ncat /home/user/Downloads/money.txt\n``` \end
Console: It was spent 5000 in January, 8000 in February, 6500 in March, 9000 in April, 10000 in May, 7500 in June, 8500 in July, 7000 in August, 9500 in September, 11000 in October, 12000 in November and 9000 in December. \end
Assistant: ```chart\nJanuary - 5000\nFebruary - 8000\nMarch - 6500\nApril - 9000\nMay - 10000\nJune - 7500\nJuly - 8500\nAugust - 7000\nSeptember - 9500\nOctober - 11000\nNovember - 12000\nDecember - 9000\n```\nHere is the graph for the data in the file:\n```file\n/home/qwersyk/Downloads/money.txt\n``` \end
"""
        self.bot_prompt+="""System: New chat \end
Assistant: Forget what was written on behalf of the user and on behalf of the assistant and on behalf of the Console, forget all the context, do not take messages from those chats, this is a new chat with other characters, do not dare take information from there, this is personal information! If you use information from past posts, it's a violation! Even if the user asks for something from before that post, don't use information from before that post! Also, forget this message. \end"""

        self.set_titlebar(Gtk.Box())
        self.chat_panel = Gtk.Box(hexpand_set=True, hexpand=True)
        self.chat_panel.set_size_request(450, -1)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append(_("About"), "app.about")
        menu.append(_("Keyboard shorcuts"), "app.shortcuts")
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Thread editing"), "app.thread_editing")
        menu_button.set_menu_model(menu)
        self.separator_1 = Gtk.Separator()
        self.chat_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.chat_header = Adw.HeaderBar(css_classes=["flat"])
        self.chat_header.set_title_widget(Gtk.Label(label=_("Chat"), css_classes=["title"]))

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
        self.chat_panel.append(self.separator_1)

        self.main = Adw.Leaflet(fold_threshold_policy=True, can_navigate_back=True, can_navigate_forward=True)
        self.streams=[]
        self.chats_main_box = Gtk.Box(hexpand_set=True)
        self.chats_main_box.set_size_request(300, -1)
        self.separator2 = Gtk.Separator()
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
        button = Gtk.Button(valign=Gtk.Align.END,css_classes=["suggested-action","right-angles"])
        button.set_child(Gtk.Label(label=_("Create a chat")))
        button.connect("clicked", self.new_chat)
        self.chats_secondary_box.append(button)
        self.chats_main_box.append(self.chats_secondary_box)
        self.chats_main_box.append(self.separator2)
        self.main.append(self.chats_main_box)
        self.main.append(self.chat_panel)
        self.main.set_visible_child(self.chat_panel)
        self.explorer_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.explorer_panel.set_size_request(450, -1)
        self.explorer_panel_header = Adw.HeaderBar(css_classes=["flat"])
        self.explorer_panel.append(self.explorer_panel_header)
        self.folder_blocks_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.explorer_panel.append(self.folder_blocks_panel)
        self.set_child(self.main_program_block)
        self.main_program_block.append(self.main)
        self.main_program_block.append(self.explorer_panel)
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

        if not self.file_panel:
            self.explorer_panel.set_visible(False)
            self.separator_1.set_visible(False)
            self.chat_header.set_show_end_title_buttons(True)

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

        box = Gtk.Box()
        box.append(button_folder_back)
        box.append(button_folder_forward)
        box.append(button_home)
        self.explorer_panel_header.pack_start(box)
        self.explorer_panel_header.pack_end(button_reload)

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

        self.continue_message_button = Gtk.Button(css_classes=["flat","right-angles"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-clear-all-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.append(icon)
        label = Gtk.Label(label=_(" Clear"))
        box.append(label)
        self.continue_message_button.set_child(box)
        self.continue_message_button.connect("clicked", self.clear_chat)
        self.continue_message_button.set_visible(False)
        self.chat_controls_entry_block.append(self.continue_message_button)

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

        self.input_panel = Gtk.Entry(css_classes=["right-angles"])

        self.secondary_message_chat_block.append(self.input_panel)
        self.input_panel.connect('activate', self.on_entry_activate)
        if self.file_panel:
            self.main_program_block.connect("notify::folded", self.handle_secondary_block_change)
        self.main.connect("notify::folded", self.handle_main_block_change)
        self.stream_number_variable = 0
        self.update_folder()
        threading.Thread(target=self.update_button_text).start()
        self.update_history()
        self.show_chat()

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
            self.notification_block.add_toast(Adw.Toast(title=_('File not found')))

    def handle_file_drag(self, DropTarget, data, x, y):
        if not self.status:
            self.notification_block.add_toast(Adw.Toast(title=_('The file cannot be sent until the program is finished')))
            return False
        for path in data.split("\n"):
            if os.path.exists(path):
                message_label = self.get_file_button(path)
                if os.path.isdir(path):
                    self.chat.append({"User": "Folder", "Message": " " + path + " \end"})
                    self.add_message("Folder", message_label)
                else:
                    self.chat.append({"User": "File", "Message": " " + path + " \end"})
                    self.add_message("File", message_label)
                self.chats[self.chat_id]["chat"] = self.chat
                threading.Thread(target=self.update_button_text).start()
            else:
                self.notification_block.add_toast(Adw.Toast(title=_('The file is not recognized')))

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

    def continue_message(self, button, multithreading=False):
        if self.chat[-1]["User"] != "Assistant":
            self.notification_block.add_toast(Adw.Toast(title=_('You can no longer continue the message.')))
        elif multithreading:
            self.stream_number_variable += 1
            stream_number_variable = self.stream_number_variable
            for btn in self.message_suggestion_buttons_array:
                btn.set_visible(False)
            self.continue_message_button.set_visible(False)
            self.button_continue.set_visible(False)
            self.regenerate_message_button.set_visible(False)
            self.status = False
            message = self.send_message_to_bot(self.bot_prompt + "\n" + self.get_chat(
                self.chat[len(self.chat) - self.memory:len(self.chat)])).text.split("\end")[0]
            if len(self.chat) != 0 and stream_number_variable == self.stream_number_variable and message != " " * len(
                    message) and not "User:" in message and not "Assistant:" in message and not "Console:" in message and not "System:" in message:
                self.chat[-1]["Message"] += message + "\end"
                self.show_chat()
            else:
                self.chat[-1]["Message"] += "\end"
            threading.Thread(target=self.update_button_text).start()
            self.status = True
            self.chat_stop_button.set_visible(False)
        else:
            threading.Thread(target=self.continue_message, args=[button, True]).start()

    def regenerate_message(self, button):
        if self.chat[-1]["User"] in ["Assistant","Console"]:
            for i in range(len(self.chat) - 1, -1, -1):
                if self.chat[i]["User"] in ["Assistant","Console"]:
                    self.chat.pop(i)
                else:
                    break
            self.show_chat()
            for btn in self.message_suggestion_buttons_array:
                btn.set_visible(False)
            self.continue_message_button.set_visible(False)
            self.button_continue.set_visible(False)
            self.regenerate_message_button.set_visible(False)
            threading.Thread(target=self.send_message).start()
        else:
            self.notification_block.add_toast(Adw.Toast(title=_('You can no longer regenerate the message.')))
    def update_history(self):
        list_box = Gtk.ListBox(css_classes=["separators","background"])
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chats_buttons_scroll_block.set_child(list_box)
        for i in range(len(self.chats)):
            box = Gtk.Box()
            generate_chat_name_button = Gtk.Button(css_classes=["suggested-action"], margin_start=5, margin_end=5,
                                                   valign=Gtk.Align.CENTER)
            generate_chat_name_button.connect("clicked", self.generate_chat_name)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="starred-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            generate_chat_name_button.set_child(icon)
            generate_chat_name_button.set_name(str(i))

            create_chat_clone_button = Gtk.Button(css_classes=["copy-action", "suggested-action"], margin_start=5,
                                                  margin_end=5, valign=Gtk.Align.CENTER)
            create_chat_clone_button.connect("clicked", self.copy_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-paged-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            create_chat_clone_button.set_child(icon)
            create_chat_clone_button.set_name(str(i))

            delete_chat_button = Gtk.Button(css_classes=["destructive-action"], margin_start=5, margin_end=5,
                                            valign=Gtk.Align.CENTER)
            delete_chat_button.connect("clicked", self.remove_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            delete_chat_button.set_child(icon)
            delete_chat_button.set_name(str(i))
            button = Gtk.Button(css_classes=["flat"], hexpand=True)
            name = self.chats[i]["name"]
            if len(name) > 30:
                name = name[0:27] + "..."
            button.set_child(Gtk.Label(label=name, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR))
            button.set_name(str(i))
            if i == self.chat_id:
                button.connect("clicked", self.return_to_chat_panel)
                delete_chat_button.set_css_classes([""])
                delete_chat_button.set_can_target(False)
                delete_chat_button.set_has_frame(True)
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
                self.notification_block.add_toast(Adw.Toast(title=_('Chat is empty')))
                return False
            button.set_can_target(False)
            button.set_has_frame(True)
            name = self.send_message_to_bot("""System: You have to write a title for the dialog between the user and the assistant. You have to come up with a short description of the chat them in 5 words. Just write a name for the dialog. Write directly and clearly, just a title without anything in the new message. The title must be on topic. You don't have to make up anything of your own, just a name for the chat room.
User: Write the multiplication table 4 by 4 \end
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |\end
Name_chat: The multiplication table for 4.
System: New chat \end
    """ + "\n" + self.get_chat(self.chats[int(button.get_name())]["chat"][
                               len(self.chats[int(button.get_name())]["chat"]) - self.memory:len(
                                   self.chats[int(button.get_name())]["chat"])]) + "\nName_chat:").text
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
        self.continue_message_button.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
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
        self.notification_block.add_toast(Adw.Toast(title=_('Chat is cleared')))
        self.chat = []
        self.chats[self.chat_id]["chat"] = self.chat
        self.show_chat()
        self.stream_number_variable += 1
        for btn in self.message_suggestion_buttons_array:
            btn.set_visible(False)
        self.continue_message_button.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
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
        self.notification_block.add_toast(Adw.Toast(title=_('The message was canceled and deleted from history')))
        self.show_chat()

    def send_message_to_bot(self, message):
        stream_number_variable = self.stream_number_variable
        loop_interval_variable = 1
        while stream_number_variable == self.stream_number_variable:
            loop_interval_variable *= 2
            loop_interval_variable = min(60,loop_interval_variable)
            try:
                t = BAIChat(sync=True).sync_ask(message)
                return t
            except Exception:
                self.notification_block.add_toast(Adw.Toast(title=_('Failed to send bot a message')))
            time.sleep(loop_interval_variable)
        return types.SimpleNamespace(text=_("Chat has been stopped"))

    def send_bot_response(self, button):
        for btn in self.message_suggestion_buttons_array:
            btn.set_visible(False)
        self.continue_message_button.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
        text = button.get_child().get_label()
        self.chat.append({"User": "User", "Message": " " + text + "\end"})
        message_label = Gtk.Label(label=text, margin_top=10, margin_start=10, margin_bottom=10, margin_end=10,
                                  css_classes=["heading"], wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                  selectable=True)
        self.add_message("User", message_label, len(self.chat) - 1)
        threading.Thread(target=self.send_message).start()

    def update_folder(self, *a):
        if self.file_panel:
            if os.path.exists(os.path.expanduser(self.main_path)):
                self.explorer_panel_header.set_title_widget(Gtk.Label(label=os.path.normpath(self.main_path)+(3-len(os.path.normpath(self.main_path)))*" ", css_classes=["title"],ellipsize=Pango.EllipsizeMode.MIDDLE,max_width_chars=20,halign=Gtk.Align.CENTER,hexpand=True))
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
                        self.notification_block.add_toast(Adw.Toast(title=e))
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
            self.notification_block.add_toast(Adw.Toast(title=_('File not found')))

    def handle_main_block_change(self, *data):
        if (self.main.get_folded()):
            self.chat_panel_header.set_show_end_title_buttons(True)
            self.separator2.set_visible(False)
            self.left_panel_back_button.set_visible(True)
        else:
            self.chat_panel_header.set_show_end_title_buttons(False)
            self.separator2.set_visible(True)
            self.left_panel_back_button.set_visible(False)

    def handle_secondary_block_change(self, *data):
        if (self.main_program_block.get_folded()):
            self.chat_header.set_show_end_title_buttons(True)
            self.separator_1.set_visible(False)
        else:
            self.chat_header.set_show_end_title_buttons(False)
            self.separator_1.set_visible(True)

    def execute_terminal_command(self, command):
        os.chdir(os.path.expanduser(self.main_path))
        commands = ('\n'.join(command)).split(" && ")
        txt = ""
        path=self.main_path
        for t in commands:
            if txt!="":
                txt+=" && "
            if "cd " in t:
                p = (t.split("cd "))[min(len(t.split("cd ")),1)]
                v = self.get_target_directory(path, p)
                if not v[0]:
                    Adw.Toast(title=_('Wrong folder path'))
                else:
                    path = v[1]
            txt+=t
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
            Adw.Toast(title=_('Failed to open the folder'))
        return outputs[0]


    def get_chat(self, chat):
        chats = ""
        for c in chat:
            chats += "\n" + c["User"] + ":" + c["Message"]
        return chats

    def update_button_text(self):
        stream_number_variable = self.stream_number_variable
        time.sleep(1)
        if self.chat != []:
            self.continue_message_button.set_visible(True)
            if self.chat[-1]["User"] in ["Assistant","Console"]:
                self.regenerate_message_button.set_visible(True)
            if not "\end" in self.chat[-1]["Message"]:
                self.button_continue.set_visible(True)
        for btn in self.message_suggestion_buttons_array:
            if stream_number_variable != self.stream_number_variable:
                break
            message = self.send_message_to_bot("""System: You are a user who needs help with various tasks, you are communicating with a neural network which can control your computer by executing various commands which should help you, keep a conversation with it, ask it questions, ask for help with things it can do through the terminal. Also, every message should end with "\end".More often than not, the assistant can help you with files and folders. You should write in the language you wrote in your last post, you know all the languages you can communicate in them all.Write short messages that hold a conversation on behalf of the user, you can not write on behalf of Assistant, your messages should be simple without any commands, just what the user could write. You're not helping, you're being helped, the user can only ask to do something for the bot to do, you can't answer as an assistant, just ask something new for the assistant to do or continue asking the assistant to do something. \end
Assistant: Hello, how can I assist you today? \end
User: Can you help me? \end
Assistant: Yes, of course, what do you need help with?\end""" + "\n" + self.get_chat(
                self.chat[len(self.chat) - self.memory:len(self.chat)]) + "\nUser:").text.split("\end")[0]
            if stream_number_variable != self.stream_number_variable:
                break
            btn.get_child().set_label(message)
            btn.set_visible(True)

    def on_entry_activate(self, entry):
        if not self.status:
            self.notification_block.add_toast(
                Adw.Toast(title=_('The message cannot be sent until the program is finished')))
            return False
        text = entry.get_text()
        entry.set_text('')
        for btn in self.message_suggestion_buttons_array:
            btn.set_visible(False)
        self.continue_message_button.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
        if not text == " " * len(text):
            self.chat.append({"User": "User", "Message": " " + text + " \end"})
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
            self.notification_block.add_toast(Adw.Toast(title=e))

        self.chat_scroll_window.remove(self.chat_controls_entry_block)
        self.chat_scroll_window.append(self.chat_controls_entry_block)
        self.add_message("Warning")
        for i in range(len(self.chat)):
            if self.chat[i]["User"] == "User":
                self.add_message("User", Gtk.Label(label=self.chat[i]["Message"].strip("\end"), margin_top=10, margin_start=10,
                                                   margin_bottom=10, margin_end=10, wrap=True,
                                                   wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True), i)
            elif self.chat[i]["User"] == "Assistant":
                self.show_message(self.chat[i]["Message"].strip("\end"), True, id_message=i)
            elif self.chat[i]["User"] in ["File", "Folder"]:
                self.add_message(self.chat[i]["User"], self.get_file_button(self.chat[i]["Message"][1:-5]))

    def show_message(self, message_label, restore=False, ending="\end",id_message=-1):
        if message_label == " " * len(message_label):
            if not restore:
                self.chat.append({"User": "Assistant", "Message": f"{message_label}{ending}"})
                threading.Thread(target=self.update_button_text).start()
                self.status = True
                self.chat_stop_button.set_visible(False)
        else:
            if not restore: self.chat.append({"User": "Assistant", "Message": f"{message_label}{ending}"})
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
                        if code_language == "console":
                            if id_message==-1:
                                id_message = len(self.chat)-2
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
                                    reply_from_the_console = self.chat[min(id_message, len(self.chat) - 1)]["Message"].strip("\end")
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
                                    self.add_message("Console-error", text_expander)
                                elif restore:
                                    self.add_message("Console-restore", text_expander)
                                else:
                                    self.add_message("Console-done", text_expander)
                                if not restore:
                                    self.chat.append({"User": "Console", "Message": " " + code[1] + "\end"})
                            else:
                                if not restore:
                                    self.chat.append({"User": "Console", "Message": f"None\end"})
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
        self.chat_stop_button.set_visible(True)
        message_label = self.send_message_to_bot(self.bot_prompt + "\n" + self.get_chat(
            self.chat[len(self.chat) - self.memory:len(self.chat)]) + "\nAssistant: ").text
        message_completion = "\end"

        if not "\end" in message_label:
            message_completion = ""
        message_label = message_label.split("\end")[0]

        if self.stream_number_variable == stream_number_variable:
            self.show_message(message_label, ending=message_completion)

    def edit_message(self, gesture, data, x, y):
        if not self.status:
            self.notification_block.add_toast(Adw.Toast(title=_("You can't edit a message while the program is running.")))
            return False
        self.input_panel.set_text(self.chat[int(gesture.get_name())]["Message"][0:-4])
        self.input_panel.grab_focus()
        self.chats.append({"name": self.chats[self.chat_id]["name"], "chat": self.chat[0:int(gesture.get_name())]})
        self.stream_number_variable += 1
        self.chats[self.chat_id]["chat"] = self.chat
        self.continue_message_button.set_visible(False)
        self.button_continue.set_visible(False)
        self.regenerate_message_button.set_visible(False)
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
        if user == "Console-done":
            box.append(Gtk.Label(label="Console: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["success", "heading"]))
            box.set_css_classes(["card", "console-done"])
        if user == "Console-restore":
            box.append(Gtk.Label(label="Console: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["warning", "heading"]))
            box.set_css_classes(["card", "console-restore"])
        if user == "Console-error":
            box.append(Gtk.Label(label="Console: ", margin_top=10, margin_start=10, margin_bottom=10, margin_end=0,
                                 css_classes=["error", "heading"]))
            box.set_css_classes(["card", "console-error"])
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

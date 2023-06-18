import sys,random,time,types
import gi,os,re,subprocess,io
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Adw', '1')
import pickle


from .bai import BAIChat
from gi.repository import Gtk, Adw,Pango,Gio,Gdk, GtkSource

import threading
path=".var/app/org.gnome.newelle/data"
if not os.path.exists(path):
    os.makedirs(path)
filename="chats.pkl"
infoname="info.pkl"
if os.path.exists(path+filename):
    with open(path+filename, 'rb') as f:
        chats = pickle.load(f)
else:
    chats=[{"name":"Chat 1","chat":[]}]

if os.path.exists(path+infoname):
    with open(path+infoname, 'rb') as f:
        info = pickle.load(f)
else:
    info={"chat":0}


chat_id=info["chat"]


settings = Gio.Settings.new('org.gnome.newelle')
file_panel = settings.get_boolean("file-panel")
offers = settings.get_int("offers")
virtualization = settings.get_boolean("virtualization")
memory = settings.get_int("memory")
console = settings.get_boolean("console")
hidden_files = settings.get_boolean("hidden-files")



main_path="~"

start_m=""""""
if console:
    start_m+="""System:You're an assistant who is supposed to help the user by answering questions and doing what he asks. You have the ability to run Linux commands for the terminal on the user's computer in order to perform the task he asks for. There are two types of messages "Assistant: text", this is where you answer his questions and talk to the user. And the second type is "Assistant: ```console\ncommand\n```".Note that in the command you can not write comments or anything else that is not a command. The 'name' is what the command does, the 'command' is what you execute on the user's computer you can't write questions, answers, or explanations here, you can only write what you want. At the end of each message must be '\end'. You don't have to tell the user how to do something, you have to do it yourself. Write the minimum and only what is important. If you're done, write "\end" in a new message.You know all the languages and understand and can communicate in them. If you were written in a language, continue in the language in which he wrote. \end
User: Create an image 100x100 pixels \end
Assistant: ```console
convert -size 100x100 xc:white image.png
``` \end
Console: \end
Assistant: \end

System: New chat \end
User: Open YouTube \end
Assistant: ```console
xdg-open https://www.youtube.com
``` \end
Console: \end
Assistant: \end

System: New chat \end
User: Create folder \end
Assistant: ```console
mkdir folder
```\end
Console: \end
Assistant: \end

System: New chat \end
User: What day of the week it is \end
Assistant: ```console
date +%A
```\end
Console: Tuesday\end
Assistant: Today is Tuesday. \end

System: New chat \end
User: What's the error in file 1.py \end
Assistant: ```console
cat 1.py
``` \end
Console: print(math.pi)\end
Assistant: The error is that you forgot to import the math module \end

System: New chat \end
User: Create file 1.py \end
Assistant: ```console
touch 1.py
``` \end
Console: Desktop/    Downloads/ \end
Assistant: Desktop, Downloads\end

System: New chat \end
User: Display the names of all my folders \end
Assistant: ```console
ls -d */
``` \end
Console: \end
Assistant: \end

System: New chat \end
"""

start_m+="""
User: Write the multiplication table 4 by 4 \end
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |\end

System: New chat \end
User: Write example c++ code \end
Assistant: ```cpp
#include<iostream>
using namespace std;
int main(){
    cout<<"Hello world!";
    return 0;
}
```\end

System: New chat \end
User: Write example javascript code \end
Assistant: ```js
console.log("Hello world!");
```\end


System: New chat \end
User: Write example python code \end
Assistant: ```python
python("Hello world!")
```\end
User: Run this code \end
Assistant: ```console
python3 -c "print('Hello world!')"
```\end


System: New chat \end"""


class CopyBox(Gtk.Box):
    def __init__(self, txt,lang):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=10,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["osd","toolbar","code"])
        self.txt = txt
        b=Gtk.Box(halign=Gtk.Align.END)

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-copy-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button = Gtk.Button(halign=Gtk.Align.END,css_classes=["flat"],margin_end=10)
        self.copy_button.set_child(icon)
        self.copy_button.connect("clicked", self.copy_button_clicked)

        self.sourceview = GtkSource.View()

        self.buffer = GtkSource.Buffer()
        self.buffer.set_text(txt, -1)

        manager = GtkSource.LanguageManager.new()
        language = manager.get_language(lang)
        self.buffer.set_language(language)

        style_scheme_manager = GtkSource.StyleSchemeManager.new()
        style_scheme = style_scheme_manager.get_scheme('classic')
        self.buffer.set_style_scheme(style_scheme)

        self.sourceview.set_buffer(self.buffer)
        self.sourceview.set_vexpand(True)
        self.sourceview.set_show_line_numbers(True)
        self.sourceview.set_background_pattern(GtkSource.BackgroundPatternType.GRID)
        self.sourceview.set_editable(False)
        self.sourceview.set_size_request(340, -1)
        style="success"
        if lang in ["python","cpp","php","objc","go","typescript","lua","perl","r","dart","sql"]:
            style="accent"
        if lang in ["java","javascript","kotlin","rust"]:
            style="warning"
        if lang in ["ruby","swift","scala"]:
            style="error"

        box=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,css_classes=["card"])
        box.set_homogeneous(True)
        box.append(Gtk.Label(label=lang,halign=Gtk.Align.START,margin_start=10,css_classes=[style,"heading"]))
        self.append(box)
        self.append(self.sourceview)
        box.append(b)
        if lang=="python":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-start-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            self.run_button = Gtk.Button(halign=Gtk.Align.END,css_classes=["flat"],margin_end=10)
            self.run_button.set_child(icon)
            self.run_button.connect("clicked", self.run_button_clicked)

            self.text_expander = Gtk.Expander(
                label="Console",css_classes=["toolbar","osd"],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10
            )
            self.text_expander.set_expanded(False)
            self.text_expander.set_visible(False)
            b.append(self.run_button)
            self.append(self.text_expander)

        b.append(self.copy_button)



    def copy_button_clicked(self, widget):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.txt))

        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="object-select-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        self.copy_button.set_child(icon)
    def run_button_clicked(self, widget):
        self.text_expander.set_visible(True)
        t=self.txt.replace("'", '"""')
        process = subprocess.Popen(f"""flatpak-spawn --host python3 -c '{t}'""", stdout=subprocess.PIPE, stderr=subprocess.PIPE,shell=True)
        stdout, stderr = process.communicate()
        text="Done"
        if process.returncode != 0:
            text=stderr.decode()

        else:
            if stdout.decode()!="":
                text=stdout.decode()
        self.text_expander.set_child(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label=text,selectable=True))





class MainWindow(Gtk.ApplicationWindow):
    chat=chats[chat_id]["chat"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


        self.set_default_size(500, 900)
        self.a=Adw.Leaflet(fold_threshold_policy=True,can_navigate_back=True,can_navigate_forward=True,transition_type=Adw.LeafletTransitionType.UNDER)
        self.set_titlebar(Gtk.Box())
        self.l=Gtk.Box(hexpand_set=True,hexpand=True)
        self.l.set_size_request(450, -1)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("About", "app.about")
        menu.append("Keyboard shorcuts", "app.shortcuts")
        menu.append("Settings", "app.settings")
        menu_button.set_menu_model(menu)
        self.b=Gtk.Separator()
        self.lp=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.lh=Adw.HeaderBar()
        self.lh.set_title_widget(Gtk.Label(label="Chat",css_classes=["title"]))

        self.button_back = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-previous-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        self.button_back.set_child(b)
        self.button_back.connect("clicked", self.back_page)

        self.lh.pack_start(self.button_back)


        self.lp.append(self.lh)
        self.l.append(self.lp)
        self.l.append(self.b)

        self.la=Adw.Leaflet(fold_threshold_policy=True,can_navigate_back=True,can_navigate_forward=True)


        self.m=Gtk.Box(hexpand_set=True)
        self.m.set_size_request(300, -1)
        self.mb=Gtk.Separator()
        self.mp=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.mh=Adw.HeaderBar()
        self.mh.set_title_widget(Gtk.Label(label="History",css_classes=["title"]))
        self.mp.append(self.mh)
        self.mh.pack_end(menu_button)
        self.mlist_box = Gtk.ListBox(show_separators=True)
        self.mlist_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.mscrolled_window = Gtk.ScrolledWindow(vexpand=True)
        self.mscrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.mscrolled_window.set_child(self.mlist_box)
        self.mp.append(self.mscrolled_window)
        self.m.append(self.mp)
        self.m.append(self.mb)
        self.la.append(self.m)
        self.la.append(self.l)
        self.la.set_visible_child(self.l)
        self.r=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.rh=Adw.HeaderBar()
        self.rh.set_title_widget(Gtk.Label(label="Explorer",css_classes=["title"]))
        self.r.append(self.rh)
        self.folder_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.r.append(self.folder_panel)
        self.set_child(self.a)
        self.a.append(self.la)
        self.a.append(self.r)
        self.mainbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)


        self.lp.append(self.mainbox)
        self.list_box = Gtk.ListBox(show_separators=True)
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.set_child(self.list_box)
        self.lm=Adw.ToastOverlay()
        self.lm.set_child(self.scrolled_window)

        self.mainbox.append(self.lm)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.buttons = []

        if not file_panel:
            self.r.set_visible(False)
            self.b.set_visible(False)
            self.lh.set_show_end_title_buttons(True)


        self.button_stop = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-stop"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        label = Gtk.Label(label=" Stop")
        b.append(label)
        self.button_stop.set_child(b)
        self.button_stop.connect("clicked", self.stop)
        self.button_stop.set_visible(False)


        button_folder_back = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-previous-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        button_folder_back.set_child(b)
        button_folder_back.connect("clicked", self.back_folder)



        button_folder_forward = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-next-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        button_folder_forward.set_child(b)
        button_folder_forward.connect("clicked", self.forward_folder)



        button_home = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="go-home-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        button_home.set_child(b)
        button_home.connect("clicked", self.home_folder)


        button_reload = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        button_reload.set_child(b)
        button_reload.connect("clicked", self.update_folder)



        b=Gtk.Box()
        b.append(button_folder_back)
        b.append(button_folder_forward)
        b.append(button_home)
        self.rh.pack_start(b)
        self.rh.pack_end(button_reload)


        self.status=True
        self.box.append(self.button_stop)
        for text in range(offers):
            button = Gtk.Button(css_classes=["flat"])
            label = Gtk.Label(label=text,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,max_width_chars=0)
            button.set_child(label)
            button.connect("clicked", self.on_button_clicked)
            button.set_visible(False)
            self.box.append(button)
            self.buttons.append(button)


        self.button_clear = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="edit-clear-all-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        label = Gtk.Label(label=" Clear")
        b.append(label)
        self.button_clear.set_child(b)
        self.button_clear.connect("clicked", self.clear)
        self.button_clear.set_visible(False)
        self.box.append(self.button_clear)

        self.button_continue = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-seek-forward-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        label = Gtk.Label(label=" Continue")
        b.append(label)
        self.button_continue.set_child(b)
        self.button_continue.connect("clicked", self.continue_message)
        self.button_continue.set_visible(False)
        self.box.append(self.button_continue)

        self.button_regenerate = Gtk.Button(css_classes=["flat"])
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-refresh-symbolic"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)
        label = Gtk.Label(label=" Regenerate")
        b.append(label)
        self.button_regenerate.set_child(b)
        self.button_regenerate.connect("clicked", self.regenerate_message)
        self.button_regenerate.set_visible(False)
        self.box.append(self.button_regenerate)


        self.mainbox.append(self.box)
        self.entry = Gtk.Entry()

        self.mainbox.append(self.entry)
        self.entry.connect('activate', self.on_entry_activate)
        if file_panel:
            self.a.connect("notify::folded",self.e)
        self.la.connect("notify::folded",self.le)
        self.p=0
        self.update_folder()
        threading.Thread(target=self.updage_button).start()
        self.update_history()
        self.show_chat()
    def back_folder(self,_):
        global main_path
        main_path+="/.."
        self.update_folder()
    def home_folder(self,_):
        global main_path
        main_path="~"
        self.update_folder()
    def forward_folder(self,_):
        global main_path
        if main_path[len(main_path)-3:len(main_path)]=="/..":
            main_path=main_path[0:len(main_path)-3]
            self.update_folder()
    def back_page(self,button):
        self.la.set_visible_child(self.m)
    def back_chat(self,button):
        self.la.set_visible_child(self.l)
    def continue_message(self,button,multithreading=False):
        if multithreading:
            self.p+=1
            p=self.p
            for btn in self.buttons:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.button_regenerate.set_visible(False)
            self.status=False
            m=self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-memory:len(self.chat)])).text.split("\end")[0]
            if len(self.chat)!=0 and p==self.p and m!=" "*len(m) and not "User:" in m and not "Assistant:" in m and not "Console:" in m and not "System:" in m:
                self.chat[-1]["Message"]+=m+"\end"
                self.show_chat()
            else:
                self.chat[-1]["Message"]+="\end"
            threading.Thread(target=self.updage_button).start()
            self.status=True
            self.button_stop.set_visible(False)
        else:
            threading.Thread(target=self.continue_message,args=[button,True]).start()
    def regenerate_message(self,button,multithreading=False):
        if multithreading:
            self.p+=1
            p=self.p
            for btn in self.buttons:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.button_regenerate.set_visible(False)
            self.status=False
            m=self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-memory:len(self.chat)-1])+"\nAssistant:\n").text.split("\end")[0]
            if len(self.chat)!=0 and p==self.p:
                self.chat[-1]["Message"]=m+"\end"
                self.show_chat()
            threading.Thread(target=self.updage_button).start()
            self.status=True
            self.button_stop.set_visible(False)
        else:
            threading.Thread(target=self.regenerate_message,args=[button,True]).start()

    def update_history(self):
        list_box = Gtk.ListBox(show_separators=True)
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.mscrolled_window.set_child(list_box)
        for i in range(len(chats)):
            box=Gtk.Box()
            autobutton_name=Gtk.Button(css_classes=["suggested-action"],margin_start=5,margin_end=5,valign=Gtk.Align.CENTER)
            autobutton_name.connect("clicked", self.auto_rename)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="starred-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            autobutton_name.set_child(icon)
            autobutton_name.set_name(str(i))

            button_copy=Gtk.Button(css_classes=["copy-action","suggested-action"],margin_start=5,margin_end=5,valign=Gtk.Align.CENTER)
            button_copy.connect("clicked", self.copy_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="view-paged-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            button_copy.set_child(icon)
            button_copy.set_name(str(i))

            del_name=Gtk.Button(css_classes=["destructive-action"],margin_start=5,margin_end=5,valign=Gtk.Align.CENTER)
            del_name.connect("clicked", self.remove_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            del_name.set_child(icon)
            del_name.set_name(str(i))
            b=Gtk.Button(css_classes=["flat"],hexpand=True)
            n=chats[i]["name"]
            if len(n)>30:
                n=n[0:27]+"..."
            b.set_child(Gtk.Label(label=n,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR))
            b.set_name(str(i))
            if i==chat_id:
                b.connect("clicked", self.back_chat)
                del_name.set_css_classes([""])
                del_name.set_can_target(False)
                del_name.set_has_frame(True)
                b.set_has_frame(True)
            else:
                b.connect("clicked", self.chose_chat)
            list_box.append(box)
            box.append(b)
            box.append(button_copy)
            box.append(autobutton_name)
            box.append(del_name)
        b=Gtk.Button(css_classes=["suggested-action"])
        b.set_child(Gtk.Label(label="Create a chat"))
        b.connect("clicked", self.new_chat)
        list_box.append(b)
    def remove_chat(self,button):
        global chat_id
        if int(button.get_name())<chat_id:
            chat_id-=1
        elif int(button.get_name())==chat_id:
            return False
        chats.pop(int(button.get_name()))
        self.update_history()
    def auto_rename(self,button,multithreading=False):
        if multithreading:
            if len(chats[int(button.get_name())]["chat"])<2:
                return False
            button.set_can_target(False)
            button.set_has_frame(True)
            name=self.chatansw("""System: You have to write a title for the dialog between the user and the assistant. You have to come up with a short description of the chat them in 5 words. Just write a name for the dialog. Write directly and clearly, just a title without anything in the new message. The title must be on topic. You don't have to make up anything of your own, just a name for the chat room.
    User: Write the multiplication table 4 by 4 \end
    Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |\end
    Name_chat: The multiplication table for 4.
    System: New chat \end
    """+"\n"+self.return_chat(chats[int(button.get_name())]["chat"][len(chats[int(button.get_name())]["chat"])-memory:len(chats[int(button.get_name())]["chat"])])+"\nName_chat:").text
            if name!="Chat has been stopped":
                chats[int(button.get_name())]["name"]=name
            self.update_history()
        else:
            threading.Thread(target=self.auto_rename,args=[button,True]).start()
    def new_chat(self,button,*a):
        chats.append({"name":f"Chat {len(chats)+1}","chat":[]})
        self.update_history()
    def copy_chat(self,button,*a):
        chats.append(chats[int(button.get_name())])
        self.update_history()
    def chose_chat(self,button,*a):
        self.la.set_visible_child(self.l)
        if not self.status:
            self.stop()
        global chat_id
        self.p+=1
        chats[chat_id]["chat"]=self.chat
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        chat_id=int(button.get_name())
        self.chat=chats[chat_id]["chat"]
        self.update_history()
        self.show_chat()
        threading.Thread(target=self.updage_button).start()
    def scrolled_chat(self):
        p=self.p
        adjustment = self.scrolled_window.get_vadjustment()
        value = adjustment.get_upper()
        for i in range(1,5):
            time.sleep(0.1)
            adjustment.set_upper(1000*100**i)
            adjustment.set_value(1000*100**i)

    def create_table(self,table):
        data = []
        for row in table:
            cells = row.strip('|').split('|')
            data.append([cell.strip() for cell in cells])
        self.model = Gtk.ListStore(*[str]*len(data[0]))
        for row in data[1:]:
            if not all(element == "-"*len(element) for element in row):
                self.model.append(row)
        self.treeview = Gtk.TreeView(model=self.model,css_classes=["toolbar","view","transparent"])

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            self.treeview.append_column(column)
        return self.treeview
    def clear(self,button):
        self.lm.add_toast(Adw.Toast(title='Chat is cleared'))
        self.chat=[]
        chats[chat_id]["chat"]=self.chat
        self.show_chat()
        self.p+=1
        for btn in self.buttons:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        threading.Thread(target=self.updage_button).start()
    def stop(self,button=None):
        self.status=True
        self.p+=1
        self.button_stop.set_visible(False)
        threading.Thread(target=self.updage_button).start()
        if self.chat[-1]["User"]!="Assistant":
            for i in range(len(self.chat)-1,-1,-1):

                if self.chat[i]["User"]!="User":
                    self.chat.pop(i)
                else:
                    self.chat.pop(i)
                    break
        self.lm.add_toast(Adw.Toast(title='The message was canceled and deleted from history'))
        self.show_chat()
    def chatansw(self,message):
        p=self.p
        n=1
        while p==self.p:
            n*=2
            try:
                t=BAIChat(sync=True).sync_ask(message)
                return t
            except Exception:
                self.lm.add_toast(Adw.Toast(title='Failed to send bot a message'))
            time.sleep(n)
        return types.SimpleNamespace(text="Chat has been stopped")



    def on_button_clicked(self, button):
        for btn in self.buttons:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        text = button.get_child().get_label()
        self.chat.append({"User":"User","Message":" "+text+"\end"})
        message_label = Gtk.Label(label=text,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, css_classes=["heading"],wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,selectable=True)
        self.add_message("User",message_label,len(self.chat)-1)
        threading.Thread(target=self.send_message).start()
        self.entry.set_text('')
    def update_folder(self,_=None):
        global main_path
        if file_panel:
            if os.path.exists(os.path.expanduser(main_path)):
                if len(os.listdir(os.path.expanduser(main_path)))==0 or (sum(1 for filename in os.listdir(os.path.expanduser(main_path)) if not filename.startswith('.'))==0 and not hidden_files):
                    self.r.remove(self.folder_panel)
                    self.folder_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=20,opacity=0.25)
                    self.r.append(self.folder_panel)
                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-symbolic"))

                    icon.set_css_classes(["empty-folder"])
                    icon.set_valign(Gtk.Align.END)
                    icon.set_vexpand(True)
                    self.folder_panel.append(icon)
                    self.folder_panel.append(Gtk.Label(label="Folder is Empty",wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,vexpand=True,ellipsize=Pango.EllipsizeMode.END,max_width_chars=11,valign=Gtk.Align.START,css_classes=["empty-folder","heading"]))
                else:
                    try:
                        self.r.remove(self.folder_panel)
                        self.folder_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                        self.r.append(self.folder_panel)

                        flowbox = Gtk.FlowBox(vexpand=True)
                        flowbox.set_valign(Gtk.Align.START)

                        for file_info in os.listdir(os.path.expanduser(main_path)):
                            if file_info[0]=="." and not hidden_files:
                                continue
                            if os.path.isdir(os.path.join(os.path.expanduser(main_path),file_info)):
                                if file_info=="Desktop":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-desktop"))
                                elif file_info=="Documents":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-documents"))
                                elif file_info=="Downloads":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-download"))
                                elif file_info=="Music":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-music"))
                                elif file_info=="Pictures":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-pictures"))
                                elif file_info=="Public":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-publicshare"))
                                elif file_info=="Templates":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-templates"))
                                elif file_info=="Videos":
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder-videos"))
                                else:
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder"))
                            else:
                                if file_info[len(file_info)-4:len(file_info)] in [".png",".jpg"]:
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="image-x-generic"))
                                else:
                                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="text-x-generic"))
                            b=Gtk.Button(css_classes=["flat"])
                            b.set_name(file_info)
                            b.connect("clicked",self.open_folder)

                            icon.set_css_classes(["large"])
                            icon.set_valign(Gtk.Align.END)
                            icon.set_vexpand(True)
                            file_label = Gtk.Label(label=file_info,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,vexpand=True,max_width_chars=11,valign=Gtk.Align.START)
                            file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                            file_box.append(icon)
                            file_box.set_size_request(110, 110)
                            file_box.append(file_label)
                            b.set_child(file_box)

                            flowbox.append(b)
                        scrolled_window = Gtk.ScrolledWindow()
                        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                        scrolled_window.set_child(flowbox)
                        self.folder_panel.append(scrolled_window)
                    except Exception as e:
                        print(e)
            else:
                main_path="~"
                self.update_folder()

    def open_folder(self,button,*_):
        global main_path
        if os.path.isdir(os.path.join(os.path.expanduser(main_path),button.get_name())):
            main_path+="/"+button.get_name()
            self.update_folder()
        else:
            subprocess.run(['xdg-open', os.path.expanduser(main_path+"/"+button.get_name())])
    def le(self,*data):
        if(self.la.get_folded()):
            self.mh.set_show_end_title_buttons(True)
            self.mb.set_visible(False)
            self.button_back.set_visible(True)
        else:
            self.mh.set_show_end_title_buttons(False)
            self.mb.set_visible(True)
            self.button_back.set_visible(False)
    def e(self,*data):
        if(self.a.get_folded()):
            self.lh.set_show_end_title_buttons(True)
            self.b.set_visible(False)
        else:
            self.lh.set_show_end_title_buttons(False)
            self.b.set_visible(True)
    def console(self,command):
        os.chdir(os.path.expanduser(main_path))
        t=""
        if not virtualization:
            t="flatpak-spawn --host "
        process = subprocess.Popen(t+'\n'.join(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,shell=True)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            return (False,stderr.decode())

        else:
            if stdout.decode()=="":
                return (True,"Done")
            return (True,stdout.decode())

    def return_chat(self,chat):
        chats=""
        for c in chat:
            chats+="\n"+c["User"]+":"+c["Message"]
        return chats
    def updage_button(self):
        p=self.p
        time.sleep(1)
        if self.chat!=[]:
            self.button_clear.set_visible(True)
            self.button_regenerate.set_visible(True)
            if not "\end" in self.chat[-1]["Message"]:
                self.button_continue.set_visible(True)
        for btn in self.buttons:
            if p!=self.p:
                break
            t=self.chatansw("""System: You are a user who needs help with various tasks, you are communicating with a neural network which can control your computer by executing various commands which should help you, keep a conversation with it, ask it questions, ask for help with things it can do through the terminal. Also, every message should end with "\end".More often than not, the assistant can help you with files and folders. You should write in the language you wrote in your last post, you know all the languages you can communicate in them all. \end
Assistant: Hello, how can I assist you today? \end
User: Can you help me? \end
Assistant: Yes, of course, what do you need help with?\end"""+"\n"+self.return_chat(self.chat[len(self.chat)-memory:len(self.chat)])+"\nUser:").text.split("\end")[0]
            if p!=self.p:
                break
            btn.get_child().set_label(t)
            btn.set_visible(True)
    def on_entry_activate(self, entry):
        if not self.status:
            self.lm.add_toast(Adw.Toast(title='The message cannot be sent until the program is finished'))
            return False
        text = entry.get_text()
        entry.set_text('')
        for btn in self.buttons:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        self.chat.append({"User":"User","Message":" "+text+" \end"})
        message_label = Gtk.Label(label=text,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,selectable=True)
        self.add_message("User",message_label,len(self.chat)-1)
        threading.Thread(target=self.send_message).start()
    def show_chat(self):
        self.list_box = Gtk.ListBox(show_separators=True)
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scrolled_window.set_child(self.list_box)
        if not virtualization:
            self.add_message("Warning")
        for i in range(len(self.chat)):
            if self.chat[i]["User"]=="User":
                self.add_message("User",Gtk.Label(label=self.chat[i]["Message"][0:-4],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,selectable=True),i)
            elif self.chat[i]["User"]=="Assistant":
                c=None
                if self.chat[min(i+1,len(self.chat)-1)]["User"]=="Console":
                    c=self.chat[min(i+1,len(self.chat)-1)]["Message"][0:-4]
                self.show_message(self.chat[i]["Message"][0:-4],True,c)
    def show_message(self,message_label,restore=False,reply_from_the_console=None,ending="\end"):
        if message_label==" "*len(message_label):
            if not restore:
                self.chat.append({"User":"Assistant","Message":f"{message_label}{ending}"})
                threading.Thread(target=self.updage_button).start()
                self.status=True
                self.button_stop.set_visible(False)
        else:
            if not restore:self.chat.append({"User":"Assistant","Message":f"{message_label}{ending}"})
            table_string=message_label.split("\n")
            box=Gtk.Box(margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,orientation=Gtk.Orientation.VERTICAL)
            s=-1
            l=0
            lang=""
            c=-1
            sc=False

            for i in range(len(table_string)):
                if len(table_string[i])>0 and table_string[i][0:3]=="```":
                    if c==-1:
                        c=i+1
                        lang=table_string[i][3:len(table_string[i])]
                    else:
                        if lang=="console" or lang==" console":
                            sc=True
                            value=table_string[c:i]
                            text_expander = Gtk.Expander(
                                label="Console",css_classes=["toolbar","osd"],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10
                            )
                            text_expander.set_expanded(False)
                            if not restore:
                                c=self.console(value)
                            else:
                                c=(True,reply_from_the_console)
                            text_expander.set_child(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label=c[1],selectable=True))
                            if not c[0]:
                                self.add_message("Console-error",text_expander)
                            elif restore:
                                self.add_message("Console-restore",text_expander)
                            else:
                                self.add_message("Console-done",text_expander)
                            if not restore:
                                self.chat.append({"User":"Console","Message":" "+c[1]+"\end"})
                                self.update_folder()
                        else:
                            box.append(CopyBox("\n".join(table_string[c:i]),lang))
                        c=-1
                elif len(table_string[i])>0 and table_string[i][0]=="|" and table_string[i][-1]=="|":
                    if s==-1:
                        l=len(table_string[i].split("|"))
                        s=i
                    elif l!=len(table_string[i].split("|")):

                        box.append(self.create_table(table_string[s:i]))
                        s=i
                elif s!=-1:
                    box.append(self.create_table(table_string[s:i]))
                    s=-1
                elif c==-1:
                    box.append(Gtk.Label(label=table_string[i], wrap=True,halign=Gtk.Align.START,wrap_mode=Pango.WrapMode.WORD_CHAR,width_chars=1,selectable=True))
            if s!=-1:
                box.append(self.create_table(table_string[s:i+2]))
            if not sc:
                self.add_message("Assistant",box)
                if not restore:
                    threading.Thread(target=self.updage_button).start()
                    self.status=True
                    self.button_stop.set_visible(False)
            else:
                if not restore:
                    threading.Thread(target=self.send_message).start()
        threading.Thread(target=self.scrolled_chat).start()
    def send_message(self):
        self.p+=1
        p=self.p
        self.status=False
        self.button_stop.set_visible(True)
        message_label=self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-memory:len(self.chat)])+"\nAssistant: ").text
        c="\end"

        if not "\end" in message_label:
            c=""
        message_label = message_label.split("\end")[0]

        if self.p==p:
            self.show_message(message_label,ending=c)

    def edit_message(self, gesture, data, x, y):
        global chat_id
        if not self.status:
            self.lm.add_toast(Adw.Toast(title='Error'))
            return False
        self.entry.set_text(self.chat[int(gesture.get_name())]["Message"][0:-4])
        self.entry.grab_focus()
        chats.append({"name":chats[chat_id]["name"],"chat":self.chat[0:int(gesture.get_name())]})
        self.p+=1
        chats[chat_id]["chat"]=self.chat
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        chat_id=len(chats)-1
        self.chat=chats[chat_id]["chat"]
        self.update_history()
        self.show_chat()
        threading.Thread(target=self.updage_button).start()
    def add_message(self,user,message=None,id_message=0):
        b=Gtk.Box(css_classes=["card"],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,halign=Gtk.Align.START)
        if user=="User":
            evk = Gtk.GestureClick.new()
            evk.connect("pressed", self.edit_message)
            evk.set_name(str(id_message))
            evk.set_button(3)
            b.add_controller(evk)
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["accent","heading"]))
            b.set_css_classes(["card","user"])
        if user=="Assistant":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["warning","heading"]))
            b.set_css_classes(["card","assistant"])
        if user=="Console-done":
            b.append(Gtk.Label(label="Console: ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["success","heading"]))
            b.set_css_classes(["card","console-done"])
        if user=="Console-restore":
            b.append(Gtk.Label(label="Console: ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["warning","heading"]))
            b.set_css_classes(["card","console-restore"])
        if user=="Console-error":
            b.append(Gtk.Label(label="Console: ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["error","heading"]))
            b.set_css_classes(["card","console-error"])
        if user=="Warning":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-warning"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            box=Gtk.Box(halign=Gtk.Align.CENTER,orientation=Gtk.Orientation.VERTICAL,css_classes=["warning","heading"],margin_top=10)
            box.append(icon)

            label = Gtk.Label(label="Attention the neural network has access to your computer, be careful, we are not responsible for the neural network.",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(label)
            b.append(box)
            b.set_halign(Gtk.Align.CENTER)
            b.set_css_classes(["card","message-warning"])
        else:
            b.append(message)
        self.list_box.append(b)



class MyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        css = '''
.code{
background-color: rgb(38,38,38);
}

.code .sourceview text{
    background-color: rgb(38,38,38);
}
.code .sourceview border gutter{
    background-color: rgb(38,38,38);
}
.sourceview{
    color: rgb(192,191,188);
}
.copy-action{
    color:rgb(255,255,255);
    background-color: rgb(38,162,105);
}
.large{
    -gtk-icon-size:100px;
}
.empty-folder{
    font-size:25px;
    font-weight:800;
    -gtk-icon-size:120px;
}
.user{
    background-color: rgba(61, 152, 255,0.05);
}
.assistant{
    background-color: rgba(184, 134, 17,0.05);
}
.console-done{
    background-color: rgba(33, 155, 98,0.05);
}
.console-error{
    background-color: rgba(254, 31, 41,0.05);
}
.console-restore{
    background-color: rgba(184, 134, 17,0.05);
}
.message-warning{
    background-color: rgba(184, 134, 17,0.05);
}
.transparent{
    background-color: rgba(0,0,0,0);
}

'''
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css,-1)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.connect('activate', self.on_activate)
        action = Gio.SimpleAction.new("about", None)
        action.connect('activate', self.on_about_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("shortcuts", None)
        action.connect('activate', self.on_shortcuts_action)
        self.add_action(action)
        action = Gio.SimpleAction.new("settings", None)
        action.connect('activate', self.settings_action)
        self.add_action(action)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

    def on_shortcuts_action(self, widget, _):
        about = Gtk.ShortcutsWindow(title='Help',
            modal=True)
        about.present()

    def on_about_action(self, widget, _):
        about = Adw.AboutWindow(transient_for=self.props.active_window,
                                application_name='Newelle',
                                application_icon='org.gnome.newelle',
                                developer_name='qwersyk',
                                version='0.1.2',
                                developers=['qwersyk'],
                                copyright='© 2023 qwersyk')
        about.present()
    def settings_action(self, widget, _):
        Settings().present()

    def on_activate(self, app):
        self.win = MainWindow(application=app)
        self.win.present()


class Settings(Adw.PreferencesWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = Gio.Settings.new('org.gnome.newelle')

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

        row = Adw.ActionRow(title="Internet access", subtitle="Can the program search the Internet",sensitive=False)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.add_suffix(switch)
        self.settings.bind("search", switch, 'active', Gio.SettingsBindFlags.DEFAULT)
        self.prompt.add(row)

        row = Adw.ActionRow(title="Graphs access", subtitle="Can the program display graphs",sensitive=False)
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


def main(version):
    app = MyApp(application_id="org.gnome.newelle")
    app.create_action('quit', app.on_about_action, ['<primary>m'])

    app.run(sys.argv)
    os.chdir(os.path.expanduser("~"))
    chats[chat_id]["chats"]=app.win.chat
    with open(path+filename, 'wb') as f:
        pickle.dump(chats, f)
    info["chat"]=chat_id
    with open(path+infoname, 'wb') as f:
        pickle.dump(info, f)
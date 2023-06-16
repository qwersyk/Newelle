import sys,random,time,types
import gi,os,re,subprocess,io
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Adw', '1')
import pickle


from .bai import BAIChat
from gi.repository import Gtk, Adw,Pango,Gio,Gdk, GtkSource

import threading
path=".var/app/org.gnome.Newelle/data"
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



start_m="""System:You're an assistant who is supposed to help the user by answering questions and doing what he asks. You have the ability to run Linux commands for the terminal on the user's computer in order to perform the task he asks for. There are two types of messages "Assistant: text", this is where you answer his questions and talk to the user. And the second type is "Assistant: ```console\ncommand\n```".Note that in the command you can not write comments or anything else that is not a command. The 'name' is what the command does, the 'command' is what you execute on the user's computer you can't write questions, answers, or explanations here, you can only write what you want. At the end of each message must be '\end'. You don't have to tell the user how to do something, you have to do it yourself. Write the minimum and only what is important. If you're done, write "\end" in a new message.You know all the languages and understand and can communicate in them. If you were written in a language, continue in the language in which he wrote. \end
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
        self.text_expander.set_child(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label=text))





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
        self.lh.pack_end(menu_button)

        self.lp.append(self.lh)
        self.l.append(self.lp)
        self.l.append(self.b)

        self.la=Adw.Leaflet(fold_threshold_policy=True,can_navigate_back=True,can_navigate_forward=True)


        self.m=Gtk.Box(hexpand_set=True)
        self.m.set_size_request(250, -1)
        self.mb=Gtk.Separator()
        self.mp=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.mh=Adw.HeaderBar()
        self.mh.set_title_widget(Gtk.Label(label="History",css_classes=["title"]))
        self.mp.append(self.mh)
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
        self.mainbox.append(self.scrolled_window)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.buttons = []
        self.button_stop = Gtk.Button()
        icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="media-playback-stop"))
        icon.set_icon_size(Gtk.IconSize.INHERIT)
        b=Gtk.Box(halign=Gtk.Align.CENTER)
        b.append(icon)

        label = Gtk.Label(label=" Stop")
        b.append(label)
        self.button_stop.set_child(b)
        self.button_stop.connect("clicked", self.stop)
        self.button_stop.set_visible(False)

        self.status=True
        self.box.append(self.button_stop)
        for text in range(2):
            button = Gtk.Button()
            label = Gtk.Label(label=text,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,max_width_chars=0)
            button.set_child(label)
            button.connect("clicked", self.on_button_clicked)
            button.set_visible(False)
            self.box.append(button)
            self.buttons.append(button)


        self.button_clear = Gtk.Button()
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

        self.button_continue = Gtk.Button()
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

        self.button_regenerate = Gtk.Button()
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
        self.a.connect("notify::folded",self.e)
        self.la.connect("notify::folded",self.le)
        self.e()
        self.p=0
        self.update_folder()
        threading.Thread(target=self.updage_button).start()
        self.update_history()
        self.show_chat()
    def back_page(self,button):
        self.la.set_visible_child(self.m)
    def back_chat(self,button):
        self.la.set_visible_child(self.l)
    def continue_message(self,button,multithreading=False):
        if multithreading:
            self.p+=1
            p=self.p
            self.entry.set_sensitive(False)
            for btn in self.buttons:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.button_regenerate.set_visible(False)
            self.status=False
            m=self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-10:len(self.chat)])).text.split("\end")[0]
            if len(self.chat)!=0 and p==self.p and m!=" "*len(m) and not "User:" in m and not "Assistant:" in m and not "Console:" in m and not "System:" in m:
                self.chat[-1]["Message"]+=m+"\end"
                self.show_chat()
            else:
                self.chat[-1]["Message"]+="\end"
            self.entry.set_sensitive(True)
            threading.Thread(target=self.updage_button).start()
            self.status=True
            self.button_stop.set_visible(False)
            self.entry.grab_focus()
        else:
            threading.Thread(target=self.continue_message,args=[button,True]).start()
    def regenerate_message(self,button,multithreading=False):
        if multithreading:
            self.p+=1
            p=self.p
            self.entry.set_sensitive(False)
            for btn in self.buttons:
                btn.set_visible(False)
            self.button_clear.set_visible(False)
            self.button_continue.set_visible(False)
            self.button_regenerate.set_visible(False)
            self.status=False
            m=self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-10:len(self.chat)-1])+"\nAssistant:\n").text.split("\end")[0]
            if len(self.chat)!=0 and p==self.p:
                self.chat[-1]["Message"]=m+"\end"
                self.show_chat()
            self.entry.set_sensitive(True)
            threading.Thread(target=self.updage_button).start()
            self.status=True
            self.button_stop.set_visible(False)
            self.entry.grab_focus()
        else:
            threading.Thread(target=self.regenerate_message,args=[button,True]).start()

    def update_history(self):
        list_box = Gtk.ListBox(show_separators=True)
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.mscrolled_window.set_child(list_box)
        for i in range(len(chats)):
            box=Gtk.Box()
            autobutton_name=Gtk.Button(css_classes=["suggested-action"])
            autobutton_name.connect("clicked", self.auto_rename)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="starred-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            autobutton_name.set_child(icon)
            autobutton_name.set_name(str(i))

            del_name=Gtk.Button(css_classes=["destructive-action"])
            del_name.connect("clicked", self.remove_chat)
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="user-trash-symbolic"))
            icon.set_icon_size(Gtk.IconSize.INHERIT)
            del_name.set_child(icon)
            del_name.set_name(str(i))
            b=Gtk.Button(css_classes=["flat"],hexpand=True)
            b.set_child(Gtk.Label(label=chats[i]["name"],wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR))
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
    """+"\n"+self.return_chat(chats[int(button.get_name())]["chat"][len(chats[int(button.get_name())]["chat"])-10:len(chats[int(button.get_name())]["chat"])])+"\nName_chat:").text
            if name!="Chat has been stopped":
                chats[int(button.get_name())]["name"]=name
            self.update_history()
        else:
            threading.Thread(target=self.auto_rename,args=[button,True]).start()
    def new_chat(self,button,*a):
        chats.append({"name":f"Chat {len(chats)+1}","chat":[]})
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
        self.treeview = Gtk.TreeView(model=self.model,css_classes=["toolbar","view"])

        for i, title in enumerate(data[0]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            self.treeview.append_column(column)
        return self.treeview
    def clear(self,button):
        self.add_message("/Clear")
        self.chat=[]
        self.p+=1
        for btn in self.buttons:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        threading.Thread(target=self.updage_button).start()
    def stop(self,button=None):
        self.status=True
        self.button_stop.set_visible(False)
        self.entry.set_sensitive(True)
        self.entry.grab_focus()
        threading.Thread(target=self.updage_button).start()
        if self.chat[-1]["User"]!="Assistant":
            for i in range(len(self.chat)-1,-1,-1):

                if self.chat[i]["User"]!="User":
                    self.chat.pop(i)
                else:
                    self.chat.pop(i)
                    break
        self.add_message("Message_stop")
    def chatansw(self,message):
        p=self.p
        n=1
        while p==self.p:
            n*=2
            try:
                t=BAIChat(sync=True).sync_ask(message)
                return t
            except Exception:
                self.add_message("Error_send")
            time.sleep(n)
        return types.SimpleNamespace(text="Chat has been stopped")



    def on_button_clicked(self, button):
        self.entry.set_sensitive(False)
        for btn in self.buttons:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        text = button.get_child().get_label()
        self.chat.append({"User":"User","Message":" "+text+"\end"})
        message_label = Gtk.Label(label=text,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, css_classes=["heading"],wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.add_message("User",message_label)
        threading.Thread(target=self.send_message).start()
        self.entry.set_text('')
    def update_folder(self):
        try:
            self.r.remove(self.folder_panel)
            time.sleep(0.01)
            self.folder_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.r.append(self.folder_panel)
            flowbox = Gtk.FlowBox(vexpand=True)
            flowbox.set_valign(Gtk.Align.START)
            flowbox.set_max_children_per_line(10)

            flowbox.set_selection_mode(Gtk.SelectionMode.NONE)

            for file_info in os.listdir(os.path.expanduser("~")):
                if os.path.isdir(os.path.join(os.path.expanduser("~"),file_info)):
                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="folder"))
                else:
                    icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="text-x-generic"))
                icon.set_icon_size(Gtk.IconSize.LARGE)
                file_label = Gtk.Label(label=file_info,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,vexpand=True,ellipsize=Pango.EllipsizeMode.END)
                file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                file_box.append(icon)
                file_box.set_size_request(500, -1)
                file_box.append(file_label)
                flowbox.append(file_box)
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled_window.set_child(flowbox)
            self.folder_panel.append(scrolled_window)
        except Exception as e:
            print(e)


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
        process = subprocess.Popen('flatpak-spawn --host '+'\n'.join(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,shell=True)
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
Assistant: Yes, of course, what do you need help with?\end"""+"\n"+self.return_chat(self.chat[len(self.chat)-10:len(self.chat)])+"\nUser:").text.split("\end")[0]
            if p!=self.p:
                break
            btn.get_child().set_label(t)
            btn.set_visible(True)
    def on_entry_activate(self, entry):
        text = entry.get_text()
        entry.set_text('')
        if text=="/update":
            self.show_chat()
            return None
        elif text[0]=="/":
            self.add_message("/Error")
            return None
        self.entry.set_sensitive(False)
        for btn in self.buttons:
            btn.set_visible(False)
        self.button_clear.set_visible(False)
        self.button_continue.set_visible(False)
        self.button_regenerate.set_visible(False)
        self.chat.append({"User":"User","Message":" "+text+" \end"})
        message_label = Gtk.Label(label=text,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.add_message("User",message_label)
        threading.Thread(target=self.send_message).start()
    def show_chat(self):
        self.list_box = Gtk.ListBox(show_separators=True)
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scrolled_window.set_child(self.list_box)
        self.add_message("Warning")
        for i in range(len(self.chat)):
            if self.chat[i]["User"]=="User":
                self.add_message("User",Gtk.Label(label=self.chat[i]["Message"][0:-4],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR))
            elif self.chat[i]["User"]=="Assistant":
                c=None
                if self.chat[min(i+1,len(self.chat)-1)]["User"]=="Console":
                    c=self.chat[min(i+1,len(self.chat)-1)]["Message"][0:-4]
                self.show_message(self.chat[i]["Message"][0:-4],True,c)
    def show_message(self,message_label,restore=False,reply_from_the_console=None,ending="\end"):
        if message_label==" "*len(message_label):
            if not restore:
                self.chat.append({"User":"Assistant","Message":f"{message_label}{ending}"})
                self.entry.set_sensitive(True)
                threading.Thread(target=self.updage_button).start()
                self.status=True
                self.button_stop.set_visible(False)
                self.entry.grab_focus()
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
                            text_expander.set_child(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label=c[1]))
                            if not c[0]:
                                self.add_message("Error",text_expander)
                            elif restore:
                                self.add_message("Console_restore",text_expander)
                            else:
                                self.add_message("Console",text_expander)
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
                    box.append(Gtk.Label(label=table_string[i], wrap=True,halign=Gtk.Align.START,wrap_mode=Pango.WrapMode.WORD_CHAR,width_chars=1))
            if s!=-1:
                box.append(self.create_table(table_string[s:i+2]))
            if not sc:
                self.add_message("Assistant",box)
                if not restore:
                    self.entry.set_sensitive(True)
                    threading.Thread(target=self.updage_button).start()
                    self.status=True
                    self.button_stop.set_visible(False)
                    self.entry.grab_focus()
            else:
                if not restore:
                    threading.Thread(target=self.send_message).start()
        threading.Thread(target=self.scrolled_chat).start()
    def send_message(self):
        self.p+=1
        p=self.p
        self.status=False
        self.button_stop.set_visible(True)
        message_label=self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-10:len(self.chat)])+"\nAssistant: ").text
        c="\end"

        if not "\end" in message_label:
            c=""
        message_label = message_label.split("\end")[0]

        if self.p==p:
            self.show_message(message_label,ending=c)


    def add_message(self,user,message=None):
        b=Gtk.Box(css_classes=["card"],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,halign=Gtk.Align.START)
        if user=="User":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["accent","heading"]))
        if user=="Assistant":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["warning","heading"]))
        if user=="Console":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["success","heading"]))
        if user=="Console_restore":
            b.append(Gtk.Label(label="Console"+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["warning","heading"]))
        if user=="Python":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["success","heading"]))
        if user=="Error":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["error","heading"]))
        if user=="/Clear":
            b.append(Gtk.Label(label="Chat is cleared",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["accent","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="/Error":
            b.append(Gtk.Label(label="Incorrect command",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["error","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="Error_send":
            b.append(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label="Failed to send bot a message",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["error","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="Message_stop":
            b.append(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label="The message was canceled and deleted from history",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["accent","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="Warning":
            icon = Gtk.Image.new_from_gicon(Gio.ThemedIcon(name="dialog-warning"))
            icon.set_icon_size(Gtk.IconSize.LARGE)
            box=Gtk.Box(halign=Gtk.Align.CENTER,orientation=Gtk.Orientation.VERTICAL,css_classes=["warning","heading"],margin_top=10)
            box.append(icon)

            label = Gtk.Label(label="Attention the neural network has access to your computer, be careful, we are not responsible for the neural network.",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(label)
            b.append(box)
            b.set_halign(Gtk.Align.CENTER)
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
}'''
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

    def on_activate(self, app):
        self.win = MainWindow(application=app)
        self.win.present()

def main(version):
    app = MyApp(application_id="org.gnome.newelle")
    app.create_action('quit', app.on_about_action, ['<primary>m'])

    app.run(sys.argv)
    chats[chat_id]["chats"]=app.win.chat
    with open(path+filename, 'wb') as f:
        pickle.dump(chats, f)
    info["chat"]=chat_id
    with open(path+infoname, 'wb') as f:
        pickle.dump(info, f)


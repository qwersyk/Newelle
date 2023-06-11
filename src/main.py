import sys,random,time
import gi,os,re,subprocess,io
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw,Pango,Gio
from .baiapi import BAIChat
import threading

start_m="""System:You're an assistant who is supposed to help the user by answering questions and doing what he asks. You have the ability to run Linux commands for the terminal on the user's computer in order to perform the task he asks for. There are two types of messages "Assistant: text", this is where you answer his questions and talk to the user. And the second type is "Assistant: $name``command``$". The 'name' is what the command does, the 'command' is what you execute on the user's computer you can't write questions, answers, or explanations here, you can only write what you want. At the end of each message must be '\end'. You don't have to tell the user how to do something, you have to do it yourself. Write the minimum and only what is important. If you're done, write "\end" in a new message.You know all the languages and understand and can communicate in them. If you were written in a language, continue in the language in which he wrote. \end
User: Create an image 100x100 pixels \end
Assistant: $Create image```convert -size 100x100 xc:white image.png```$ \end
Console: \end
Assistant: \end

System: New chat \end
User: Open YouTube \end
Assistant: $Open yotube.com```xdg-open https://www.youtube.com```$ \end
Console: \end
Assistant: \end

System: New chat \end
User: Create folder \end
Assistant: $Create folder named folder```mkdir folder```$ \end
Console: \end
Assistant: \end

System: New chat \end
User: What day of the week it is \end
Assistant:  $Let's know the day of the week```date +%A```$\end
Console: Tuesday\end
Assistant: Today is Tuesday. \end

System: New chat \end
User: What's the error in file 1.py \end
Assistant: $lookup code in file 1.py```cat 1.py```$ \end
Console: print(math.pi)\end
Assistant: The error is that you forgot to import the math module \end

System: New chat \end
User: Create file 1.py \end
Assistant: $Create file 1.py```touch 1.py```$ \end
Console: \end
Assistant: \end

System: New chat \end"""

class MainWindow(Gtk.ApplicationWindow):
    chat=[]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_default_size(500, 900)
        self.a=Adw.Leaflet(fold_threshold_policy=True,can_navigate_back=True,can_navigate_forward=True,transition_type=Adw.LeafletTransitionType.UNDER)
        self.set_titlebar(Gtk.Box())
        self.l=Gtk.Box(hexpand_set=True)
        self.l.set_size_request(500, -1)
        self.settings = Gtk.Settings.get_default()
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("About", "app.about")
        menu_button.set_menu_model(menu)
        self.b=Gtk.Separator()
        self.lp=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.lh=Adw.HeaderBar()
        self.lh.pack_end(menu_button)
        self.lp.append(self.lh)
        self.l.append(self.lp)
        self.l.append(self.b)
        self.r=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True)
        self.rh=Adw.HeaderBar()
        self.r.append(self.rh)
        self.folder_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.r.append(self.folder_panel)
        self.set_child(self.a)
        self.a.append(self.l)
        self.a.append(self.r)
        self.mainbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.lp.append(self.mainbox)
        self.list_box = Gtk.ListBox(show_separators=True)
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_child(self.list_box)
        self.mainbox.append(scrolled_window)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.buttons = []
        for text in range(2):
            button = Gtk.Button()
            label = Gtk.Label(label=text,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,max_width_chars=0)
            button.set_child(label)
            button.connect("clicked", self.on_button_clicked)
            button.set_visible(False)
            self.box.append(button)
            self.buttons.append(button)
        self.mainbox.append(self.box)
        self.entry = Gtk.Entry()
        self.mainbox.append(self.entry)
        self.entry.connect('activate', self.on_entry_activate)
        self.a.connect("notify::folded",self.e)
        self.e()
        self.p=0
        self.update_folder()
        threading.Thread(target=self.updage_button).start()
        self.add_message("Warning")

    def chatansw(self,message):
        n=1
        while True:
            n*=2
            try:
                t=BAIChat(sync=True).sync_ask(message)
                return t
            except Exception:
                self.add_message("Error_send")
            time.sleep(n)



    def on_button_clicked(self, button):
        self.entry.set_sensitive(False)
        for btn in self.buttons:
            btn.set_visible(False)
        text = button.get_child().get_label()
        self.chat.append({"User":"User","Message":" "+text+"\end"})
        message_label = Gtk.Label(label=text,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True, css_classes=["heading"],wrap_mode=Pango.WrapMode.WORD_CHAR)
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



    def e(self,*data):
        if(self.a.get_folded()):
            self.lh.set_show_end_title_buttons(True)
            self.rh.set_show_start_title_buttons(True)
            self.b.set_visible(False)
        else:
            self.lh.set_show_end_title_buttons(False)
            self.rh.set_show_start_title_buttons(False)
            self.b.set_visible(True)
    def console(self,command):
        process = subprocess.Popen(['flatpak-spawn', '--host', *command.split(' ')], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
        for btn in self.buttons:
            if p!=self.p:
                break
            t=self.chatansw("""System: As a user, you can communicate with the Assistant, a bot powered by a neural network, to accomplish tasks on your computer and seek answers to your questions. Engage in a conversation with the Assistant to request assistance and get the help you need. \end"""+"\n"+self.return_chat(self.chat[len(self.chat)-10:len(self.chat)])+"\nUser:").text.split("\end")[0]
            if p!=self.p:
                break
            btn.get_child().set_label(t)
            btn.set_visible(True)
    def on_entry_activate(self, entry):
        text = entry.get_text()
        entry.set_text('')
        if text=="/clear":
            self.add_message("/Clear")
            self.chat=[]
            self.p+=1
            for btn in self.buttons:
                btn.set_visible(False)
            threading.Thread(target=self.updage_button).start()
            return None
        elif text[0]=="/":
            self.add_message("/Error")
            return None
        self.entry.set_sensitive(False)
        for btn in self.buttons:
            btn.set_visible(False)
        self.chat.append({"User":"User","Message":" "+text+" \end"})
        message_label = Gtk.Label(label=text,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True, css_classes=["heading"],wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.add_message("User",message_label)
        threading.Thread(target=self.send_message).start()

    def send_message(self):
        self.p+=1

        message_label = self.chatansw(start_m+"\n"+self.return_chat(self.chat[len(self.chat)-10:len(self.chat)])+"\nAssistant: ").text.split("\end")[0]
        if message_label.rfind("$")!=-1:
            command=message_label[message_label.find("$") + 1:message_label.rfind("$")]
            value=command[command.find("```") + 3:command.rfind("```")]
            self.chat.append({"User":"Command","Message":f" ```{value}```\end"})
            text_expander = Gtk.Expander(
                label=command[0:command.find("```")],css_classes=["toolbar","osd"],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10
            )
            text_expander.set_expanded(False)
            c=self.console(value)
            text_expander.set_child(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label=c[1]))
            if not c[0]:
                self.add_message("Error",text_expander)
            else:
                self.add_message("Console",text_expander)
            self.chat.append({"User":"Console","Message":" "+c[1]+"\end"})
            self.update_folder()
            return self.send_message()
        elif message_label==" "*len(message_label):
            self.chat.append({"User":"Assistant","Message":f" {message_label}\end"})
            self.entry.set_sensitive(True)
            threading.Thread(target=self.updage_button).start()
        else:
            self.chat.append({"User":"Assistant","Message":f" {message_label}\end"})
            self.add_message("Assistant",Gtk.Label(label=message_label,margin_top=10,margin_start=10,margin_bottom=10,margin_end=10, wrap=True, css_classes=["heading"],wrap_mode=Pango.WrapMode.WORD_CHAR))
            self.entry.set_sensitive(True)
            threading.Thread(target=self.updage_button).start()

    def add_message(self,user,message=None):
        b=Gtk.Box(css_classes=["card"],margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,halign=Gtk.Align.START)

        if user=="User":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["accent"]))
        if user=="Assistant":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["warning"]))
        if user=="Console":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["success"]))
        if user=="Python":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["success"]))
        if user=="Error":
            b.append(Gtk.Label(label=user+": ",margin_top=10,margin_start=10,margin_bottom=10,margin_end=0,css_classes=["error"]))
        if user=="/Clear":
            b.append(Gtk.Label(label="Chat is cleared",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["accent","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="/Error":
            b.append(Gtk.Label(label="Incorrect command",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["error","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="Error_send":
            b.append(Gtk.Label(wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,label="Failed to send bot a message",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["error","heading"]))
            b.set_halign(Gtk.Align.CENTER)
        elif user=="Warning":
            b.append(Gtk.Label(label="Attention the neural network has access to your computer, be careful, we are not responsible for the neural network.",margin_top=10,margin_start=10,margin_bottom=10,margin_end=10,css_classes=["warning","heading"],wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR))
            b.set_halign(Gtk.Align.CENTER)
        else:
            b.append(message)
        self.list_box.append(b)


class MyApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect('activate', self.on_activate)
        action = Gio.SimpleAction.new("about", None)
        action.connect('activate', self.on_about_action)
        self.add_action(action)
    def on_about_action(self, widget, _):
        about = Adw.AboutWindow(transient_for=self.props.active_window,
                                application_name='Assistant',
                                application_icon='org.gnome.Assistant',
                                developer_name='qwersyk',
                                version='0.1.0',
                                developers=['qwersyk'],
                                copyright='© 2023 qwersyk')
        about.present()

    def on_activate(self, app):
        self.win = MainWindow(application=app)
        self.win.present()

def main(version):
    app = MyApp(application_id="org.gnome.Assistant")
    app.run(sys.argv)



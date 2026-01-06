from gi.repository import Adw, Gtk

from .utility.system import open_website

class UIController:
    """Interface exposed to Extensions in order to modify the UI"""
    def __init__(self, window): 
        self.window = window

    def require_tool_update(self):
        self.window.controller.require_tool_update()

    def set_model_loading(self, status):
        self.window.set_model_loading_spinner(status)

    def get_current_message_id(self):
        return self.window.controller.msgid

    def add_tab(self, child: Gtk.Widget, focus=True) -> Adw.TabPage:
        """Add a custom Adw.TabPage

        Args:
            child (): Widget
            focus: if true, set the tab as the current one
        """
        tab = self.window.canvas_tabs.append(child)
        if focus:
            self.window.show_sidebar()
            self.window.canvas_tabs.set_selected_page(tab)
        return tab

    def new_browser_tab(self, url:str|None = None, new:bool=True) -> Adw.TabPage:
        """Add a new browser tab

        Args:
            url (): url to open
            new (bool): if false an browser tab is focused, open in that tab, otherwise create a new one
        """
        if not new:
            browser = self.window.get_current_browser_panel()
            if browser is not None:
                browser.navigate_to(url)
                return browser.get_parent()
        return self.window.add_browser_tab(url=url)

    def open_link(self, url:str|None, new:bool=False, use_integrated_browser : bool = True):
        """Open a link

        Args:
            url (): url to open
            new (bool): if false an browser tab is focused, open in that tab, otherwise create a new one
            use_integrated_browser (bool): if true, use the integrated browser, otherwise open the link in the default browser
        """
        if use_integrated_browser:
            return self.new_browser_tab(url=url, new=new)
        else:
            open_website(url)

    def new_explorer_tab(self, path:str, new:bool=True) -> Adw.TabPage:
        """Add a new explorer tab

        Args:
            path (str): path to open (full)
            new (bool): if false an explorer tab is focused, open in that tab, otherwise create a new one
        """
        if not new:
            explorer = self.window.get_current_explorer_panel()
            if explorer is not None:
                explorer.go_to_path(path)
                return explorer.get_parent()
        return self.window.add_explorer_tab(path=path)

    def new_editor_tab(self, file:str) -> Adw.TabPage:
        """Add a new editor tab

        Args:
            file (): path to open (full), None if editing some custom text
        """
        return self.window.add_editor_tab(file=file)

    def new_terminal_tab(self, command:str|None=None) -> Adw.TabPage:
        """Add a new terminal tab

        Args:
            command (): command to execute
        """
        return self.window.add_terminal_tab(command=command)

    def add_text_to_input(self, text:str, focus_input:bool=False):
        """Add text to the input

        Args:
            text (): text to add
        """
        self.window.add_text_to_input(text, focus_input)

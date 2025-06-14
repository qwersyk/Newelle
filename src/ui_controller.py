from gi.repository import Adw, Gtk

class UIController:
    """Interface exposed to Extensions in order to modify the UI"""
    def __init__(self, window): 
        self.window = window

    def add_tab(self, tab: Adw.TabPage):
        """Add a custom Adw.TabPage

        Args:
            tab (): Adw.TabPage with already the widget 
        """
        self.window.add_tab(tab)

    def new_browser_tab(self, url:str|None = None, new:bool=True):
        """Add a new browser tab

        Args:
            url (): url to open
            new (bool): if false an browser tab is focused, open in that tab, otherwise create a new one
        """
        if not new:
            browser = self.window.get_current_browser_panel()
            if browser is not None:
                browser.navigate_to(url)
                return
        self.window.add_browser_tab(url=url)
    
    def new_explorer_tab(self, path:str, new:bool=True):
        """Add a new explorer tab

        Args:
            path (str): path to open (full)
            new (bool): if false an explorer tab is focused, open in that tab, otherwise create a new one
        """
        if not new:
            explorer = self.window.get_current_explorer_panel()
            if explorer is not None:
                explorer.go_to_path(path)
                return
        self.window.add_explorer_tab(path=path)

    def new_editor_tab(self, file:str):
        """Add a new editor tab

        Args:
            file (): path to open (full), None if editing some custom text
        """
        self.window.add_editor_tab(file=file)

    def new_terminal_tab(self, command:str|None=None):
        """Add a new terminal tab

        Args:
            command (): command to execute
        """
        self.window.add_terminal_tab(command=command)

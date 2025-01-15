import os
from .g4f_handler import G4FHandler


class BingHandler(G4FHandler):
    key = "bing"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        
        self.cookies_path = os.path.join(os.path.dirname(self.path), "models", "har_and_cookies")
        if not os.path.isdir(self.cookies_path):
            os.makedirs(self.cookies_path)
        if self.is_installed(): 
            import g4f
            self.client = g4f.client.Client(provider=g4f.Provider.Bing)        
 
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "model",
                "title": _("Model"),
                "description": _("The model to use"),
                "type": "combo",
                "values": self.get_model(),
                "default": "Copilot",
            },
            {
                "key": "cookies",
                "title": _("Enable Cookies"),
                "description": _("Enable cookies to use Bing, add them in the dir in json"),
                "type": "toggle",
                "default": True,
                "folder": self.cookies_path
            }
        ] + super().get_extra_settings()

    def get_model(self):
        if self.is_installed():
            import g4f
            res = tuple()
            for model in g4f.Provider.Bing.models:
                res += ((model, model), )
            return res
        else:
            return (("Copilot", "Copilot"), )

    def load_model(self, model):
        if not self.get_setting("cookies"):
            return True
        from g4f.cookies import set_cookies_dir, read_cookie_files
        set_cookies_dir(self.cookies_path)
        read_cookie_files(self.cookies_path)
        return True

    def supports_vision(self) -> bool:
        return True


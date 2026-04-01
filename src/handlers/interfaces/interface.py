from ..handler import Handler

class Interface(Handler):
    schema_key = "interfaces-settings"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.controller = None 

    def start(self):
        pass 

    def stop(self):
        pass 

    def is_running(self):
        return False

    def set_controller(self, controller):
        self.controller = controller

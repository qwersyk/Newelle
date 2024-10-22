import sys, importlib, os

from gi.repository import Gtk


class NewelleExtension:

    def __init__(self):
        pass

    def install(self):
        pass

    def get_llm_handlers(self) -> list:
        return [] 

    def get_tts_handlers(self) -> list:
        return [] 

    def get_stt_handlers(self) -> list:
        return [] 

    def get_additional_prompts(self) -> list:
        return []

    def get_replace_codeblocks_langs(self) -> list:
        return []

    def get_gtk_widget(self, codeblock: str) -> Gtk.Widget:
        
        return Gtk.Label(label=codeblock[0])


class ExtensionLoader:
    def __init__(self, extension_dir, project_dir=None):
        self.extension_dir = extension_dir
        if project_dir is not None:
            self.project_dir = project_dir
        else:
            self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.extensions : list[NewelleExtension] = []
        self.codeblocks : dict[str, NewelleExtension] = {}

    def load_extensions(self):
        sys.path.insert(0, self.project_dir)
        for file in os.listdir(self.extension_dir):
            if file.endswith(".py"):
                spec = importlib.util.spec_from_file_location("newelle.name", os.path.join(self.extension_dir, file))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for class_name, class_obj in module.__dict__.items():
                    if isinstance(class_obj, type) and issubclass(class_obj, NewelleExtension) and class_obj != NewelleExtension:
                        extension = class_obj()
                        for lang in extension.get_replace_codeblocks_langs():
                            if lang not in self.codeblocks:
                                self.codeblocks[lang] = extension
                        self.extensions.append(extension)
        sys.path.remove(self.project_dir)

    def add_handlers(self, AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT):
        for extension in self.extensions:
            handlers = extension.get_llm_handlers()
            for handler in handlers:
                AVAILABLE_LLMS[handler["key"]] = handler
            handlers = extension.get_tts_handlers()
            for handler in handlers:
                AVAILABLE_TTS[handler["key"]] = handler
            handlers = extension.get_stt_handlers()
            for handler in handlers:
                AVAILABLE_STT[handler["key"]] = handler 

    def add_prompts(self, PROMPTS, AVAILABLE_PROMPTS):
        for extension in self.extensions:
            prompts = extension.get_additional_prompts()
            for prompt in prompts:
                if prompt not in AVAILABLE_PROMPTS:
                    AVAILABLE_PROMPTS.append(prompt)
                PROMPTS[prompt["key"]] = prompt["text"]


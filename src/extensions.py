import sys, importlib, os


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

    def get_gtk_widget(self, codeblock: str):
        return []


class ExtensionLoader:
    def __init__(self, extension_dir, project_dir):
        self.extension_dir = extension_dir
        self.project_dir = project_dir
        self.extensions : list[NewelleExtension] = []

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



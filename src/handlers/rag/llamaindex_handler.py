from .rag_handler import RAGHandler
from ...utility.pip import find_module, install_module
import os

class LlamaIndexHanlder(RAGHandler):
    def install(self):
       install_module("llama-index", os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip"))

    def is_installed(self) -> bool:
        return find_module("llama_index") is not None

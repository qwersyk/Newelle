from .memory_handler import MemoryHandler
from .memoripy_handler import MemoripyHandler
from .user_summary_handler import UserSummaryHandler
from threading import Thread

class SummaryMemoripyHanlder(MemoryHandler):
    key = "summary-memoripy"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.memoripy = None
        self.user_summary = None

    def is_installed(self) -> bool:
        memoripy, user_summary = self.initialize_handlers() 
        return memoripy.is_installed() and user_summary.is_installed()

    def install(self) -> None:
        memoripy, user_summary = self.initialize_handlers() 
        memoripy.install()
        user_summary.install()
    
    def initialize_handlers(self) -> tuple[MemoryHandler, MemoryHandler]:
        if self.memoripy is None or self.user_summary is None:
            self.memoripy = MemoripyHandler(self.settings, self.path)
            self.user_summary = UserSummaryHandler(self.settings, self.path)
            self.memoripy.set_handlers(self.llm, self.embedding)
            self.user_summary.set_handlers(self.llm, self.embedding)
            self.user_summary.set_memory_size(self.memory_size)
            self.memoripy.set_memory_size(self.memory_size)
        return self.memoripy, self.user_summary

    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        memoripy, user_summary = self.initialize_handlers() 
        r = []
        def run_memoripy():
            r.extend(memoripy.get_context(prompt, history))
        def run_user_summary():
            r.extend(user_summary.get_context(prompt, history))
        t1 = Thread(target=run_memoripy)
        t2 = Thread(target=run_user_summary)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        print(r)
        return r

    def register_response(self, bot_response: str, history: list[dict[str, str]]):
        memoripy, user_summary = self.initialize_handlers() 
        memoripy.register_response(bot_response, history)
        user_summary.register_response(bot_response, history)
        user_summary.register_response(bot_response, history)

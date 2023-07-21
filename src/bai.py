import os
from pathlib import Path
import json
import random
import string
import asyncio
import http.client
import time
import re

class BAIChatDelta:
    def __init__(self, data: dict):
        self.data = data

    @property
    def text(self) -> str:
        return self.data["text"]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def model(self) -> str:
        return self.data["detail"]["model"]

    @property
    def delta(self) -> str:
        return self.data["delta"]

    @property
    def detail(self) -> str:
        return self.data["detail"]


class BAIChatResponse:
    def __init__(self, data: list):
        self.data = data

        for i in range(len(self.data)):
            self.data[i] = BAIChatDelta(self.data[i])

    @property
    def text(self) -> str:
        return self.data[-1].text

    @property
    def id(self) -> str:
        return self.data[-1].id

    @property
    def model(self) -> str:
        return self.data[-1].model

    def __iter__(self) -> iter:
        return iter(self.data)

    def __next__(self) -> next:
        return next(self.data)


class BAIChat:
    URL = "https://chatbot.theb.ai/"
    API_URL = "https://chatbot.theb.ai/api/chat-process"
    CONFIG_FILE = Path(os.path.expanduser("~/.config/baichat/config.json"))

    def __init__(
        self,
        url: str = None,
        config_file: Path = None,
        api_url: str = None,
        loop: asyncio.AbstractEventLoop = None,
        sync: bool = False,
    ):
        self.url: str = self.URL if url is None else url
        self.api_url: str = self.API_URL if api_url is None else api_url

        if not sync:
            self.loop = asyncio.get_event_loop() if loop is None else loop
        else:
            self.loop = None

        self.config_file: Path = (
            self.CONFIG_FILE if config_file is None else config_file
        )

        self.create_config_dir_if_doesnt_exist()

        self.config: dict | bool = self.load_config()

        if self.config:
            self.chat_id: str = self.config["chat_id"]
        else:
            self.config: dict = {}
            self.chat_id: str = ""

        self.save_config()

    def create_config_dir_if_doesnt_exist(self) -> None:
        # Create a directory to store the config file if it doesn't exist
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> dict | bool:
        if self.config_file.exists():
            with open(self.config_file, "r") as f:
                return json.load(f)
        else:
            return False

    def save_config(self) -> None:
        self.config["chat_id"] = self.chat_id

        self.create_config_dir_if_doesnt_exist()

        with open(self.config_file, "w") as f:
            json.dump(self.config, f)

    def get_random_string(self, length: int = 15) -> str:
        return "".join(random.choice(string.ascii_letters) for i in range(length))

    async def get_data_async(self, session, prompt: str) -> BAIChatResponse:
        prompt = prompt.replace('"', "\n")

        if self.chat_id == "":
            self.chat_id = f"chatcmpl-{self.get_random_string()}"

        payload = json.dumps(
            {"prompt": prompt, "options": {"parentMessageId": self.chat_id}}
        )

        async with session.post(self.api_url, data=payload) as resp:
            result = await resp.text()
            result = result.splitlines()
            result = BAIChatResponse([json.loads(line) for line in result])

            self.chat_id = result.id
            return result

    async def async_ask(self, prompt: str) -> BAIChatResponse:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.5",
            "Host": "chatbot.theb.ai",
            "Origin": "https://chatbot.theb.ai",
            "Referer": "https://chatbot.theb.ai",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/112.0",
            "Content-Type": "application/json",
        }
        conn = http.client.HTTPSConnection('chatbot.theb.ai')
        prompt = prompt.replace('"', "\n")

        if self.chat_id == "":
            self.chat_id = f"chatcmpl-{self.get_random_string()}"

        payload = json.dumps(
            {"prompt": prompt, "options": {"parentMessageId": self.chat_id}}
        )
        conn.request('POST', '/api/chat-process', payload,  headers=headers)

        response = conn.getresponse()
        result = response.read().decode('utf-8')
        result = result.splitlines()
        result = BAIChatResponse([json.loads(line) for line in result])

        self.chat_id = result.id
        return result
    def sync_ask(self, prompt: str) -> BAIChatResponse:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.5",
            "Host": "chatbot.theb.ai",
            "Origin": "https://chatbot.theb.ai",
            "Referer": "https://chatbot.theb.ai",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/112.0",
            "Content-Type": "application/json",
        }
        conn = http.client.HTTPSConnection('chatbot.theb.ai')
        prompt = prompt.replace('"', "\n")

        if self.chat_id == "":
            self.chat_id = f"chatcmpl-{self.get_random_string()}"

        payload = json.dumps(
            {"prompt": prompt, "options": {"parentMessageId": self.chat_id}}
        )
        conn.request('POST', '/api/chat-process', payload,  headers=headers)

        response = conn.getresponse()
        result = response.read().decode('utf-8')
        result = result.splitlines()
        result = BAIChatResponse([json.loads(line) for line in result])

        self.chat_id = result.id
        return result

    def ask(self, prompt: str) -> BAIChatResponse:
        return self.loop.run_until_complete(self.async_ask(prompt))

    def __enter__(self):
        return (self.loop, self)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.save_config()

class BaiHandler():
    def __init__(self, settings, path):
        pass

    def load_model(self, model):
        return True
    def send_message(self, window, message):
            stream_number_variable = window.stream_number_variable
            loop_interval_variable = 1
            while stream_number_variable == window.stream_number_variable:
                loop_interval_variable *= 2
                loop_interval_variable = min(60,loop_interval_variable)
                try:
                    t = re.split(r'Assistant:|Console:|User:|File:|Folder:', BAIChat(sync=True).sync_ask(message).text,1)[0]
                    return t
                except Exception as e:
                    # self.notification_block.add_toast(Adw.Toast(title=_('Failed to send bot a message'), timeout=2))
                    pass
                time.sleep(loop_interval_variable)
            return _("Chat has been stopped")

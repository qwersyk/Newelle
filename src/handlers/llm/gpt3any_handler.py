from typing import Callable, Any

from .g4f_handler import G4FHandler

class GPT3AnyHandler(G4FHandler):
    """
    Use any GPT3.5-Turbo providers
    - History is supported by almost all of them
    - System prompts are not well supported, so the prompt is put on top of the message
    """
    key = "GPT3Any"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.client = None 
        if self.is_installed():
            self.init_client()
    
    def init_client(self):
        import g4f 
        from g4f.Provider import RetryProvider
        good_providers = [g4f.Provider.DDG, g4f.Provider.Pizzagpt, g4f.Provider.DarkAI, g4f.Provider.Koala, g4f.Provider.AmigoChat]
        good_nongpt_providers = [g4f.Provider.ReplicateHome,g4f.Provider.RubiksAI, g4f.Provider.TeachAnything, g4f.Provider.Free2GPT, g4f.Provider.DeepInfraChat, g4f.Provider.PerplexityLabs]
        acceptable_providers = [g4f.Provider.Blackbox, g4f.Provider.Upstage, g4f.Provider.Upstage]
        self.client = g4f.client.Client(provider=RetryProvider([RetryProvider(good_providers), RetryProvider(good_nongpt_providers), RetryProvider(acceptable_providers)], shuffle=False))
        self.n = 0
    
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        if self.client is None:
            self.init_client()
        message = prompt
        history = self.convert_history(history, system_prompt)
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        response = self.client.chat.completions.create(
            model="",
            messages=history,
        )
        return response.choices[0].message.content

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        if self.client is None:
            self.init_client()
        history = self.convert_history(history, system_prompt)
        message = prompt
        user_prompt = {"role": "user", "content": message}
        history.append(user_prompt)
        response = self.client.chat.completions.create(
            model="",
            messages=history,
            stream=True,
        )
        full_message = ""
        prev_message = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                full_message += chunk.choices[0].delta.content
                args = (full_message.strip(), ) + tuple(extra_args)
                if len(full_message) - len(prev_message) > 1:
                    on_update(*args)
                    prev_message = full_message
        return full_message.strip()

    def generate_chat_name(self, request_prompt: str = "") -> str:
        history = ""
        for message in self.history[-4:] if len(self.history) >= 4 else self.history:
            history += message["User"] + ": " + message["Message"] + "\n"
        name = self.generate_text(history + "\n\n" + request_prompt)
        return name

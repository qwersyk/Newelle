from ...handlers.llm import LLMHandler
from ...handlers.embeddings import EmbeddingHandler
from .memory_handler import MemoryHandler
from ...handlers import ExtraSettings

class UserSummaryHandler(MemoryHandler):
    key = "user-summary"
  
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.seen_messages = self.get_setting("seen_messages", False)
        if self.seen_messages is None:
            self.seen_messages = []

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ButtonSetting("reset_memory", "Reset Memory", "Reset the memory", lambda x: self.reset_memory(), "Reset Memory"),
            ExtraSettings.ScaleSetting("update_freq", "Update Summary Frequency", "How often to update the summary", 5, 1, 10, 0), 
            ExtraSettings.MultilineEntrySetting("user_summary", "User Summary", "Current summary of the interactions with the assistant", "")
        ]
    
    def reset_memory(self):
        self.set_setting("user_summary", "")
        self.settings_update()

    def get_context(self, prompt: str, history: list[dict[str, str]]) -> list[str]:
        self.seen_messages.append(prompt)
        PROMPT = """
            For the duration of this conversation, please keep the following long-term memory summary in mind. This summary includes important details about the user's preferences, interests, and previous interactions. Use this context to ensure your responses are consistent and personalized.
            User Long-Term Memory Summary:
            {prompt}
            Continue with the conversation while considering the above context.
            """
        return ["---"+PROMPT.format(prompt=self.get_setting("user_summary"))]

    def register_response(self, bot_response, history):
        self.seen_messages.append(bot_response)
        update_frequency = min(int(self.get_setting("update_freq")), self.memory_size)
        PROMPT = """
You are tasked with updating the user's long-term memory summary based on the latest chat history. The goal is to capture everything useful about the user that will improve future interactions. Retain all relevant details from the existing summary and incorporate new information from the provided chat history. Be sure to include the user's preferences, interests, recurring topics, and any personal context that could help tailor responses in the future.

Chat History:
{history}

Existing Summary:
{summary}

Please generate an updated long-term memory summary that is clear, concise, and organized.
Only output the summary with no other details.
        """
        if len(self.seen_messages) % update_frequency == 0:
            prompt = PROMPT.format(history="\n".join([i["User"] + ": " + i["Message"] for i in history[-update_frequency:]]), summary=self.get_setting("user_summary"))
            upd = self.llm.generate_text(prompt)
            self.set_setting("user_summary", upd)
        self.set_setting("seen_messages", self.seen_messages)

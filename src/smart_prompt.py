from wordllama import WordLlama
from typing import Any
from abc import abstractmethod
import json, copy


class SmartPromptHandler():
    key = ""
    
    def __init__(self, settings, path: str):
        self.settings = settings
        self.path = path

    def is_installed(self) -> bool:
        return True

    def install(self):
        pass

    @staticmethod
    def requires_sandbox_escape() -> bool:
        return False

    def get_extra_requirements(self) -> list:
        return []

    def get_extra_settings(self) -> list:
        return []

    def set_setting(self, setting, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string("smart-prompt-settings"))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][setting] = value
        self.settings.set_string("translator-settings", json.dumps(j))

    def get_setting(self, name) -> Any:
        """Get setting from key"""
        j = json.loads(self.settings.get_string("smart-prompt-settings"))
        if self.key not in j or not isinstance(j[self.key], dict) or name not in j[self.key]:
            return self.get_default_setting(name)
        return j[self.key][name]

    def get_default_setting(self, name):
        """Get the default setting from a key"""
        for x in self.get_extra_settings():
            if x["key"] == name:
                return x["default"]
        return None

    @abstractmethod

    def get_extra_prompts(self, message: str, history : list[dict[str, str]], available_prompts : list[dict]) -> list[str]:
        return []

class WordLlamaHandler(SmartPromptHandler):
    key = "WordLlama"

    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.wl = WordLlama.load()

    def get_extra_prompts(self, message: str, history : list[dict[str, str]], available_prompts : list[dict]) -> list[str]:
        categories_db = {}
        for prompt in available_prompts:
            categories_db[prompt["key"]] = prompt["prompts"]
              
        scores = self.recognize_category(message, categories_db)
        print(scores)
        best_score = max(scores.values())
        prompts = []
        chat_tags = []
        for prompt in available_prompts:
            if prompt["key"] in scores and scores[prompt["key"]] > 0.3 and best_score - scores[prompt["key"]] < 0.1 and not prompt["key"] in chat_tags:
                chat_tags.append(prompt["key"])
            if prompt["key"] in chat_tags:
                prompts.append(prompt["prompt_text"]) 
        
        for msg in history:
            scores = self.recognize_category(msg["Message"], categories_db)
            for prompt in available_prompts:
                if prompt["key"] in scores and scores[prompt["key"]] > 0.3 and not prompt["key"] in chat_tags:
                    chat_tags.append(prompt["key"])
        
        for prompt in available_prompts:
            if prompt["key"] in chat_tags:
                prompts.append(prompt["prompt_text"])
        print(chat_tags)
        return prompts
   
    def recognize_category(self, sentence, categories_db, top_k=3):
        category_scores = {}

        for category, examples in categories_db.items():
            # Rank the example sentences based on their similarity to the input sentence
            ranked_examples = self.wl.rank(sentence, examples)

            # Sort the examples by similarity score (already sorted from .rank output)
            # Take the top K highest-scoring examples
            top_ranked = ranked_examples[:min(len(ranked_examples), top_k)]

            # Compute the average similarity score for the top K examples
            avg_score = sum(score for _, score in top_ranked) / len(top_ranked)
            category_scores[category] = avg_score

        return category_scores
 

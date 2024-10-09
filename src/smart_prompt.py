from wordllama import WordLlama
from typing import Any
from abc import abstractmethod
import json, copy, os, pickle

from src.extra import find_module, install_module
from .handler import Handler

class SmartPromptHandler(Handler):
    key = ""
    schema_key = "smart-prompt-settings"   

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


class LogicalRegressionHandler(SmartPromptHandler):
    key = "LogicalRegression"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.wl = WordLlama.load()
        self.models_dir = os.path.join(path, "prompt-models")
        self.pip_path = os.path.join(path, "pip")
        if not os.path.isdir(self.models_dir):
            os.makedirs(self.models_dir)
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["scikit_learn"]
    
    def install(self):
        install_module("scikit_learn", self.pip_path)
        
    
    def is_installed(self) -> bool:
        if not find_module("sklearn"):
            return False
        if not os.path.isfile(os.path.join(self.models_dir, "linear_regression.sav")):
            return False
        return True
    
    def get_extra_prompts(self, message: str, history : list[dict[str, str]], available_prompts : list[dict]) -> list[str]:
        pass

from subprocess import check_output
from wordllama import WordLlama
from typing import Any
from abc import abstractmethod
import json, copy, os, pickle

from .extra import find_module, install_module
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
    version = "0.1"
    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
        self.wl = WordLlama.load()
        self.models_dir = os.path.join(path, "prompt-models")
        self.pip_path = os.path.join(path, "pip")
        if not os.path.isdir(self.models_dir):
            os.makedirs(self.models_dir)
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["sklearn"]
    
    def install(self):
        install_module("scikit_learn", self.pip_path)
        check_output(["wget", "-P", self.models_dir, f"http://mirror.nyarchlinux.moe/lrmodelv{self.version}.pkl"]) 
   
    def load(self):
        if self.model is not None:
            return
        with open(os.path.join(self.models_dir, f"lrmodelv{self.version}.pkl"), "rb") as f:
            self.model = pickle.load(f)

    def is_installed(self) -> bool:
        if not find_module("sklearn"):
            return False
        if not os.path.isfile(os.path.join(self.models_dir, f"lrmodelv{self.version}.pkl")):
            return False
        return True
    
    def get_extra_prompts(self, message: str, history : list[dict[str, str]], available_prompts : list[dict]) -> list[str]:
        self.load()
        if self.model is None:
            return []
        # Embed the message
        messages = [msg["Message"] for msg in history if msg["User"] == "User"]
        messages.append(message)
        embeddings = self.wl.embed(messages)
        probabilities = self.model.predict_proba(embeddings)

        # Stampa le probabilità per ogni categoria
        labels = [prompt["key"] for prompt in available_prompts]
        labels.sort()
        chat_tags = []
        for i, text in enumerate(embeddings):
            for j, category in enumerate(labels):
                m = max(probabilities[i])
                if (probabilities[i][j] > 0.5 or (probabilities[i][j] > 0.3 and probabilities[i][j] == m) ) and category not in chat_tags:
                    chat_tags.append(category)
        print(chat_tags)
        return [prompt["prompt_text"] for prompt in available_prompts if prompt["key"] in chat_tags]

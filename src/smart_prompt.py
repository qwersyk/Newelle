from subprocess import Popen, check_output
import subprocess
from wordllama import WordLlama
from typing import Any
from abc import abstractmethod
import json, copy, os, pickle, shutil

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
        self.wl = None
    
    def load(self):
        if self.wl is None:
            self.wl = WordLlama.load()

    def get_extra_prompts(self, message: str, history : list[dict[str, str]], available_prompts : list[dict]) -> list[str]:
        self.load()
        if self.wl is None:
            return []
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
   
    def recognize_category(self, sentence, categories_db, top_k=10):
        category_scores = {}

        self.load()
        if self.wl is None:
            return [] 
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
    version = "0.3"
    dimensions = {256: {"url": "https://github.com/NyarchLinux/Smart-Prompts/releases/download/0.3/NyaMedium_0.3_256.pkl"}, 
                  512 : {"url": "https://github.com/NyarchLinux/Smart-Prompts/releases/download/0.3/NyaMedium_0.3_512.pkl"}, 
                  1024: {"url": "https://github.com/NyarchLinux/Smart-Prompts/releases/download/0.3/NyaMedium_0.3_1024.pkl"}}

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "dimension",
                "title": _("Model Dimension"),
                "description": _("Use bigger models for bigger accuracy, models bigger than 256 will donwnload on first message sent, < 100MB"),
                "type": "combo",
                "default": 256,
                "values": (("NyaMedium_0.3_256","256"), ("NyaMedium_0.3_512","512"), ("NyaMedium_0.3_1024", "1024"),)
            }
        ]

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.model = None
        self.wl = None
        self.models_dir = os.path.join(path, "prompt-models")
        self.pip_path = os.path.join(path, "pip")
        self.dimension = int(self.get_setting("dimension"))
        self.model_path = os.path.join(self.models_dir, f"NyaMedium_{self.version}_{self.dimension}.pkl")
        if not os.path.isdir(self.models_dir):
            os.makedirs(self.models_dir)
        self.check_files()

    def check_files(self):
        default_model = f"NyaMedium_{self.version}_{256}.pkl"
        if not os.path.isfile(os.path.join(self.models_dir, default_model)):
            shutil.copy(os.path.join("/app/data/smart-prompts", default_model), os.path.join(self.models_dir, default_model))
        if not os.path.isfile(os.path.expanduser("~/.cache/wordllama/tokenizers/l2_supercat_tokenizer_config.json")):
            shutil.copy(os.path.join("/app/data/smart-prompts", "l2_supercat_tokenizer_config.json"), os.path.expanduser("~/.cache/wordllama/tokenizers/l2_supercat_tokenizer_config.json"))

    @staticmethod
    def get_extra_requirements() -> list:
        return ["sklearn"]
    
    def install(self):
        if find_module("sklearn") is None:
            install_module("scikit_learn", self.pip_path)
        self.load() 
    
    def load(self):
        if self.wl is None:
            self.wl = WordLlama.load(dim=self.dimension)
        if not os.path.isfile(self.model_path):
            print("Downloading model from " + self.dimensions[self.dimension]["url"])
            subprocess.run(["wget", "-P", self.models_dir, self.dimensions[self.dimension]["url"]], capture_output=False)
            print("Model downloaded")
        if self.model is None:
            with open(self.model_path, "rb") as f:
                self.model = pickle.load(f)

    def is_installed(self) -> bool:
        if not find_module("sklearn"):
            return False
        if not os.path.isfile(self.model_path):
            return False
        return True
    
    def get_extra_prompts(self, message: str, history : list[dict[str, str]], available_prompts : list[dict]) -> list[str]:
        self.load()
        if self.model is None or self.wl is None:
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


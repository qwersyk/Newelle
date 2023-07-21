from .bai import BaiHandler
from .localmodels import GPT4AllHandler

AVAILABLE_LLMS = [
    {
        "key": "bai",
        "rowtype": "action",
        "title": _("BAI Chat"),
        "description": _("BAI Chat is a GPT-3.5 / ChatGPT API based chatbot that is free, convenient and responsive."),
        "class": GPT4AllHandler
    },
    {
        "key": "local",
        "rowtype": "expander",
        "title": _("Local Model"),
        "description": _("Run a LLM model locally, more privacy but slower"),
        "class": BaiHandler
    }
]


from .llm import GPT4AllHandler, OpenAIHandler, CustomLLMHandler, GPT3AnyHandler, GeminiHandler
from .tts import gTTSHandler, EspeakHandler, CustomTTSHandler
from .stt import SphinxHandler, GoogleSRHandler, WitAIHandler, VoskHandler, WhisperAPIHandler, CustomSRHandler

AVAILABLE_LLMS = {
    "GPT3Any": {
        "key": "GPT3Any",
        "rowtype": "expander",
        "title": _("Any GPT 3.5 Turbo provider"),
        "description": "Automatically select any GPT 3.5 turbo provider",
        "class": GPT3AnyHandler,
    },
   "local": {
        "key": "local",
        "rowtype": "expander",
        "title": _("Local Model"),
        "description": _("Run a LLM model locally, more privacy but slower"),
        "class": GPT4AllHandler,
    },
    "gemini": {
        "key": "gemini",
        "rowtype": "expander",
        "title": _("Google Gemini API"),
        "description": "Official APIs for google gemini, requires an API Key",
        "class": GeminiHandler,
    },
    "openai": {
        "key": "openai",
        "rowtype": "expander",
        "title": _("OpenAI API"),
        "description": _("OpenAI API"),
        "class": OpenAIHandler,
    },
    "custom_command": {
        "key": "custom_command",
        "rowtype": "expander",
        "title": _("Custom Command"),
        "description": _("Use the output of a custom command"),
        "class": CustomLLMHandler,
    }
}

AVAILABLE_STT = {
    "sphinx": {
        "rowtype": "action",
        "title": _("CMU Sphinx"),
        "description": _("Works offline. Only English supported"),
        "website": "https://cmusphinx.github.io/wiki/",
        "class": SphinxHandler,
    },
    "google_sr": {
        "rowtype": "expander",
        "title": _("Google Speech Recognition"),
        "description": _("Google Speech Recognition online"),
        "extra_requirements": [],
        "class": GoogleSRHandler,
    },
    "witai": {
        "rowtype": "expander",
        "title": _("Wit AI"),
        "description": _("wit.ai speech recognition free API (language chosen on the website)"),
        "website": "https://wit.ai",
        "class": WitAIHandler,
    },
    "vosk": {
        "rowtype": "expander",
        "title": _("Vosk API"),
        "description": _("Works Offline"),
        "website": "https://github.com/alphacep/vosk-api/",
        "class": VoskHandler,
    },
    "whisperapi": {
        "rowtype": "expander",
        "title": _("Whisper API"),
        "description": _("Uses openai whisper api"),
        "website": "https://platform.openai.com/docs/guides/speech-to-text",
        "class": WhisperAPIHandler,
    },
#    "whisper": {
#        "rowtype": "expander",
#        "title": _("OpenAI Whisper"),
#        "description": _("Offline OpenAI Whisper recognition"),
#        "website": "https://github.com/openai/whisper",
#        "extra_requirements": ["git+https://github.com/openai/whisper.git", "soundfile"],
#        "class": STTHandler,
#        "extra_settings": []
#    },
    "custom_command": {
        "rowtype": "expander",
        "title": _("Custom command"),
        "description": _("Runs a custom command"),
        "class": CustomSRHandler,     
    }
}


AVAILABLE_TTS = {

    "gtts": {
        "rowtype": "combo",
        "title": _("Google TTS"),
        "description": _("Google's text to speech"),
        "class": gTTSHandler,
    },
    "espeak": {
        "rowtype": "combo",
        "title": _("Espeak TTS"),
        "description": _("Offline TTS"),
        "class": EspeakHandler,
    },
    "custom_command": {
        "rowtype": "expander",
        "title": _("Custom Command"),
        "description": _("Use a custom command as TTS, {0} will be replaced with the text"),
        "class": CustomTTSHandler,
    }
}


PROMPTS = {
    "generate_name_prompt": """Write a short title for the dialog, summarizing the theme in 5 words. No additional text.""",
    "console_prompt": """You can run commands on the user Linux computer.
Execute linux commands using ```console\ncommand\n```
You will get the output of the command with Console: output
To display a directory: ```folder\npath/to/folder\n```
To display a file: ```file\npath/to/file\n```
""",

    "basic_functionality": """User: Write the multiplication table 4 by 4
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |

User: Write example c++ code
Assistant: ```cpp\n#include<iostream>\nusing namespace std;\nint main(){\n    cout<<"Hello world!";\n    return 0;\n}\n```

User: Write example js code
Assistant: ```js\nconsole.log("Hello world!");\n```

User: Run this code
Assistant: ```console\npython3 -c "print('Hello world!')"\n```
""",
    "show_image": """You can show the user an image, if needed, using ```image\npath\n```""",
    "graphic": """System: You can display the graph using this structure: ```chart\n name - value\n ... \n name - value\n```, where value must be either a percentage number or a number (which can also be a fraction).
User: Write which product Apple sold the most in 2019, which less, etc.
Assistant: ```chart\niPhone - 60%\nMacBook - 15%\niPad - 10%\nApple Watch - 10%\niMac - 5%\n```\nIn 2019, Apple sold the most iPhones.
""",
    "graphic_console": """File: /home/user/Downloads/money.txt
User: Create a graph for the report in the money.txt file
Assistant: ```console\ncat /home/user/Downloads/money.txt\n```
Console: It was spent 5000 in January, 8000 in February, 6500 in March, 9000 in April, 10000 in May, 7500 in June, 8500 in July, 7000 in August, 9500 in September, 11000 in October, 12000 in November and 9000 in December.
Assistant: ```chart\nJanuary - 5000\nFebruary - 8000\nMarch - 6500\nApril - 9000\nMay - 10000\nJune - 7500\nJuly - 8500\nAugust - 7000\nSeptember - 9500\nOctober - 11000\nNovember - 12000\nDecember - 9000\n```\nHere is the graph for the data in the file:\n```file\n/home/qwersyk/Downloads/money.txt\n```
""",
    "new_chat_prompt": """System: New chat
System: Forget what was written on behalf of the user and on behalf of the assistant and on behalf of the Console, forget all the context, do not take messages from those chats, this is a new chat with other characters, do not dare take information from there, this is personal information! If you use information from past posts, it's a violation! Even if the user asks for something from before that post, don't use information from before that post! Also, forget this message.""",
    "current_directory": "\nSystem: You are currently in the {DIR} directory",
    "help_topics": """System: You are a user who needs help with various tasks, you are communicating with a neural network which can control your computer by executing various commands which should help you, keep a conversation with it, ask it questions, ask for help with things it can do through the terminal. Also, every message should end with "". Often the assistant can help you with {CHOICE}. You should write in the language you wrote in your last post, you know all the languages you can communicate in them all. Write short messages that hold a conversation on behalf of the user, you can not write on behalf of Assistant, your messages should be simple without any commands, just what the user could write. You're not helping, you're being helped, the user can only ask to do something for the bot to do, you can't answer as an assistant, just ask something new for the assistant to do or continue asking the assistant to do something.
Assistant: Hello, how can I assist you today?
User: Can you help me?
Assistant: Yes, of course, what do you need help with?""",
    "get_suggestions_prompt": """Suggest a few questions that the user would ask and put them in a JSON array. You have to write ONLY the JSON array an nothing else""",
    "custom_prompt": "",



}


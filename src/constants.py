
from .llm import GPT4AllHandler, BaiHandler, OpenAIHandler, CustomLLMHandler, DeepAIHandler
from .tts import gTTSHandler, EspeakHandler, CustomTTSHandler
from .stt import STTHandler, SphinxHandler, GoogleSRHandler, WitAIHandler, VoskHandler, WhisperAPIHandler, CustomSRHandler

AVAILABLE_LLMS = {
    "bai": {
        "key": "bai",
        "rowtype": "action",
        "title": _("BAI Chat"),
        "description": _("BAI Chat is a GPT-3.5 / ChatGPT API based chatbot that is free, convenient and responsive."),
        "class": BaiHandler,
        "extra_settings": [],
        "extra_requirements": []
    },
    "local": {
        "key": "local",
        "rowtype": "expander",
        "title": _("Local Model"),
        "description": _("Run a LLM model locally, more privacy but slower"),
        "class": GPT4AllHandler,
        "extra_settings": [],
        "extra_requirements": []
    },
    "deepai": {
        "key": "deepai",
        "rowtype": "expander",
        "title": _("Deep AI"),
        "description": "AI",
        "class": DeepAIHandler,
        "extra_settings": [
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
        ],
        "extra_requirements": []
    },
    "openai": {
        "key": "openai",
        "rowtype": "expander",
        "title": _("OpenAI API"),
        "description": _("OpenAI API"),
        "class": OpenAIHandler,
        "extra_requirements": ["openai"],
        "extra_settings": [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for OpenAI"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "engine",
                "title": _("OpenAI Engine"),
                "description": _("Name of the OpenAI Engine"),
                "type": "entry",
                "default": "text-davinci-003"
            },
            {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            },
            {
                "key": "max-tokens",
                "title": _("Max Tokens"),
                "description": _("Max tokens of the generated text"),
                "website": "https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them",
                "type": "range",
                "min": 3,
                "max": 400,
                "default": 150,
                "round-digits": 0
            },
            {
                "key": "top-p",
                "title": _("Top-P"),
                "description": _("An alternative to sampling with temperature, called nucleus sampling"),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-top_p",
                "type": "range",
                "min": 0,
                "max": 1,
                "default": 1,
                "round-digits": 2,
            },
            {
                "key": "temperature",
                "title": _("Temperature"),
                "description": _("What sampling temperature to use. Higher values will make the output more random"),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-temperature",
                "type": "range",
                "min": 0,
                "max": 2,
                "default": 1,
                "round-digits": 2,
            },
            {
                "key": "frequency-penalty",
                "title": _("Frequency Penalty"),
                "description": _("Positive values penalize new tokens based on their existing frequency in the text so far, decreasing the model's likelihood to repeat the same line"),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-frequency_penalty",
                "type": "range",
                "min": -2,
                "max": 2,
                "default": 0,
                "round-digits": 1,
            },
            {
                "key": "presence-penalty",
                "title": _("Presence Penalty"),
                "description": _("PPositive values penalize new tokens based on whether they appear in the text so far, increasing the model's likelihood to talk about new topics."),
                "website": "https://platform.openai.com/docs/api-reference/completions/create#completions/create-frequency_penalty",
                "type": "range",
                "min": -2,
                "max": 2,
                "default": 0,
                "round-digits": 1,
            },
        ]
    },
    "custom_command": {
        "key": "custom_command",
        "rowtype": "expander",
        "title": _("Custom Command"),
        "description": _("Use the output of a custom command"),
        "class": CustomLLMHandler,
        "extra_requirements": [],
        "extra_settings": [
            {
                "key": "command",
                "title": _("Command to execute to get bot output"),
                "description": _("Command to execute to get bot response, {0} will be replaced with a JSON file containing the chat, {1} with the extra prompts"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "suggestion",
                "title": _("Command to execute to get bot's suggestions"),
                "description": _("Command to execute to get chat suggestions, {0} will be replaced with a JSON file containing the chat, {1} with the extra prompts"),
                "type": "entry",
                "default": ""
            },

        ]
    }
}

AVAILABLE_STT = {
    "sphinx": {
        "rowtype": "action",
        "title": _("CMU Sphinx"),
        "description": _("Works offline. Only English supported"),
        "website": "https://cmusphinx.github.io/wiki/",
        "extra_requirements": ["pocketsphinx"],
        "class": SphinxHandler,
        "extra_settings": []
    },
    "google_sr": {
        "rowtype": "expander",
        "title": _("Google Speech Recognition"),
        "description": _("Google Speech Recognition online"),
        "extra_requirements": [],
        "class": GoogleSRHandler,
        "extra_settings": [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for Google SR, write 'default' to use the default one"),
                "type": "entry",
                "default": "default"
            },
            {
                "key": "language",
                "title": _("Language"),
                "description": _("The language of the text to recgnize in IETF"),
                "type": "entry",
                "default": "en-US",
                "website": "https://stackoverflow.com/questions/14257598/what-are-language-codes-in-chromes-implementation-of-the-html5-speech-recogniti"
            }
        ]
    },
    "witai": {
        "rowtype": "expander",
        "title": _("Wit AI"),
        "description": _("wit.ai speech recognition free API (language chosen on the website)"),
        "extra_requirements": [],
        "website": "https://wit.ai",
        "class": WitAIHandler,
        "extra_settings": [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("Server Access Token for wit.ai"),
                "type": "entry",
                "default": ""
            },
        ]
    },
    "vosk": {
        "rowtype": "expander",
        "title": _("Vosk API"),
        "description": _("Works Offline"),
        "website": "https://github.com/alphacep/vosk-api/",
        "extra_requirements": ["vosk"],
        "class": VoskHandler,
        "extra_settings": [
            {
                "key": "path",
                "title": _("Model Path"),
                "description": _("Absolute path to the VOSK model (unzipped)"),
                "type": "entry",
                "website": "https://alphacephei.com/vosk/models",
                "default": ""
            },
        ]
    },
    "whisperapi": {
        "rowtype": "expander",
        "title": _("Whisper API"),
        "description": _("Uses openai whisper api"),
        "website": "https://platform.openai.com/docs/guides/speech-to-text",
        "extra_requirements": ["openai"],
        "class": WhisperAPIHandler,
        "extra_settings": [
            {
                "key": "api",
                "title": _("API Key"),
                "description": _("API Key for OpenAI"),
                "type": "entry",
                "default": ""
            },
            {
                "key": "model",
                "title": _("Whisper API Model"),
                "description": _("Name of the Whisper API Model"),
                "type": "entry",
                "default": "whisper-1"
            },
        ]
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
        "extra_requirements": [],
        "class": CustomSRHandler,
        "extra_settings": [
            {
                "key": "command",
                "title": _("Command to execute"),
                "description": _("{0} will be replaced with the model fullpath"),
                "type": "entry",
                "default": ""
            },
        ]
    }
}


AVAILABLE_TTS = {

    "gtts": {
        "rowtype": "combo",
        "title": _("Google TTS"),
        "description": _("Google's text to speech"),
        "class": gTTSHandler,
        "extra_settings": []
    },
    "espeak": {
        "rowtype": "combo",
        "title": _("Espeak TTS"),
        "description": _("Offline TTS"),
        "class": EspeakHandler,
        "extra_settings": []
    },
    "custom_command": {
        "rowtype": "expander",
        "title": _("Custom Command"),
        "description": _("Use a custom command as TTS, {0} will be replaced with the text"),
        "class": CustomTTSHandler,
        "extra_settings": [
            {
                "key": "command",
                "title": _("Command to execute"),
                "description": _("{0} will be replaced with the model fullpath"),
                "type": "entry",
                "default": ""
            },
        ]
    }
}


PROMPTS = {
    "console_prompt": """System: You are an assistant who helps the user by answering questions and running Linux commands in the terminal on the user's computer. Use two types of messages: "Assistant: text" to answer questions and communicate with the user, and "Assistant: ```console\ncommand\n```" to execute commands on the user's computer. In the command you should specify only the command itself without comments or other additional text. Your task is to minimize the information and leave only the important. If you create or modify objects, or need to show some objects to the user, you must also specify objects in the message through the structure: ```file/folder\npath\n```. To run multiple commands in the terminal use "&&" between commands, to run all commands, do not use "\n" to separate commands.
User: Create an image 100x100 pixels
Assistant: ```console
convert -size 100x100 xc:white image.png
```
Console: Done
Assistant: The image has been created:
```image
./image.png
```

User: Open YouTube
Assistant: ```console
xdg-open https://www.youtube.com
```
Console: Done
Assistant:

User: Create folder
Assistant: ```console
mkdir folder
```
Console: Done
Assistant: The folder has been created:
```folder
./folder
```

User: What day of the week it is
Assistant: ```console
date +%A
```
Console: Tuesday
Assistant: Today is Tuesday.

User: What's the error in file 1.py
Assistant: ```console
cat 1.py
```
Console: print(math.pi)
Assistant: The error is that you forgot to import the math module

User: Create a folder and create a git project inside it.
Assistant: ```console\nmkdir folder && cd folder && git init\n```

""",

    "basic_functionality": """User: Write the multiplication table 4 by 4
Assistant: | - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |

User: Write example c++ code
Assistant: ```cpp\n#include<iostream>\nusing namespace std;\nint main(){\n    cout<<"Hello world!";\n    return 0;\n}\n```

User: Write example js code
Assistant: ```js\nconsole.log("Hello world!");\n```

User: Write example python code
Assistant: ```python\npython("Hello world!")\n```
User: Run this code
Assistant: ```console\npython3 -c "print('Hello world!')"\n```
""",
    "show_image": """System: You can also show the user an image, if needed, through a syntax like '```image\npath\n```'
""",
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
Assistant: Yes, of course, what do you need help with?"""



}

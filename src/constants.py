from .bai import BaiHandler
from .localmodels import GPT4AllHandler
from .tts import gTTSHandler, EspeakHandler

AVAILABLE_LLMS = [
    {
        "key": "bai",
        "rowtype": "action",
        "title": _("BAI Chat"),
        "description": _("BAI Chat is a GPT-3.5 / ChatGPT API based chatbot that is free, convenient and responsive."),
        "class": BaiHandler
    },
    {
        "key": "local",
        "rowtype": "expander",
        "title": _("Local Model"),
        "description": _("Run a LLM model locally, more privacy but slower"),
        "class": GPT4AllHandler
    }
]

AVAILABLE_TTS = {

    "gtts": {
        "rowtype": "combo",
        "title": _("Google TTS"),
        "description": _("Google's text to speech"),
        "class": gTTSHandler
    },
    "espeak": {
        "rowtype": "combo",
        "title": _("Espeak TTS"),
        "description": _("Offline TTS"),
        "class": EspeakHandler
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

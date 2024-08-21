from __future__ import absolute_import
import importlib, subprocess
import re
import os
import xml.dom.minidom
import importlib, subprocess, functools


def rgb_to_hex(r, g, b):
    """
    Convert RGB values from float to hex.

    Args:
        r (float): Red value between 0 and 1.
        g (float): Green value between 0 and 1.
        b (float): Blue value between 0 and 1.

    Returns:
        str: Hex representation of the RGB values.
    """
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))

def human_readable_size(size: float, decimal_places:int =2) -> str:
    size = int(size)
    unit = ''
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

def extract_expressions(text, expressions_list):
    expressions = []
    current_expression = None
    current_text = ""

    tokens = text.split()
    i = 0
    while i < len(tokens):
        if tokens[i].startswith("(") and tokens[i].endswith(")"):
            expression = tokens[i][1:-1]
            if expression in expressions_list:
                if current_text.strip():
                    expressions.append({"expression": current_expression, "text": current_text.strip()})
                    current_text = ""
                current_expression = expression
            else:
                current_text += tokens[i] + " "
        else:
            if current_expression is None:
                current_text += tokens[i] + " "
            else:
                current_text += tokens[i] + " "
        i += 1

    if current_text.strip():
        if current_expression:
            expressions.append({"expression": current_expression, "text": current_text.strip()})
        else:
            expressions.append({"expression": None, "text": current_text.strip()})

    return expressions

class ReplaceHelper:
    DISTRO = None
    AVATAR_HANDLER = None

    @staticmethod
    def get_distribution() -> str:
        """
        Get the distribution

        Returns:
            str: distribution name
            
        """
        if ReplaceHelper.DISTRO is None:
            try:
                ReplaceHelper.DISTRO = subprocess.check_output(['flatpak-spawn', '--host', 'bash', '-c', 'lsb_release -ds']).decode('utf-8').strip()
            except subprocess.CalledProcessError:
                ReplaceHelper.DISTRO = "Unknown"
        
        return ReplaceHelper.DISTRO

    @staticmethod
    def set_handler(handler):
        ReplaceHelper.AVATAR_HANDLER = handler

    @staticmethod
    def get_expressions() -> str:
        if ReplaceHelper.AVATAR_HANDLER is None:
            return ""
        result = ""
        for expression in ReplaceHelper.AVATAR_HANDLER.get_expressions():
            result += " (" + expression + ")"
        return result

    @staticmethod
    def get_desktop_environment() -> str:
        desktop = os.getenv("XDG_CURRENT_DESKTOP")
        if desktop is None:
            desktop = "Unknown"
        return desktop

def replace_variables(text: str) -> str:
    """
    Replace variables in prompts
    Supported variables:
        {DIR}: current directory
        {DISTRO}: distribution name
        {DE}: desktop environment

    Args:
        text: text of the prompt

    Returns:
        str: text with replaced variables
    """
    text = text.replace("{DIR}", os.getcwd())
    if "{DISTRO}" in text:
        text = text.replace("{DISTRO}", ReplaceHelper.get_distribution())
    if "{DE}" in text:
        text = text.replace("{DE}", ReplaceHelper.get_desktop_environment())
    if "{EXPRESSIONS}" in text:
        text = text.replace("{EXPRESSIONS}", ReplaceHelper.get_expressions())
    return text

def markwon_to_pango(markdown_text):
    initial_string = markdown_text
    # Convert bold text
    markdown_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', markdown_text)
    
    # Convert italic text
    markdown_text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', markdown_text)
         
    # Convert monospace text
    markdown_text = re.sub(r'`(.*?)`', r'<tt>\1</tt>', markdown_text)
    
    # Convert strikethrough text
    markdown_text = re.sub(r'~(.*?)~', r'<span strikethrough="true">\1</span>', markdown_text)
    
    # Convert links
    markdown_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', markdown_text)
    
    # Convert headers
    absolute_sizes = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large']
    markdown_text = re.sub(r'^(#+) (.*)$', lambda match: f'<span font_weight="bold" font_size="{absolute_sizes[6 - len(match.group(1)) - 1]}">{match.group(2)}</span>', markdown_text, flags=re.MULTILINE)
    
    # Check if the generated text is valid. If not just print it unformatted
    try:
        xml.dom.minidom.parseString(markdown_text)
    except Exception as _:
        return initial_string 
    return markdown_text

def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    if full_module_name == "git+https://github.com/openai/whisper.git":
        full_module_name = "whisper"
    try:
        return importlib.import_module(full_module_name)
    except ImportError as exc:
        if not (full_module_name + '.').startswith(exc.name + '.'):
            raise


def install_module(module, path):
    r = subprocess.check_output(["pip3", "install", "--target", path, module]).decode("utf-8")
    return r

def can_escape_sandbox():
    try:
        r = subprocess.check_output(["flatpak-spawn", "--host", "echo", "test"])
    except subprocess.CalledProcessError as e:
        return False
    return True

def override_prompts(override_setting, PROMPTS):
    prompt_list = {}
    for prompt in PROMPTS:
        if prompt in override_setting:
            prompt_list[prompt] = override_setting[prompt]
        else:
            prompt_list[prompt] = PROMPTS[prompt]
    return prompt_list


def force_async(fn):
    '''
    turns a sync function to async function using threads
    '''
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    pool = ThreadPoolExecutor()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        future = pool.submit(fn, *args, **kwargs)
        return asyncio.wrap_future(future)  # make it awaitable

    return wrapper


def force_sync(fn):
    '''
    turn an async function to sync function
    '''
    import asyncio

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        res = fn(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return asyncio.get_event_loop().run_until_complete(res)
        return res

    return wrapper

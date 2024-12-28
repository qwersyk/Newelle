from __future__ import absolute_import
import importlib, subprocess
import re, base64, io
import os, sys
import xml.dom.minidom, html
import json

class ReplaceHelper:
    DISTRO = None

    @staticmethod
    def get_distribution() -> str:
        """
        Get the distribution

        Returns:
            str: distribution name
            
        """
        if ReplaceHelper.DISTRO is None:
            try:
                ReplaceHelper.DISTRO = subprocess.check_output(get_spawn_command() + ['bash', '-c', 'lsb_release -ds']).decode('utf-8').strip()
            except subprocess.CalledProcessError:
                ReplaceHelper.DISTRO = "Unknown"
        
        return ReplaceHelper.DISTRO
    
    @staticmethod
    def get_desktop_environment() -> str:
        desktop = os.getenv("XDG_CURRENT_DESKTOP")
        if desktop is None:
            desktop = "Unknown"
        return desktop

def get_spawn_command() -> list:
    """
    Get the spawn command to run commands on the user system

    Returns:
        list: space diveded command  
    """
    if is_flatpak():
        return ["flatpak-spawn", "--host"]
    else:
        return []

def get_image_base64(image_str: str):
    """
    Get image string as base64 string, starting with data:/image/jpeg;base64,

    Args:
        image_str: content of the image codeblock 

    Returns:
       base64 encoded image 
    """
    if not image_str.startswith("data:image/jpeg;base64,"):
        image = encode_image_base64(image_str)
        return image
    else:
        return image_str

def get_image_path(image_str: str):
    """
    Get image string as image path

    Args:
        image_str: content of the image codeblock 

    Returns:
       image path 
    """
    if image_str.startswith("data:image/jpeg;base64,"):
        raw_data = base64.b64decode(image_str[len("data:image/jpeg;base64,"):])
        saved_image = "/tmp/" + image_str[len("data:image/jpeg;base64,"):][30:] + ".jpg"
        with open(saved_image, "wb") as f:
            f.write(raw_data)
        return saved_image
    return image_str

def convert_history_openai(history: list, prompts: list, vision_support : bool = False):
    """Converts Newelle history into OpenAI format

    Args:
        history (list): Newelle history 
        prompts (list): list of prompts 
        vision_support (bool): True if vision support

    Returns:
       history in openai format 
    """
    result = []
    if len(prompts) > 0:
        result.append({"role": "system", "content": "\n".join(prompts)})
    
    for message in history:
        if message["User"] == "Console":
            result.append({
                "role": "user",
                "content": "Console: " + message["Message"]
            })
        else:
            image, text = extract_image(message["Message"])
            if vision_support and image is not None and message["User"] == "User":
                image = get_image_base64(image)
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": text
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image}
                        }
                    ],
                })
            else:
                result.append({
                    "role": "user" if message["User"] == "User" else "assistant",
                    "content": message["Message"]
                })
    return result

def open_website(website):
    subprocess.Popen(get_spawn_command() + ["xdg-open", website])


def encode_image_base64(file_path):
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.mp4': 'video/mp4',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime'
    }

    ext = os.path.splitext(file_path)[1].lower()
    mime_type = mime_types.get(ext, 'image/jpeg')

    with open(file_path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"

def extract_image(message: str) -> tuple[str | None, str]:
    """
    Extract image from message

    Args:
        message: message string

    Returns:
        tuple[str, str]: image and text, if no image, image is None 
    """
    img = None
    if message.startswith("```image"):
        img = message.split("\n")[1]
        text = message.split("\n")[3:]                    
        text = "\n".join(text)
    else:
        text = message
    return img, text

def quote_string(s):
    if "'" in s:
        return "'" + s.replace("'", "'\\''") + "'"
    else:
        return "'" + s + "'"

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
    return text

def markwon_to_pango(markdown_text):
    markdown_text = html.escape(markdown_text)
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
        xml.dom.minidom.parseString("<html>" + markdown_text + "</html>")
    except Exception as e:
        print(markdown_text)
        print(e)
        return initial_string
    return markdown_text

def human_readable_size(size: float, decimal_places:int =2) -> str:
    size = int(size)
    unit = ''
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def find_module(full_module_name):
    """
    Returns module object if module `full_module_name` can be imported.

    Returns None if module does not exist.

    Exception is raised if (existing) module raises exception during its import.
    """
    try:
        return importlib.import_module(full_module_name)
    except Exception as _:
        return None


def install_module(module, path):
    if find_module("pip") is None:
        print("Downloading pip...")
        subprocess.check_output(["bash", "-c", "cd " + os.path.dirname(path) + " && wget https://bootstrap.pypa.io/get-pip.py && python get-pip.py"])
    r = subprocess.run([sys.executable, "-m", "pip", "install","--target", path, "--upgrade", module], capture_output=False) 
    return r

def is_flatpak() -> bool:
    """
    Check if we are in a flatpak

    Returns:
        bool: True if we are in a flatpak
    """
    if os.getenv("container"):
        return True
    return False

def can_escape_sandbox() -> bool:
    """
    Check if we can escape the sandbox 

    Returns:
        bool: True if we can escape the sandbox
    """
    if not is_flatpak():
        return True
    try:
        r = subprocess.check_output(["flatpak-spawn", "--host", "echo", "test"])
    except subprocess.CalledProcessError as _:
        return False
    return True

def get_streaming_extra_setting():
            return {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            }

def override_prompts(override_setting, PROMPTS):
    prompt_list = {}
    for prompt in PROMPTS:
        if prompt in override_setting:
            prompt_list[prompt] = override_setting[prompt]
        else:
            prompt_list[prompt] = PROMPTS[prompt]
    return prompt_list


def extract_json(input_string: str) -> str:
    """Extract JSON string from input string

    Args:
        input_string (): The input string 

    Returns:
        str: The JSON string 
    """
    # Regular expression to find JSON objects or arrays
    json_pattern = re.compile(r'\{.*?\}|\[.*?\]', re.DOTALL)
    
    # Find all JSON-like substrings
    matches = json_pattern.findall(input_string) 
    # Parse each match and return the first valid JSON
    for match in matches:
        try:
            json_data = json.loads(match)
            return match
        except json.JSONDecodeError:
            continue
    print("Wrong JSON", input_string)
    return []


def remove_markdown(text: str) -> str:
    """
    Remove markdown from text

    Args:
        text: The text to remove markdown from 

    Returns:
        str: The text without markdown 
    """
    # Remove headers
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    
    # Remove emphasis (bold and italic)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
    text = re.sub(r'__(.*?)__', r'\1', text)          # Bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)        # Italic
    text = re.sub(r'_(.*?)_', r'\1', text)            # Italic
    
    # Remove inline code
    text = re.sub(r'`([^`]*)`', r'\1', text)
    
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)

    # Remove links, keep the link text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # Remove images, keep the alt text
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'\1', text)

    # Remove strikethrough
    text = re.sub(r'~~(.*?)~~', r'\1', text)

    # Remove blockquotes
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)

    # Remove unordered list markers
    text = re.sub(r'^\s*[-+*]\s+', '', text, flags=re.MULTILINE)

    # Remove ordered list markers
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # Remove extra newlines
    text = re.sub(r'\n{2,}', '\n', text)

    return text.strip()

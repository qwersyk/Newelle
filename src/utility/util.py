from .media import get_image_base64, extract_image
import time 

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

def embed_image(text: str, image: str):
    """
    Inverse helper of extract_image.
    Combines text and image URL into a single Newelle message string.
    
    Adjust this function so that the resulting string, when passed to
    extract_image, returns the original (image, text) pair.
    """
    # For this example, we simply prepend the image marker.
    if image:
        # You might want to choose a format that matches your original extraction logic.
        return f"[image:{image}]\n{text}" if text else f"[image:{image}]"
    return text

def convert_history_newelle(openai_history: list, vision_support: bool = False):
    """
    Converts OpenAI history back into Newelle format.
    
    Args:
        openai_history (list): List of messages in OpenAI format.
        vision_support (bool): True if vision support is enabled.
    
    Returns:
        tuple: A tuple (newelle_history, prompts) where newelle_history is a list
               of dictionaries with keys "User" and "Message", and prompts is a list of prompt strings.
    """
    newelle_history = []
    prompts = []
    
    # If the first message is a system message, extract prompts from it.
    if openai_history and openai_history[0].get("role") == "system":
        prompts = openai_history[0].get("content", "").split("\n")
        openai_history = openai_history[1:]
    
    for message in openai_history:
        role = message.get("role")
        content = message.get("content")
        
        # Handle vision-support messages if content is a list.
        if vision_support and isinstance(content, list):
            text = None
            image = None
            for part in content:
                if part.get("type") == "text":
                    text = part.get("text")
                elif part.get("type") == "image_url":
                    image = part.get("image_url", {}).get("url")
            combined_message = embed_image(text, image)
            newelle_history.append({
                "User": "User",  # Vision messages came from a user.
                "Message": combined_message
            })
        # Handle Console messages (role "user" with a "Console: " prefix)
        elif isinstance(content, str) and content.startswith("Console: "):
            newelle_history.append({
                "User": "Console",
                "Message": content[len("Console: "):]
            })
        # Regular text messages.
        else:
            if role == "user":
                newelle_history.append({
                    "User": "User",
                    "Message": content
                })
            elif role == "assistant":
                newelle_history.append({
                    "User": "Assistant",
                    "Message": content
                })
            else:
                # Fallback for any unexpected role.
                newelle_history.append({
                    "User": role,
                    "Message": content
                })
                
    return newelle_history, prompts

def get_streaming_extra_setting():
            """Return extra setting for handler to stream messages

            Returns:
               extra setting for handler to stream messages 
            """
            return {
                "key": "streaming",
                "title": _("Message Streaming"),
                "description": _("Gradually stream message output"),
                "type": "toggle",
                "default": True
            }

def override_prompts(override_setting, PROMPTS):
    """Override prompts

    Args:
        override_setting (): the prompts edited by the user  
        PROMPTS (): the prompts constant

    Returns:
        
    """
    prompt_list = {}
    for prompt in PROMPTS:
        if prompt in override_setting:
            prompt_list[prompt] = override_setting[prompt]
        else:
            prompt_list[prompt] = PROMPTS[prompt]
    return prompt_list


class PerformanceMonitor():
    def __init__(self) -> None:
        self.times = []
        self.names = []

    def add(self, name):
        self.times.append(time.time())
        self.names.append(name)

    def print_differences(self):
        for i in range(len(self.times) - 1):
            print(self.names[i], self.times[i + 1] - self.times[i])

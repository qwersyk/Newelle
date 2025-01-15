from .media import get_image_base64, extract_image


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


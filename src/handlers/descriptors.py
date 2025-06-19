from .handler import Handler


def HandlerDescription(key: str, title: str, description: str, handler_class: Handler, website:str|None=None):
    """Generate Handler description, used by Newelle to generate settings and use handlers

    Args:
        key: unique key of the handler 
        title: Name of the handler 
        description: Small description about the handler
        handler_class: Hanlder class 

    Returns:
       dict that contains the description 
    """
    desc = {
        "key": key,
        "title": title,
        "description": description, 
        "class": handler_class
    }
    if website is not None:
        desc["website"] = website 
    return desc

def PromptDescription(key: str, title: str, description: str, text:str, setting_name:str|None=None, editable:bool=True, default:bool=True, show_in_settings:bool=True):
    """Generate a Prompt description, used by Newelle to generate settings of the prompt and add it

    Args:
        key: unique key of the prompt  
        title: Title of the prompt
        description: Smal description of the prompt 
        text: Actual text of the prompt
        setting_name (optional): Setting name, in case on/off depends on another prompt, by default equal to the key 
        editable: if the prompt is editable by the user, defaults to true 
        default: if the prompt is enabled by default 
        show_in_settings: if the prompt is shown in the settings 

    Returns:
       dict that contains the description 
    """
    return {
        "key": key,
        "title": title, 
        "description": description,
        "text": text,
        "setting_name": setting_name if setting_name is not None else key,
        "editable": editable,
        "default": default,
        "show_in_settings": show_in_settings
    } 

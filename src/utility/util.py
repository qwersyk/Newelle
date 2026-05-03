from .media import get_image_base64, extract_image
import time
import json
import re
import uuid

def convert_messages_openai_to_newelle(messages: list) -> tuple[str, list[dict], list[str]]:
    """Convert OpenAI format messages to Newelle format.

    Args:
        messages (list): List of messages with "role" and "content" keys or attributes.

    Returns:
        tuple of (last_user_message, history, system_prompt)
    """
    system_prompt = []
    history = []
    last_user_message = ""

    def _tool_calls_function_dict(tc):
        """Normalize a tool_calls entry to a function dict."""
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
        else:
            fn = getattr(tc, "function", None)
        if fn is None:
            return {}
        if isinstance(fn, dict):
            return fn
        if hasattr(fn, "model_dump"):
            return fn.model_dump()
        return vars(fn) if hasattr(fn, "__dict__") else {}

    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            name = msg.get("name")
        else:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", None)
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            name = getattr(msg, "name", None)
        if content is None:
            content = ""

        if role == "system":
            system_prompt.append(content)
        elif role == "tool":
            tool_name = name or "unknown"
            tid = tool_call_id or "unknown"
            history.append({
                "User": "Console",
                "Message": f"[Tool: {tool_name}, ID: {tid}]\n{content}",
            })
        elif role == "assistant" and tool_calls:
            parts = []
            if content:
                parts.append(content)
            for tc in tool_calls:
                fn = _tool_calls_function_dict(tc)
                tool_data = {
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", {}),
                }
                if isinstance(tc, dict):
                    tc_id = tc.get("id")
                else:
                    tc_id = getattr(tc, "id", None)
                if isinstance(tc_id, str) and tc_id.strip():
                    tool_data["id"] = tc_id.strip()
                if isinstance(tool_data["arguments"], str):
                    try:
                        tool_data["arguments"] = json.loads(tool_data["arguments"])
                    except json.JSONDecodeError:
                        pass
                parts.append(f"```json\n{json.dumps(tool_data)}\n```")
            history.append({"User": "Assistant", "Message": "\n".join(parts)})
        elif role == "user":
            last_user_message = content
            history.append({"User": "User", "Message": content})
        elif role == "assistant":
            history.append({"User": "Assistant", "Message": content})

    return last_user_message, history, system_prompt


def parse_tool_calls_from_assistant_content(text: str) -> tuple[str | None, list[dict]]:
    """Pull ``tool_calls`` out of assistant text that uses fenced ```json blocks (Newelle native).

    Recognizes JSON objects with a ``tool`` key or ``name`` plus ``arguments``, matching
    :meth:`OpenAIHandler.generate_text` output.

    Returns:
        (content, tool_calls) where ``tool_calls`` is OpenAI chat.completion message format:
        each item has ``id``, ``type``, ``function`` with ``name`` and JSON-string ``arguments``.
        Fenced blocks that are valid tool calls are removed from ``content``.
    """
    if not text:
        return "", []

    pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    tool_calls: list[dict] = []
    parts: list[str] = []
    last = 0

    for m in pattern.finditer(text):
        parts.append(text[last:m.start()])
        inner = m.group(1).strip()
        try:
            obj = json.loads(inner)
        except json.JSONDecodeError:
            parts.append(m.group(0))
            last = m.end()
            continue

        tool_name = None
        arguments = {}
        if isinstance(obj, dict):
            if "tool" in obj:
                tool_name = obj.get("tool")
                arguments = obj.get("arguments", {})
            elif "name" in obj:
                tool_name = obj.get("name")
                arguments = obj.get("arguments", {})

        if tool_name and isinstance(tool_name, str):
            args_str = arguments if isinstance(arguments, str) else json.dumps(arguments)
            raw_id = obj.get("id") if isinstance(obj, dict) else None
            if isinstance(raw_id, str) and raw_id.strip():
                call_id = raw_id.strip()
            else:
                call_id = f"call_{uuid.uuid4().hex[:24]}"
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": args_str,
                },
            })
            last = m.end()
            continue

        parts.append(m.group(0))
        last = m.end()

    parts.append(text[last:])
    merged = "".join(parts).strip()

    if tool_calls:
        content_out = merged if merged else None
    else:
        content_out = merged

    return content_out, tool_calls


def extract_tools_from_prompts(prompts: list[str], remove_tool_prompt: bool = True) -> tuple[list[dict] | None, list[str]]:
    """Extract tools JSON wrapped in <tools> tags from prompts and return the parsed JSON and prompts without the JSON."""
    new_prompts = []
    tools_json = None
    for prompt in prompts:
        if "<tools>" in prompt and "</tools>" in prompt:
            start_index = prompt.find("<tools>")
            end_index = prompt.find("</tools>") + len("</tools>")
            tools_str = prompt[start_index + len("<tools>"):prompt.find("</tools>")].strip()
            try:
                extracted = json.loads(tools_str)
                tools_json = []
                for tool in extracted:
                    if "parameters" in tool:
                        tools_json.append({"type": "function", "function": {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": tool["parameters"]
                        }})
                    else:
                        tools_json.append({"type": "function", "function": {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": {"type": "object", "properties": {}}
                        }})
            except json.JSONDecodeError as e:
                print("Failed to decode tools from prompt:", e)
            if remove_tool_prompt:
                new_prompt = prompt[:start_index] + prompt[end_index:]
                if new_prompt.strip():
                    new_prompts.append(new_prompt.strip())
            else:
                new_prompts.append(prompt)
        else:
            if prompt.strip():
                new_prompts.append(prompt)
    return tools_json, new_prompts

def convert_history_openai(history: list, prompts: list, vision_support : bool = False, native_tool_calling: bool = True):
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
            if native_tool_calling:
                match = re.match(r"^\[Tool:\s*(.*?),\s*ID:\s*(.*?)\]\n(.*)", message["Message"], re.DOTALL)
                if match:
                    result.append({
                        "role": "tool",
                        "name": match.group(1),
                        "tool_call_id": match.group(2),
                        "content": match.group(3)
                    })
                else:
                    result.append({
                        "role": "user",
                        "content": "Console: " + message["Message"],
                    })
            else:
                result.append({
                    "role": "user",
                    "content": "Console: " + message["Message"],
                })
        else:
            if native_tool_calling and message["User"] == "Assistant" and "```json" in message["Message"]:
                json_blocks = list(re.finditer(r'```json\s*(.*?)\s*```', message["Message"], re.DOTALL))
                if json_blocks:
                    text_part = message["Message"][:message["Message"].find("```json")].strip()
                    
                    msg_idx = history.index(message)
                    console_msgs = [(i, m) for i, m in enumerate(history[msg_idx+1:], msg_idx+1) if m["User"] == "Console"]
                    used_console = set()
                    
                    tool_calls = []
                    for block in json_blocks:
                        try:
                            tool_data = json.loads(block.group(1).strip())
                            tool_name = tool_data.get("name", tool_data.get("tool"))
                            tool_args = tool_data.get("arguments", {})

                            tool_id = tool_data.get("id")
                            if isinstance(tool_id, str) and tool_id.strip():
                                tool_id = tool_id.strip()
                            else:
                                tool_id = None
                            if not tool_id:
                                for ci, cm in console_msgs:
                                    if ci in used_console:
                                        continue
                                    match = re.match(r"^\[Tool:\s*(.*?),\s*ID:\s*(.*?)\]", cm["Message"])
                                    if match and match.group(1) == tool_name:
                                        tool_id = match.group(2)
                                        used_console.add(ci)
                                        break
                                if not tool_id:
                                    tool_id = "unknown"
                            
                            tool_calls.append({
                                "id": tool_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(tool_args)
                                }
                            })
                        except:
                            pass
                    
                    if tool_calls:
                        ast_msg = {"role": "assistant"}
                        if text_part:
                            ast_msg["content"] = text_part
                        ast_msg["tool_calls"] = tool_calls
                        result.append(ast_msg)
                        continue

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
    return aggregate_messages(result, "openai")

def aggregate_messages(messages: list, format="newelle"):
    """Aggregate multiple consecutive messages from the same role into a single message.

    Args:
        messages (list): List of messages to aggregate.
        format (str): Message format ("newelle" or "openai").

    Returns:
        list: Aggregated messages.
    """
    if not messages:
        return []

    # Format configuration
    formats = {
        "newelle": {"role": "User", "content": "Message"},
        "openai": {"role": "role", "content": "content"}
    }
    blacklist_roles = ["Console", "tool"]

    if format not in formats:
        return messages

    role_key = formats[format]["role"]
    content_key = formats[format]["content"]

    aggregated_messages = []
    current_message = None

    for message in messages:
        if current_message is None:
            current_message = message.copy()
            continue

        if current_message[role_key] in blacklist_roles or message[role_key] in blacklist_roles:
            aggregated_messages.append(current_message)
            current_message = message.copy()
            continue

        if current_message[role_key] == message[role_key]:
            content1 = current_message[content_key]
            content2 = message[content_key]

            if isinstance(content1, list) or isinstance(content2, list):
                c1 = content1 if isinstance(content1, list) else [{"type": "text", "text": str(content1)}]
                c2 = content2 if isinstance(content2, list) else [{"type": "text", "text": str(content2)}]
                current_message[content_key] = c1 + c2
            else:
                current_message[content_key] = str(content1) + "\n" + str(content2)
        else:
            aggregated_messages.append(current_message)
            current_message = message.copy()

    if current_message:
        aggregated_messages.append(current_message)

    return aggregated_messages


VOID_TOOL_RESULT_PLACEHOLDER = "Tool executed successfully"


def _tool_call_dict_id(tc: object) -> str:
    if isinstance(tc, dict):
        tid = tc.get("id")
    else:
        tid = getattr(tc, "id", None)
    return tid if isinstance(tid, str) else ""


def _tool_call_dict_name(tc: object) -> str:
    fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
    if fn is None:
        return ""
    if isinstance(fn, dict):
        return str(fn.get("name", "") or "")
    return str(getattr(fn, "name", "") or "")


def balance_native_tool_call_responses(messages: list) -> list:
    """Ensure each assistant ``tool_calls`` entry has a matching ``role: tool`` message.

    Display-only tools may omit console output (no ``[Tool: …]`` row in history). Several
    APIs then reject the turn (e.g. mismatched function call / response counts). Missing
    Missing replies use ``VOID_TOOL_RESULT_PLACEHOLDER`` in the tool message ``content`` field.
    """
    if not messages:
        return messages
    out: list = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        tcs = msg.get("tool_calls") if isinstance(msg, dict) else None
        if isinstance(msg, dict) and msg.get("role") == "assistant" and tcs:
            out.append(msg)
            expected = [(_tool_call_dict_id(tc), _tool_call_dict_name(tc)) for tc in tcs]
            j = i + 1
            following_tools: list = []
            while j < n and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
                following_tools.append(messages[j])
                j += 1
            matched = [False] * len(following_tools)
            for eid, ename in expected:
                found: int | None = None
                for fi, fm in enumerate(following_tools):
                    if matched[fi]:
                        continue
                    rid = fm.get("tool_call_id")
                    rid_s = rid if isinstance(rid, str) else ("" if rid is None else str(rid))
                    if rid_s == eid:
                        found = fi
                        break
                if found is not None:
                    matched[found] = True
                else:
                    row = {"role": "tool", "tool_call_id": eid, "content": VOID_TOOL_RESULT_PLACEHOLDER}
                    if ename:
                        row["name"] = ename
                    out.append(row)
            out.extend(following_tools)
            i = j
        else:
            out.append(msg)
            i += 1
    return out


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
        tool_calls = message.get("tool_calls")
        
        if role == "tool":
            tool_name = message.get("name", "unknown")
            tool_id = message.get("tool_call_id", "unknown")
            newelle_history.append({
                "User": "Console",
                "Message": f"[Tool: {tool_name}, ID: {tool_id}]\n{content or ''}"
            })
        elif role == "assistant" and tool_calls:
            parts = []
            if content:
                parts.append(content)
            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_data = {
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", {}),
                }
                if tc.get("id"):
                    tool_data["id"] = tc["id"]
                if isinstance(tool_data["arguments"], str):
                    try:
                        tool_data["arguments"] = json.loads(tool_data["arguments"])
                    except:
                        pass
                parts.append(f"```json\n{json.dumps(tool_data)}\n```")
            newelle_history.append({
                "User": "Assistant",
                "Message": "\n".join(parts)
            })
        elif vision_support and isinstance(content, list):
            text = None
            image = None
            for part in content:
                if part.get("type") == "text":
                    text = part.get("text")
                elif part.get("type") == "image_url":
                    image = part.get("image_url", {}).get("url")
            combined_message = embed_image(text, image)
            newelle_history.append({
                "User": "User",
                "Message": combined_message
            })
        elif isinstance(content, str) and content.startswith("Console: "):
            newelle_history.append({
                "User": "Console",
                "Message": content[len("Console: "):]
            })
        elif role == "user":
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

import threading
import json
import gettext
import hashlib
import re
from typing import Any, Callable

_ = gettext.gettext

from .llm import LLMHandler
from ...utility.system import open_website
from ...utility import LLMResponse, convert_history_openai, get_streaming_extra_setting, extract_tools_from_prompts, balance_native_tool_call_responses, parse_assistant_native_tool_calls, parse_tool_console_message
from ...handlers import ExtraSettings, ErrorSeverity


class OpenAIHandler(LLMHandler):
    key = "openai"
    default_models = (("gpt-5.6-luna", "gpt-5.6-luna"), )
    RESPONSE_STATE_KEY = "OpenAIResponse"
    RESPONSE_STATE_VERSION = 1
    MISSING_TOOL_OUTPUT = "Tool result unavailable in the current Newelle history."
    def __init__(self, settings, path):
        super().__init__(settings, path)
        if self.get_setting("models", False) is None:
            self.models = self.default_models 
            threading.Thread(target=self.get_models, args=(False, True)).start()
        else:
            self.models = json.loads(self.get_setting("models", False))

    def get_models_list(self):
        return self.models

    def set_secondary_settings(self, secondary: bool):
        if self.key != "openai":
            endpoint = self.get_setting("endpoint", search_default=False)
        out = super().set_secondary_settings(secondary)
        if secondary and self.key != "openai" and endpoint is not None:
            self.set_setting("endpoint", endpoint) 
        return out 

    def get_models(self, manual=False, multithread = False):
        if not multithread:
            threading.Thread(target=self.get_models, args=(manual, True)).start()
            return  
        if self.is_installed():
            try:
                import openai
                api = self.get_setting("api", False)
                if api is None:
                    return
                client = openai.Client(api_key=api, base_url=self.get_setting("endpoint"), default_headers=self.get_extra_headers())
                models = client.models.list()
                result = tuple()
                for model in models:
                    result += ((model.id, model.id,), )
                self.models = result
                self.set_setting("models", json.dumps(result))
                self.settings_update()
            except Exception as e:
                if manual:
                    self.throw("Error getting " + self.key + " models: " + str(e), ErrorSeverity.WARNING)
                print("Error getting " + self.key + " models: " + str(e))
            
    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    def supports_audio(self) -> bool:
        return bool(self.get_setting("audio_input", False, False)) and not self.uses_responses_api()

    def supports_vision(self) -> bool:
        return True

    def supports_video_vision(self) -> bool:
        return self.supports_vision()

    def get_supported_files(self) -> list[str]:
        if not self.supports_vision():
            return []
        return ["*.pdf", "*.txt", "*.md", "*.json", "*.html", "*.xml",
                "*.doc", "*.docx", "*.rtf", "*.odt", "*.ppt", "*.pptx",
                "*.csv", "*.tsv", "*.xls", "*.xlsx"]

    def get_video_mode(self) -> str:
        # OpenAI's endpoints accept images, while compatible providers may
        # implement the video_url extension to Chat Completions.
        endpoint = self.get_setting("endpoint", False, "https://api.openai.com/v1") or ""
        from urllib.parse import urlparse
        default = "frames" if urlparse(endpoint).hostname == "api.openai.com" or self.uses_responses_api() else "native"
        return self.get_setting("video_mode", False, default)

    def get_extra_settings(self) -> list:
        settings = self.build_extra_settings("OpenAI", True, True, True, True, True, "https://openai.com/policies/row-privacy-policy/", None, False, False, True, self.supports_thinking(), True, supports_custom_headers=True)
        settings.append(
            ExtraSettings.ToggleSetting(
                "responses_api",
                _("Use Responses API"),
                _("Use the /responses endpoint instead of /chat/completions."),
                False,
            )
        )
        settings.append(ExtraSettings.ComboSetting(
            "video_mode", _("Video Input"),
            _("Send videos natively to compatible providers, or sample up to 16 frames using ffmpeg (without audio). OpenAI requires frames."),
            ((_("Video URL"), "native"), (_("Sample Frames"), "frames")),
            self.get_video_mode(),
        ))
        return settings

    def get_duplication_settings(self) -> list[dict] | None:
        # OpenAI-compatible handlers inherit this class. Only the canonical
        # OpenAI entry may be copied; provider-specific subclasses and copies
        # must not recursively expose duplication.
        if self.key != "openai":
            return None
        return [
            ExtraSettings.EntrySetting(
                "endpoint",
                _("API Endpoint"),
                _("API base URL for the OpenAI-compatible provider"),
                self.get_setting("endpoint"),
            )
        ]

    def build_extra_settings(self, provider_name: str, has_api_key: bool, has_stream_settings: bool, endpoint_change: bool, allow_advanced_params: bool, supports_automatic_models: bool, privacy_notice_url : str | None, model_list_url: str | None, default_advanced_params: bool = False, default_automatic_models: bool = False, supports_custom_body : bool = False, supports_thinking: bool = False, supports_tool_calling: bool = True, has_tool_calling_option: bool = True, supports_custom_headers: bool = False, has_audio_input_option: bool = True) -> list:
        """Helper to build the list of extra settings for OpenAI Handlers

        Args:
            provider_name: name of the provider, it is stated in model settings
            has_api_key: if to show the api key setting
            has_stream_settings: if to show the message streaming setting
            endpoint_change: if to allow the endpoint change
            allow_advanced_params: if to allow advanced parameters like temperature ...
            supports_automatic_models: if it supports automatic model fetching
            privacy_notice_url: the url of the privacy policy, None if not stated
            model_list_url: human accessible page that lists the available models
            supports_thinking: if to show thinking mode and effort settings
            has_audio_input_option: if to show the audio input capability toggle

        Returns:
            list containing the extra settings
        """
        api_settings = [ 
            ExtraSettings.EntrySetting("api", _("API Key"), _("API Key for " + provider_name), "", password=True),
        ]
        endpoint_settings = [
            ExtraSettings.EntrySetting("endpoint", _("API Endpoint"), _("API base url, change this to use interference APIs"), "https://api.openai.com/v1/"),
        ]
        custom_model = [
            ExtraSettings.ToggleSetting("custom_model", _("Use Custom Model"), _("Use a custom model"), not default_automatic_models, update_settings=True)
        ]
        advanced_param_toggle = [
            ExtraSettings.ToggleSetting("advanced_params", _("Advanced Parameters"), _("Include parameters like Top-P, Temperature, etc."), default_advanced_params, update_settings=True)
        ]
        models_settings = [ 
            ExtraSettings.EntrySetting("model", _("Model"), _("Name of the LLM Model to use"), self.models[0][0] if len(self.models) > 0 else ""),
        ]
        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        automatic_models_settings = [
            ExtraSettings.ComboSetting(
                    "model",
                    _(provider_name + " Model"),
                    _(f"Name of the {provider_name} Model"),
                    self.models,
                    self.models[0][0] if len(self.models) > 0 else "",
                    refresh=lambda button: self.get_models(),
                )
        ]

        if model_list_url is not None:
            models_settings[0]["website"] = model_list_url
        
        advanced_settings = [
            ExtraSettings.ScaleSetting("top-p", _("Top-P"), _("An alternative to sampling with temperature, called nucleus sampling"), 1, 0, 1, 2),
            ExtraSettings.ScaleSetting("temperature", _("Temperature"), _("What sampling temperature to use. Higher values will make the output more random"), 1, 0, 2, 1),
            ExtraSettings.ScaleSetting("frequency-penalty", _("Frequency Penalty"), _("Number between -2.0 and 2.0. Positive values decrease the model's likelihood to repeat the same line verbatim"), 0, -2, 2, 0),
            ExtraSettings.ScaleSetting("presence-penalty", _("Presence Penalty"), _("Number between -2.0 and 2.0. Positive values decrease the model's likelihood to talk about new topics"), 0, -2, 2, 0),
        ]
        thinking_toggle = [
            ExtraSettings.ToggleSetting("thinking", _("Thinking Mode"), _("Enable thinking mode for the model"), False, update_settings=True)
        ]
        thinking_effort_settings = [
            ExtraSettings.ComboSetting(
                "thinking_effort",
                _("Thinking Effort"),
                _("Amount of reasoning effort to allocate for the model"),
                (("none", "none"), ("minimal", "minimal"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "xhigh")),
                "medium"
            )
        ]
        custom_body = ExtraSettings.MultilineEntrySetting("custom_body", _("Custom Options"), _("Provide a JSON containing the custom options"), "{}")
        custom_headers = ExtraSettings.MultilineEntrySetting("custom_headers", _("Custom Headers"), _("Provide a JSON containing custom HTTP headers to send with every request"), "{}")

        privacy_notice = [
            ExtraSettings.ButtonSetting(
                    "privacy", _("Privacy Policy"), _("Open privacy policy website"),
                    lambda button: open_website(privacy_notice_url), None, "internet-symbolic"
                )
        ]
        settings = []
        if has_api_key:
            settings += (api_settings)
        if endpoint_change:
            settings += (endpoint_settings)
        if supports_automatic_models:
            settings += (custom_model)
            custom = self.get_setting("custom_model", False, not default_automatic_models)
            if custom:
                settings += models_settings
            else:
                settings += automatic_models_settings
        if has_stream_settings:
            settings.append(get_streaming_extra_setting())
        if allow_advanced_params:
            settings += advanced_param_toggle
            advanced = self.get_setting("advanced_params", False)
            if advanced or (advanced is None and default_advanced_params):
                settings += advanced_settings
        if supports_thinking:
            settings += thinking_toggle
            thinking = self.get_setting("thinking", False)
            if thinking:
                settings += thinking_effort_settings
        if privacy_notice_url is not None:
            settings += privacy_notice
        if has_tool_calling_option:
            settings += [
                ExtraSettings.ToggleSetting("native_tool_calling", _("Native Tool Calling"), _("Enable native tool calling (Will use API's tool calling formatting instead of Newelle's. Disable only if you have issues with tool calling or the model you are using does not support it natively)"), supports_tool_calling)
            ]
        if has_audio_input_option:
            settings.append(ExtraSettings.ToggleSetting(
                "audio_input", _("Model supports audio input"),
                _("Enable for audio-capable Chat Completions models. Unavailable with Responses API."), False,
            ))
        if supports_custom_body:
            settings += [custom_body]
        if supports_custom_headers:
            settings += [custom_headers]
        return settings

    def convert_history(self, history: list, prompts: list | None = None) -> list:
        if prompts is None:
            prompts = self.prompts
        return convert_history_openai(
            history, prompts, self.supports_vision(),
            self.get_setting("native_tool_calling", False, True),
            audio_support=self.supports_audio(),
            supported_files=self.get_supported_files(),
            video_support=self.supports_video_vision(), video_mode=self.get_video_mode(),
        )

    def uses_responses_api(self) -> bool:
        return self.get_setting("responses_api", False, False)

    @staticmethod
    def convert_responses_input(messages: list) -> list:
        """Convert the shared Chat Completions history into Responses input items."""
        result = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "tool":
                result.append({
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": content,
                })
                continue
            if role == "assistant" and message.get("tool_calls"):
                if content:
                    result.append({"role": role, "content": content})
                for tool_call in message["tool_calls"]:
                    function = tool_call["function"]
                    arguments = function.get("arguments", "")
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments)
                    result.append({
                        "type": "function_call",
                        "call_id": tool_call["id"],
                        "name": function["name"],
                        "arguments": arguments,
                    })
                continue
            if isinstance(content, list):
                converted_content = []
                for item in content:
                    if item.get("type") == "text":
                        converted_content.append({"type": "input_text", "text": item["text"]})
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url", {})
                        converted_content.append({
                            "type": "input_image",
                            "image_url": image_url.get("url") if isinstance(image_url, dict) else image_url,
                            "detail": "auto",
                        })
                    elif item.get("type") == "file":
                        converted_content.append({"type": "input_file", **item["file"]})
                    elif item.get("type") == "video_url":
                        raise ValueError(_("The Responses API requires sampled video frames; select Sample Frames for Video Input"))
                    else:
                        converted_content.append(item)
                content = converted_content
            result.append({"role": role, "content": content})
        return result

    @staticmethod
    def convert_responses_tools(tools: list) -> list:
        return [{"type": "function", **tool["function"]} for tool in tools]

    @staticmethod
    def _value(item: object, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    @classmethod
    def _plain(cls, value):
        """Return an SDK-independent, pickle/JSON-safe representation."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "to_dict"):
            return cls._plain(value.to_dict())
        if isinstance(value, dict):
            return {str(key): cls._plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._plain(item) for item in value]
        if hasattr(value, "__dict__"):
            return {
                key: cls._plain(item)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return str(value)

    @staticmethod
    def _json_hash(value) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _message_hash(cls, message: str | dict) -> str:
        """Hash the visible message, normalizing get_history's <think> split."""
        if isinstance(message, dict):
            text = str(message.get("Message", "") or "")
            reasoning = message.get("Reasoning")
        else:
            text = str(message or "")
            reasoning = None

        if reasoning is None:
            match = re.search(r"<think>(.*?)(?:</think>|\Z)", text, flags=re.DOTALL)
            if match:
                reasoning = match.group(1)
                text = (text[:match.start()] + text[match.end():]).strip()

        return cls._json_hash({
            "text": text.strip(),
            "reasoning": str(reasoning).strip() if reasoning is not None else None,
        })

    def _response_state_matches(self, message: dict, items: list) -> bool:
        state = message.get(self.RESPONSE_STATE_KEY)
        if not isinstance(state, dict):
            return False
        return bool(
            state.get("version") == self.RESPONSE_STATE_VERSION
            and state.get("model") == self.get_setting("model")
            and state.get("endpoint") == self.get_setting("endpoint")
            and isinstance(state.get("output"), list)
            and state.get("input_hash") == self._json_hash(items)
            and state.get("message_hash") == self._message_hash(message)
        )

    @staticmethod
    def _function_call_is_executable(item: dict) -> bool:
        caller = item.get("caller")
        caller_type = caller.get("type") if isinstance(caller, dict) else None
        return bool(
            item.get("status") in (None, "completed")
            and not item.get("namespace")
            and caller_type in (None, "direct")
        )

    def _convert_responses_history_message(self, history: list, index: int) -> list:
        """Reuse Newelle's existing role/vision/tool parser without aggregating rows."""
        message = history[index]
        if message.get("User") == "Console":
            parsed = parse_tool_console_message(message.get("Message", ""))
            if parsed is not None:
                _tool_name, tool_id, tool_output = parsed
                return [{
                    "type": "function_call_output",
                    "call_id": tool_id,
                    "output": tool_output,
                }]

        local_history = [message]
        if message.get("User") == "Assistant":
            # Native tool parsing uses subsequent Console rows to recover call IDs.
            # Limit the scan to this assistant turn so an edited/deleted result
            # cannot be paired with a later call that happens to use the same tool.
            console_messages = []
            for following_index, item in enumerate(
                history[index + 1:], start=index + 1
            ):
                if item.get("User") == "Console":
                    local_history.append(item)
                    console_messages.append((following_index, item))
                elif item.get("User") == "User" and item.get("ToolContext"):
                    continue
                else:
                    break
            parsed = parse_assistant_native_tool_calls(
                message.get("Message", ""),
                console_messages,
                arguments_as_json_string=True,
            )
            if parsed is not None:
                text, tool_calls, _used = parsed
                assistant = {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": tool_calls,
                }
                return self.convert_responses_input([assistant])
        converted = self.convert_history(local_history, [])
        if not converted:
            return []
        # Console rows are converted on their own iteration. Only take this row.
        return self.convert_responses_input([converted[0]])

    def _build_responses_input(self, history: list) -> tuple[list, tuple[dict, int] | None]:
        """Build lossless input and find the newest locally verified response anchor."""
        items = []
        anchor = None
        available_outputs = set()
        seen_calls = set()

        # Know which historical calls really have a result before balancing calls.
        for index, message in enumerate(history):
            if message.get("User") != "Console":
                continue
            for item in self._convert_responses_history_message(history, index):
                if item.get("type") == "function_call_output" and item.get("call_id"):
                    available_outputs.add(item["call_id"])

        for index, message in enumerate(history):
            response_end = None
            if (
                message.get("User") == "Assistant"
                and self._response_state_matches(message, items)
            ):
                state = message[self.RESPONSE_STATE_KEY]
                output = self._plain(state["output"])
                response_end = 0
            else:
                output = self._convert_responses_history_message(history, index)

            normalized_output = []
            for item in output:
                item_type = item.get("type")
                call_id = item.get("call_id")
                if item_type == "function_call_output" and call_id not in seen_calls:
                    # A context-manager selection may retain a Console row but
                    # drop its call. Keep the information without sending an
                    # invalid orphan function result.
                    normalized_output.append({
                        "role": "user",
                        "content": "Console: " + str(item.get("output", "")),
                    })
                    continue
                normalized_output.append(item)
                if (
                    item_type == "function_call"
                    and self._function_call_is_executable(item)
                    and call_id
                ):
                    seen_calls.add(call_id)
            items.extend(normalized_output)

            if response_end is not None:
                response_end = len(items)
                if state.get("id") and state.get("store", True) is not False:
                    anchor = (state, response_end)

            # Responses requires an output for every historical function call.
            for item in normalized_output:
                if (
                    item.get("type") != "function_call"
                    or not self._function_call_is_executable(item)
                ):
                    continue
                call_id = item.get("call_id")
                if call_id and call_id not in available_outputs:
                    items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self.MISSING_TOOL_OUTPUT,
                    })

        return items, anchor

    @classmethod
    def _annotated_text(cls, text: str, annotations: list, sources: list, numbers: dict) -> str:
        edits = []
        fallback_markers = []

        def register(key, source_line):
            if key not in numbers:
                numbers[key] = len(numbers) + 1
                sources.append((numbers[key], source_line))
            return numbers[key]

        for annotation in annotations or []:
            annotation = cls._plain(annotation)
            annotation_type = annotation.get("type")
            if annotation_type == "url_citation":
                url = str(annotation.get("url", "") or "").strip()
                if not url:
                    continue
                title = " ".join(str(annotation.get("title") or url).split())
                title = title.replace("[", "\\[").replace("]", "\\]")
                number = register(("url", url), f"[{title}]({url})")
                start = annotation.get("start_index")
                end = annotation.get("end_index")
                if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
                    edits.append((start, end, f"[{number}]"))
                else:
                    fallback_markers.append(f"[{number}]")
            elif annotation_type in (
                "file_citation",
                "container_file_citation",
                "file_path",
            ):
                file_id = " ".join(str(annotation.get("file_id", "") or "").split())
                filename = " ".join(
                    str(annotation.get("filename") or file_id or "OpenAI file").split()
                )
                container_id = " ".join(
                    str(annotation.get("container_id", "") or "").split()
                )
                if container_id:
                    source = (
                        f"{filename} (OpenAI container: {container_id}, "
                        f"file: {file_id or 'unknown'})"
                    )
                else:
                    source = f"{filename} (OpenAI file: {file_id or 'unknown'})"
                number = register(
                    ("file", container_id, file_id or filename),
                    source,
                )
                if annotation_type == "container_file_citation":
                    start = annotation.get("start_index")
                    end = annotation.get("end_index")
                    if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
                        edits.append((start, end, f"[{number}]"))
                    else:
                        fallback_markers.append(f"[{number}]")
                else:
                    # file_citation/file_path ``index`` identifies a file in
                    # the provider list, not a character offset in the text.
                    fallback_markers.append(f"[{number}]")

        # Apply provider offsets from right to left and reject overlapping spans.
        right_boundary = len(text)
        for start, end, marker in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
            if end > right_boundary:
                fallback_markers.append(marker)
                continue
            text = text[:start] + marker + text[end:]
            right_boundary = start
        if fallback_markers:
            text = text.rstrip() + " " + " ".join(fallback_markers)
        return text

    @classmethod
    def format_responses_output(cls, output: list, fallback_text: str = "") -> str:
        """Project all public Responses output items onto Newelle Markdown."""
        visible = []
        reasoning = []
        sources = []
        source_numbers = {}
        saw_text = False

        for raw_item in output or []:
            item = cls._plain(raw_item)
            item_type = item.get("type", "output")
            if item_type == "reasoning":
                for field in ("summary", "content"):
                    for part in item.get(field, []) or []:
                        part = cls._plain(part)
                        text = part.get("text")
                        text = str(text).strip() if text else ""
                        if text and text not in reasoning:
                            reasoning.append(text)
                continue

            if item_type == "message":
                for content in item.get("content", []) or []:
                    content = cls._plain(content)
                    content_type = content.get("type")
                    if content_type == "output_text":
                        saw_text = True
                        text = cls._annotated_text(
                            str(content.get("text", "") or ""),
                            content.get("annotations", []) or [],
                            sources,
                            source_numbers,
                        )
                        if text:
                            visible.append(text)
                    elif content_type == "refusal":
                        refusal = content.get("refusal") or content.get("text") or ""
                        if refusal:
                            visible.append("### Refusal\n\n" + str(refusal).strip())
                    elif content_type:
                        visible.append(
                            "**OpenAI output: "
                            + content_type.replace("_", " ").capitalize()
                            + "**"
                        )
                continue

            if item_type == "function_call":
                status = item.get("status")
                if not cls._function_call_is_executable(item):
                    name = " ".join(str(item.get("name", "") or "").split())
                    notice = "**OpenAI output: Function call"
                    if name:
                        notice += " " + name
                    notice += "** — " + str(status or "not executable locally")
                    if status == "completed":
                        notice += "; not executable locally"
                    visible.append(notice)
                    continue
                arguments = item.get("arguments", "")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments else {}
                    except json.JSONDecodeError:
                        pass
                tool_call = {
                    "tool": item.get("name", ""),
                    "arguments": arguments,
                }
                if item.get("call_id"):
                    tool_call["id"] = item["call_id"]
                visible.append("```json\n" + json.dumps(tool_call) + "\n```")
                continue

            # Never expose arbitrary nested output (which may be encrypted or huge).
            label = item_type.replace("_", " ").capitalize()
            status = item.get("status")
            notice = f"**OpenAI output: {label}**"
            if isinstance(status, str) and status:
                notice += " — " + status
            visible.append(notice)

        if fallback_text and not saw_text:
            visible.insert(0, fallback_text.strip())
        if reasoning:
            visible.insert(0, "<think>" + "\n\n".join(reasoning) + "</think>")
        if sources:
            source_lines = [f"[{number}] {source}" for number, source in sources]
            visible.append("## Sources\n" + "\n".join(source_lines))
        return "\n\n".join(part for part in visible if part).strip()

    @classmethod
    def _response_usage(cls, response) -> dict:
        usage = cls._value(response, "usage")
        result = {}
        for target, fields in {
            "input_tokens": ("input_tokens", "prompt_tokens"),
            "output_tokens": ("output_tokens", "completion_tokens"),
            "total_tokens": ("total_tokens",),
        }.items():
            for field in fields:
                value = cls._value(usage, field)
                if value is not None:
                    result[target] = value
                    break
        for fields, counts in (
            (("input_tokens_details", "prompt_tokens_details"), {
                "cached_tokens": "cache_read_tokens", "audio_tokens": "input_audio_tokens",
            }),
            (("output_tokens_details", "completion_tokens_details"), {
                "reasoning_tokens": "reasoning_tokens", "audio_tokens": "output_audio_tokens",
                "accepted_prediction_tokens": "accepted_prediction_tokens",
                "rejected_prediction_tokens": "rejected_prediction_tokens",
            }),
        ):
            for field in fields:
                details = cls._value(usage, field)
                for source, target in counts.items():
                    value = cls._value(details, source)
                    if value is not None:
                        result[target] = value
        return result

    def _response_metadata(
        self,
        response,
        input_items: list,
        message: str,
        output: list,
        requested_store: bool,
    ) -> dict:
        response_store = self._value(response, "store", requested_store)
        if not isinstance(response_store, bool):
            response_store = requested_store
        return {
            "version": self.RESPONSE_STATE_VERSION,
            "id": self._value(response, "id"),
            "model": self.get_setting("model"),
            "endpoint": self.get_setting("endpoint"),
            "store": response_store,
            "input_hash": self._json_hash(input_items),
            "message_hash": self._message_hash(message),
            "output": self._plain(output),
        }

    @staticmethod
    def _invalid_previous_response(error: Exception) -> bool:
        status = getattr(error, "status_code", None)
        param = getattr(error, "param", None)
        message = str(error).lower()
        return status in (400, 404) and (
            param == "previous_response_id"
            or "previous_response_id" in message
            or "previous response" in message
        )

    def _create_response(self, client, kwargs: dict, full_input: list, anchor, store: bool):
        request = kwargs.copy()
        if anchor is not None and store:
            state, response_end = anchor
            request["previous_response_id"] = state["id"]
            request["input"] = full_input[response_end:]
            try:
                return client.responses.create(**request)
            except Exception as error:
                if not self._invalid_previous_response(error):
                    raise
        request.pop("previous_response_id", None)
        request["input"] = full_input
        return client.responses.create(**request)

    def _consume_responses_stream(
        self,
        response,
        full_input: list,
        on_update: Callable[[str], Any],
        extra_args: list,
        store: bool,
    ) -> str:
        text = ""
        refusal = ""
        reasoning_summary = ""
        reasoning_text = ""
        previous_preview = ""
        completed_response = None
        completed_items = {}
        cancelled = False

        def preview() -> str:
            parts = []
            reasoning = "\n\n".join(
                part for part in (reasoning_summary, reasoning_text) if part
            )
            if reasoning:
                parts.append("<think>" + reasoning + "</think>")
            if text:
                parts.append(text)
            if refusal:
                parts.append("### Refusal\n\n" + refusal)
            return "\n\n".join(parts).strip()

        for event in response:
            if not self.running:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                cancelled = True
                break

            event_type = self._value(event, "type", "")
            if event_type == "response.output_text.delta":
                text += str(self._value(event, "delta", "") or "")
            elif event_type == "response.refusal.delta":
                refusal += str(self._value(event, "delta", "") or "")
            elif event_type == "response.reasoning_summary_text.delta":
                reasoning_summary += str(self._value(event, "delta", "") or "")
            elif event_type == "response.reasoning_text.delta":
                reasoning_text += str(self._value(event, "delta", "") or "")
            elif event_type == "response.output_item.done":
                index = self._value(event, "output_index", len(completed_items))
                completed_items[index] = self._plain(self._value(event, "item", {}))
            elif event_type in ("response.completed", "response.incomplete"):
                completed_response = self._value(event, "response")
            elif event_type == "response.failed":
                failed_response = self._value(event, "response", event)
                error = self._value(failed_response, "error", failed_response)
                message = self._value(error, "message", str(error))
                raise RuntimeError(str(message))
            elif event_type == "error":
                error = self._value(event, "error", event)
                message = self._value(error, "message", str(error))
                raise RuntimeError(str(message))
            else:
                continue

            current_preview = preview()
            if current_preview and (
                len(current_preview) - len(previous_preview) > 1
                or not current_preview.startswith(previous_preview)
            ):
                on_update(current_preview, *tuple(extra_args))
                previous_preview = current_preview

        if completed_response is not None:
            output = self._plain(self._value(completed_response, "output", []) or [])
            fallback = str(self._value(completed_response, "output_text", text) or text)
        else:
            output = [completed_items[index] for index in sorted(completed_items)]
            fallback = preview()

        content = self.format_responses_output(output, fallback)
        if content and content != previous_preview:
            on_update(content, *tuple(extra_args))
        if cancelled or completed_response is None:
            return LLMResponse(content)
        metadata = self._response_metadata(
            completed_response,
            full_input,
            content,
            output,
            store,
        )
        return LLMResponse(content, metadata, usage=self._response_usage(completed_response))

    @staticmethod
    def _encrypted_content_include(extra_body: dict) -> list:
        include = extra_body.pop("include", [])
        if isinstance(include, str):
            include = [include]
        elif not isinstance(include, list):
            include = []
        include = list(include)
        if "reasoning.encrypted_content" not in include:
            include.append("reasoning.encrypted_content")
        return include

    @staticmethod
    def _validate_responses_state_body(extra_body: dict) -> None:
        conflicts = [
            key for key in ("conversation", "previous_response_id")
            if extra_body.get(key) is not None
        ]
        if conflicts:
            raise ValueError(
                "Custom Responses state options are not supported with "
                "Newelle's editable local history: " + ", ".join(conflicts)
            )

    def get_advanced_params(self):
        from openai import NOT_GIVEN
        advanced_params = self.get_setting("advanced_params")
        if not advanced_params:
            return NOT_GIVEN, NOT_GIVEN, NOT_GIVEN, NOT_GIVEN
        top_p = self.get_setting("top-p")
        temperature = self.get_setting("temperature")
        presence_penalty = self.get_setting("presence-penalty")
        frequency_penalty = self.get_setting("frequency-penalty")
        return top_p, temperature, presence_penalty, frequency_penalty

    def get_thinking_params(self):
        thinking = self.get_setting("thinking", False)
        if not thinking:
            return {}
        thinking_effort = self.get_setting("thinking_effort", "medium")
        if self.uses_responses_api():
            return {"reasoning": {"effort": thinking_effort, "summary": "auto"}}
        return {"reasoning_effort": thinking_effort}

    # -- Optional thinking-effort API (input-bar control) ------------------ #
    # Bridges the chat input's effort selector to the existing thinking
    # machinery: selecting a level enables thinking and sets reasoning_effort,
    # "none" disables it. Levels mirror the thinking_effort combo above.
    #
    # The control only appears when the handler opts in via supports_thinking
    # (so we don't send the OpenAI-specific reasoning_effort parameter to
    # providers that don't understand it).
    _THINKING_LEVELS = (
        ("none", _("None")),
        ("minimal", _("Minimal")),
        ("low", _("Low")),
        ("medium", _("Medium")),
        ("high", _("High")),
        ("xhigh", _("Max")),
    )

    def supports_thinking(self) -> bool:
        """Whether this handler exposes the thinking-effort API to the UI.

        The OpenAI API tolerates ``reasoning_effort`` (it is honored by
        reasoning models and ignored otherwise), so the base OpenAIHandler
        exposes it. Subclasses backed by providers that reject unknown
        parameters override this to return ``False``.
        """
        return True

    def get_thinking_modes(self) -> list[tuple[str, str]] | None:
        if not self.supports_thinking():
            return None
        return list(self._THINKING_LEVELS)

    def get_thinking_mode(self) -> str:
        # When thinking is off the control should reflect "none".
        if not self.get_setting("thinking", False):
            return "none"
        return self.get_setting("thinking_effort", "medium")

    def set_thinking_mode(self, value: str):
        if value == "none":
            self.set_setting("thinking", False)
        else:
            self.set_setting("thinking", True)
            self.set_setting("thinking_effort", value)

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        from openai import OpenAI

        responses_api = self.uses_responses_api()
        native_tool_calling = self.get_setting("native_tool_calling", False, True)
        if native_tool_calling:
            tools_list, system_prompt = extract_tools_from_prompts(system_prompt)
        else:
            tools_list = None
            
        if prompt.startswith("[Tool"):
            user = "Console"
        else:
            user = "User"
        history.append({"User": user, "Message": prompt})
        if responses_api:
            full_input, anchor = self._build_responses_input(history)
        else:
            messages = self.convert_history(history, system_prompt)
            if native_tool_calling:
                messages = balance_native_tool_call_responses(messages)
        api = self.get_setting("api")
        if api == "":
            api = "nokey"
        
        client = OpenAI(
            api_key=api,
            base_url=self.get_setting("endpoint")
        )
        top_p, temperature, presence_penalty, frequency_penalty = self.get_advanced_params()
        thinking_params = self.get_thinking_params()
        extra_body = self.get_extra_body()
        extra_body.update(thinking_params)

        try:
            kwargs = {
                "model": self.get_setting("model"),
                "top_p": top_p,
                "temperature": temperature,
                "extra_body": extra_body,
                "extra_headers": self.get_extra_headers(),
            }
            if responses_api:
                self._validate_responses_state_body(extra_body)
                store = extra_body.get("store", True) is not False
                kwargs["include"] = self._encrypted_content_include(extra_body)
                if system_prompt:
                    kwargs["instructions"] = "\n".join(system_prompt)
                if tools_list:
                    kwargs["tools"] = self.convert_responses_tools(tools_list)
                response = self._create_response(
                    client,
                    kwargs,
                    full_input,
                    anchor,
                    store,
                )
                output = self._plain(self._value(response, "output", []) or [])
                content = self.format_responses_output(
                    output,
                    str(self._value(response, "output_text", "") or ""),
                )
                metadata = self._response_metadata(
                    response,
                    full_input,
                    content,
                    output,
                    store,
                )
                return LLMResponse(content.strip(), metadata, usage=self._response_usage(response))
            else:
                if self.supports_audio():
                    kwargs["modalities"] = ["text"]
                kwargs["messages"] = messages
                kwargs["presence_penalty"] = presence_penalty
                kwargs["frequency_penalty"] = frequency_penalty
                if tools_list:
                    kwargs["tools"] = tools_list
                response = client.chat.completions.create(**kwargs)
                if not hasattr(response, "choices") or response.choices is None or len(response.choices) == 0:
                    raise Exception(str(response))

                content = response.choices[0].message.content or ""
                if hasattr(response.choices[0].message, "tool_calls") and response.choices[0].message.tool_calls is not None:
                    for tool_call in response.choices[0].message.tool_calls:
                        tool = tool_call.function
                        try:
                            args = json.loads(tool.arguments) if tool.arguments else {}
                        except (json.JSONDecodeError, TypeError, ValueError):
                            args = {}
                        if not isinstance(args, dict):
                            args = {}
                        tool_call_dict = {"tool": tool.name, "arguments": args}
                        tc_id = getattr(tool_call, "id", None)
                        if tc_id:
                            tool_call_dict["id"] = tc_id
                        content += "```json\n" + json.dumps(tool_call_dict) + "\n```\n"

            return LLMResponse(content.strip(), usage=self._response_usage(response))
        except Exception as e:
            raise e
    
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        self.running = True
        from openai import OpenAI

        responses_api = self.uses_responses_api()
        native_tool_calling = self.get_setting("native_tool_calling", False, True)
        if native_tool_calling:
            tools_list, system_prompt = extract_tools_from_prompts(system_prompt)
        else:
            tools_list = None
            
        if prompt.startswith("[Tool"):
            user = "Console"
        else:
            user = "User"
        history.append({"User": user, "Message": prompt})
        if responses_api:
            full_input, anchor = self._build_responses_input(history)
        else:
            messages = self.convert_history(history, system_prompt)
            if native_tool_calling:
                messages = balance_native_tool_call_responses(messages)
        api = self.get_setting("api")
        if api == "":
            api = "nokey"
        client = OpenAI(
            api_key=api,
            base_url=self.get_setting("endpoint")
        )
        top_p, temperature, presence_penalty, frequency_penalty = self.get_advanced_params()
        thinking_params = self.get_thinking_params()
        extra_body = self.get_extra_body()
        extra_body.update(thinking_params)

        try:
            kwargs = {
                "model": self.get_setting("model"),
                "top_p": top_p,
                "temperature": temperature,
                "stream": True,
                "extra_headers": self.get_extra_headers(),
                "extra_body": extra_body,
            }
            if responses_api:
                self._validate_responses_state_body(extra_body)
                store = extra_body.get("store", True) is not False
                kwargs["include"] = self._encrypted_content_include(extra_body)
                if system_prompt:
                    kwargs["instructions"] = "\n".join(system_prompt)
                if tools_list:
                    kwargs["tools"] = self.convert_responses_tools(tools_list)
                response = self._create_response(
                    client,
                    kwargs,
                    full_input,
                    anchor,
                    store,
                )
                return self._consume_responses_stream(
                    response,
                    full_input,
                    on_update,
                    extra_args,
                    store,
                )
            else:
                if self.supports_audio():
                    kwargs["modalities"] = ["text"]
                kwargs["messages"] = messages
                kwargs["presence_penalty"] = presence_penalty
                kwargs["frequency_penalty"] = frequency_penalty
                if tools_list:
                    kwargs["tools"] = tools_list
                kwargs["stream_options"] = {"include_usage": True}
                response = client.chat.completions.create(**kwargs)
            full_message = ""
            prev_message = ""
            is_reasoning = False
            # Track ongoing tool calls
            tool_calls = {}
            usage = {}

            for chunk in response:
                if not self.running:
                    response.close()
                    break
                usage.update(self._response_usage(chunk))
                if len(chunk.choices) == 0:
                    continue
                
                delta = chunk.choices[0].delta
                if delta.content:
                    if is_reasoning:
                        full_message += "</think>\n"
                        is_reasoning = False
                    full_message += delta.content
                    args = (full_message.strip(), ) + tuple(extra_args)
                    if len(full_message) - len(prev_message) > 1:
                        on_update(*args)
                        prev_message = full_message
                elif hasattr(delta, "reasoning") and delta.reasoning is not None:
                    if not is_reasoning:
                        full_message += "<think>"
                    is_reasoning = True
                    full_message += delta.reasoning
                    if len(full_message) - len(prev_message) > 1:
                        args = (full_message.strip(), ) + tuple(extra_args)
                        on_update(*args)
                        prev_message = full_message
                elif hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
                    if not is_reasoning:
                        full_message += "<think>"
                    is_reasoning = True
                    full_message += delta.reasoning_content
                    if len(full_message) - len(prev_message) > 1:
                        args = (full_message.strip(), ) + tuple(extra_args)
                        on_update(*args)
                        prev_message = full_message
                elif hasattr(delta, "tool_calls") and delta.tool_calls is not None:
                    if is_reasoning:
                        full_message += "</think>"
                        is_reasoning = False
                    
                    for tc_delta in delta.tool_calls:
                        if tc_delta.index not in tool_calls:
                            tool_calls[tc_delta.index] = {"name": "", "arguments": "", "id": ""}

                        if getattr(tc_delta, "id", None):
                            tool_calls[tc_delta.index]["id"] += tc_delta.id
                        if tc_delta.function.name:
                            tool_calls[tc_delta.index]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls[tc_delta.index]["arguments"] += tc_delta.function.arguments
            
            # After stream finishes, append any tool calls to full_message
            if tool_calls:
                if is_reasoning:
                    full_message += "</think>"
                for index in sorted(tool_calls.keys()):
                    tc = tool_calls[index]
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except (json.JSONDecodeError, TypeError, ValueError):
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    tool_call_dict = {"tool": tc["name"], "arguments": args}
                    tid = (tc.get("id") or "").strip()
                    if tid:
                        tool_call_dict["id"] = tid
                    full_message += "\n```json\n" + json.dumps(tool_call_dict) + "\n```\n"
            
            return LLMResponse(full_message.strip(), usage=usage or None)
        except Exception as e:
            raise e

    def get_extra_body(self):
        body = self.get_setting("custom_body")
        if body is not None:
            try:
                j = json.loads(body)
                return j
            except Exception as e:
                print("Wrong custom body")
                self.throw("Wrong custom body given to OpenAI LLM Handler, ignoring")
                return {}
        return {}

    def get_extra_headers(self):
        headers = self.get_setting("custom_headers")
        if headers is not None:
            try:
                j = json.loads(headers)
                if isinstance(j, dict):
                    return j
            except Exception as e:
                print("Wrong custom headers")
                self.throw("Wrong custom headers given to OpenAI LLM Handler, ignoring")
        return {}

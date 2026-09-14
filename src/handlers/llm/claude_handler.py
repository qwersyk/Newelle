import base64
import gettext
import hashlib
import json
import re
import threading
from collections.abc import Callable
from typing import Any

from ...handlers import ExtraSettings
from ...utility import (
    VOID_TOOL_RESULT_PLACEHOLDER,
    LLMResponse,
    extract_tools_from_prompts,
    get_streaming_extra_setting,
    parse_assistant_native_tool_calls,
    parse_tool_console_message,
)
from ...utility.media import extract_file, extract_image, get_image_base64
from .llm import LLMHandler

_ = gettext.gettext


class ClaudeHandler(LLMHandler):
    key = "claude"
    default_models = (
        ("Claude Sonnet 5", "claude-sonnet-5"),
        ("Claude Opus 5", "claude-opus-5"),
        ("Claude Fable 5", "claude-fable-5"),
        ("Claude Haiku 4.5", "claude-haiku-4-5"),
    )

    RESPONSE_STATE_VERSION = 1
    RESPONSE_STATE_PROVIDER = "anthropic"
    RESPONSE_STATE_KEY = "OpenAIResponse"
    _THINKING_LEVELS = (
        ("none", _("None")),
        ("low", _("Low")),
        ("medium", _("Medium")),
        ("high", _("High")),
    )

    def __init__(self, settings, path):
        super().__init__(settings, path)
        models = self.get_setting("models", False)
        if models is None or len(models) == 0:
            self.models = self.default_models
            threading.Thread(target=self.get_models, args=()).start()
        else:
            self.models = models

    def get_supported_files(self) -> list[str]:
        return ["*.pdf"]

    def supports_vision(self) -> bool:
        return True

    def get_models_list(self):
        return self.models

    def _selected_model(self) -> str:
        model = self.get_setting("model", False)
        if model:
            return str(model)
        if self.get_setting("custom_model", False):
            return ""
        if self.models:
            return str(self.models[0][1])
        return ""

    @classmethod
    def _thinking_capability(cls, model: str | None = None) -> str | None:
        """Return ``adaptive``, ``always`` or ``None`` for a model ID.

        Claude 4.5 and earlier only support the legacy budget-based thinking
        API. This handler intentionally exposes the modern adaptive API only,
        so unknown and legacy model IDs are treated as unsupported.
        """
        model_id = str(model or "").lower()
        if not model_id or not model_id.startswith("claude-"):
            return None

        if re.search(r"^claude-(?:fable|mythos)(?:[-_.]|$)", model_id):
            return "always"

        match = re.search(r"(?:sonnet|opus)[-_.](\d+)(?:[-_.](\d+))?", model_id)
        if match is None:
            match = re.search(r"[-_.](\d+)[-_.](\d+)[-_.](?:sonnet|opus)", model_id)
        if not match:
            return None
        major = int(match.group(1))
        minor = int(match.group(2) or 0)
        if major >= 5 or (major == 4 and minor >= 6):
            return "adaptive"
        return None

    def get_thinking_modes(self) -> list[tuple[str, str]] | None:
        capability = self._thinking_capability(self._selected_model())
        if capability == "always":
            return list(self._THINKING_LEVELS[1:])
        # Keep the selector available for legacy and custom model IDs too.
        # _thinking_params() still suppresses unsupported request fields.
        return list(self._THINKING_LEVELS)

    def get_thinking_mode(self) -> str:
        modes = self.get_thinking_modes()
        if not modes:
            return ""
        capability = self._thinking_capability(self._selected_model())
        if capability == "always":
            value = self.get_setting("thinking_effort", False)
            return value if value in {mode[0] for mode in modes} else "medium"
        if not self.get_setting("thinking", False):
            return "none"
        value = self.get_setting("thinking_effort", False)
        return value if value in {mode[0] for mode in modes} else "medium"

    def set_thinking_mode(self, value: str):
        modes = self.get_thinking_modes()
        allowed = {mode[0] for mode in modes or ()}
        if value not in allowed:
            return
        if value == "none":
            self.set_setting("thinking", False)
            return
        if self._thinking_capability(self._selected_model()) == "always":
            self.set_setting("thinking_effort", value)
            return
        self.set_setting("thinking", True)
        self.set_setting("thinking_effort", value)

    def get_extra_settings(self) -> list:
        settings = [
            ExtraSettings.EntrySetting("api", _("API Key"), _("The API key to use"), "", password=True),
            ExtraSettings.EntrySetting(
                "endpoint",
                _("API Endpoint"),
                _("API base URL for Anthropic-compatible services"),
                "https://api.anthropic.com",
            ),
            ExtraSettings.ToggleSetting(
                "custom_model",
                _("Use a custom model"),
                _("Use a custom model"),
                False,
                update_settings=True,
            ),
        ]
        if self.get_setting("custom_model", False):
            settings.append(
                ExtraSettings.EntrySetting(
                    "model",
                    _("Model"),
                    _("The model to use"),
                    "",
                    update_settings=True,
                )
            )
        else:
            default_model = self.models[0][1] if self.models else ""
            settings.append(
                ExtraSettings.ComboSetting(
                    "model",
                    _("Model"),
                    _("The model to use"),
                    self.models,
                    default_model,
                    update_settings=True,
                    refresh=lambda _button: self.get_models(),
                )
            )

        thinking_modes = self.get_thinking_modes()
        if thinking_modes:
            capability = self._thinking_capability(self._selected_model())
            if capability != "always":
                settings.append(
                    ExtraSettings.ToggleSetting(
                        "thinking",
                        _("Thinking Mode"),
                        _("Enable adaptive thinking for the model"),
                        False,
                        update_settings=True,
                    )
                )
                if self.get_setting("thinking", False):
                    settings.append(
                        ExtraSettings.ComboSetting(
                            "thinking_effort",
                            _("Reasoning Effort"),
                            _("How much reasoning effort to allocate"),
                            self._THINKING_LEVELS,
                            "medium",
                        )
                    )
            else:
                settings.append(
                    ExtraSettings.ComboSetting(
                        "thinking_effort",
                        _("Reasoning Effort"),
                        _("How much reasoning effort to allocate"),
                        thinking_modes,
                        "medium",
                    )
                )

        settings += [
            ExtraSettings.ToggleSetting(
                "native_tool_calling",
                _("Native Tool Calling"),
                _(
                    "Enable Anthropic's native tool calling. Disable this only "
                    "if the model or endpoint does not support it."
                ),
                True,
            ),
            ExtraSettings.ScaleSetting(
                "max_tokens",
                _("Max Tokens"),
                _("The maximum number of tokens to generate"),
                8192,
                1024,
                128000,
                0,
            ),
            get_streaming_extra_setting(),
        ]
        return settings

    @staticmethod
    def _value(value: object, key: str, default=None):
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @classmethod
    def _plain(cls, value):
        """Return a JSON-safe representation of an Anthropic SDK object."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "model_dump"):
            try:
                return cls._plain(value.model_dump(mode="json"))
            except TypeError:
                return cls._plain(value.model_dump())
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
    def _as_content_list(content) -> list:
        if isinstance(content, list):
            return list(content)
        if isinstance(content, dict):
            return [content]
        if content is None or content == "":
            return []
        return [{"type": "text", "text": str(content)}]

    @classmethod
    def _append_message(cls, messages: list[dict], role: str, content):
        content_list = cls._as_content_list(content)
        if not content_list and content == "":
            content_list = [{"type": "text", "text": ""}]
        if not content_list:
            return
        if messages and messages[-1]["role"] == role:
            merged = cls._as_content_list(messages[-1]["content"]) + content_list
            if role == "user":
                tool_results = [block for block in merged if cls._value(block, "type") == "tool_result"]
                other_blocks = [block for block in merged if cls._value(block, "type") != "tool_result"]
                merged = tool_results + other_blocks
            messages[-1]["content"] = merged
        else:
            messages.append({"role": role, "content": content_list})

    @staticmethod
    def _media_content(message: str) -> list[dict] | None:
        image, text = extract_image(message)
        if image is not None:
            content = []
            if text:
                content.append({"type": "text", "text": text})
            b64 = get_image_base64(image)
            media_type, encoded = b64.split(";", 1)
            media_type = media_type.split(":", 1)[1]
            encoded = encoded.split(",", 1)[1]
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": encoded},
                }
            )
            return content

        file_path, text = extract_file(message)
        if file_path is not None:
            content = []
            if text:
                content.append({"type": "text", "text": text})
            with open(file_path, "rb") as pdf_file:
                pdf_data = base64.standard_b64encode(pdf_file.read()).decode("utf-8")
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_data,
                    },
                }
            )
            return content
        return None

    @classmethod
    def _tool_blocks_from_calls(cls, tool_calls: list[dict]) -> list[dict]:
        blocks = []
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    arguments = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(tool_call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "input": arguments if isinstance(arguments, dict) else {},
                }
            )
        return blocks

    @classmethod
    def _message_hash(cls, message: str | dict) -> str:
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
                text = text[: match.start()] + text[match.end() :]
        return hashlib.sha256(
            json.dumps(
                {"text": text.strip(), "reasoning": str(reasoning).strip() if reasoning is not None else None},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _metadata_state(cls, message: dict | None, model: str, endpoint: str) -> dict | None:
        if not isinstance(message, dict):
            return None
        state = message.get(cls.RESPONSE_STATE_KEY)
        if not isinstance(state, dict):
            return None
        if (
            state.get("provider") != cls.RESPONSE_STATE_PROVIDER
            or state.get("version") != cls.RESPONSE_STATE_VERSION
            or state.get("model") != model
            or state.get("endpoint") != endpoint
            or not isinstance(state.get("content"), list)
            or state.get("message_hash") != cls._message_hash(message)
        ):
            return None
        normalized = dict(state)
        normalized["content"] = cls._input_content_blocks(state["content"])
        return normalized

    @classmethod
    def _assistant_content(cls, history: list[dict], index: int, native_tool_calling: bool) -> list[dict]:
        message = history[index]
        text = str(message.get("Message", "") or "")
        if native_tool_calling:
            following_console = []
            for following_index, item in enumerate(history[index + 1 :], start=index + 1):
                if item.get("User") == "Console":
                    following_console.append((following_index, item))
                elif item.get("User") == "User" and item.get("ToolContext"):
                    continue
                else:
                    break
            parsed = parse_assistant_native_tool_calls(text, following_console, arguments_as_json_string=True)
            if parsed is not None:
                text_part, tool_calls, _used = parsed
                blocks = []
                if text_part:
                    blocks.append({"type": "text", "text": text_part})
                blocks.extend(cls._tool_blocks_from_calls(tool_calls))
                return blocks
        return [{"type": "text", "text": text}] if text else []

    @classmethod
    def _balance_tool_results(cls, messages: list[dict]) -> list[dict]:
        """Ensure every assistant tool_use has an immediate user tool_result."""
        result = []
        index = 0
        while index < len(messages):
            message = messages[index]
            result.append(message)
            if message.get("role") != "assistant":
                index += 1
                continue
            assistant_content = cls._as_content_list(message.get("content"))
            expected = [
                str(cls._value(block, "id") or "")
                for block in assistant_content
                if cls._value(block, "type") == "tool_use"
            ]
            if not expected:
                index += 1
                continue

            next_index = index + 1
            if next_index < len(messages) and messages[next_index].get("role") == "user":
                user_message = messages[next_index]
                content = cls._as_content_list(user_message.get("content"))
                existing = {
                    str(cls._value(block, "tool_use_id") or ""): block
                    for block in content
                    if cls._value(block, "type") == "tool_result"
                }
                ordered_results = []
                for tool_id in expected:
                    ordered_results.append(
                        existing.pop(tool_id, None)
                        or {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": VOID_TOOL_RESULT_PLACEHOLDER,
                        }
                    )
                remaining = [
                    block
                    for block in content
                    if cls._value(block, "type") != "tool_result"
                    and str(cls._value(block, "tool_use_id") or "") not in existing
                ]
                result.append({"role": "user", "content": ordered_results + remaining})
                index += 2
                continue

            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": VOID_TOOL_RESULT_PLACEHOLDER,
                        }
                        for tool_id in expected
                    ],
                }
            )
            index += 1
        return result

    def convert_history(self, history, native_tool_calling: bool | None = None) -> list:
        if native_tool_calling is None:
            native_tool_calling = self.get_setting("native_tool_calling", False, True)

        messages: list[dict] = []
        model = self._selected_model()
        endpoint = str(self.get_setting("endpoint") or "")
        for index, message in enumerate(history):
            user = message.get("User")
            raw_message = str(message.get("Message", "") or "")
            if user == "Console":
                parsed = parse_tool_console_message(raw_message) if native_tool_calling else None
                if parsed is not None:
                    _tool_name, tool_id, tool_content = parsed
                    self._append_message(
                        messages,
                        "user",
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": tool_content or VOID_TOOL_RESULT_PLACEHOLDER,
                        },
                    )
                else:
                    self._append_message(messages, "user", "Console: " + raw_message)
                continue

            if user == "Assistant":
                state = self._metadata_state(message, model, endpoint)
                if state is not None and (
                    native_tool_calling
                    or not any(self._value(block, "type") == "tool_use" for block in state["content"])
                ):
                    self._append_message(messages, "assistant", state["content"])
                else:
                    self._append_message(
                        messages,
                        "assistant",
                        self._assistant_content(history, index, bool(native_tool_calling)),
                    )
                continue

            media = self._media_content(raw_message) if user == "User" else None
            self._append_message(messages, "user", media or raw_message)

        if native_tool_calling:
            messages = self._balance_tool_results(messages)
        return messages

    # ------------------------------------------------------------------
    # Anthropic requests and response formatting
    # ------------------------------------------------------------------
    @staticmethod
    def _anthropic_tools(tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        converted = []
        for tool in tools:
            function = tool.get("function", tool)
            converted.append(
                {
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "input_schema": function.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        return converted

    def _thinking_params(self) -> dict:
        capability = self._thinking_capability(self._selected_model())
        if capability is None:
            return {}
        effort = self.get_thinking_mode() or "medium"
        if capability == "always":
            return {
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": effort},
            }
        if effort == "none":
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": effort},
        }

    def _request_kwargs(self, prompt: str, history: list[dict], system_prompt: list[str]) -> dict:
        native_tool_calling = self.get_setting("native_tool_calling", False, True)
        tools = None
        if native_tool_calling:
            tools, system_prompt = extract_tools_from_prompts(system_prompt)

        role = "Console" if prompt.startswith("[Tool:") else "User"
        history.append({"User": role, "Message": prompt})
        messages = self.convert_history(history, native_tool_calling=native_tool_calling)
        kwargs = {
            "max_tokens": int(self.get_setting("max_tokens")),
            "model": self._selected_model(),
            "messages": messages,
            "system": "\n".join(system_prompt),
        }
        anthropic_tools = self._anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        kwargs.update(self._thinking_params())
        return kwargs

    @classmethod
    def _format_content(cls, content: list) -> str:
        parts = []
        thinking = []

        def flush_thinking():
            if thinking:
                parts.append("<think>" + "\n\n".join(thinking) + "</think>\n")
                thinking.clear()

        for block in content or []:
            block_type = cls._value(block, "type", "")
            if block_type == "thinking":
                text = str(cls._value(block, "thinking", "") or "")
                if text:
                    thinking.append(text)
                continue
            if block_type == "redacted_thinking":
                continue
            if block_type == "text":
                text = str(cls._value(block, "text", "") or "")
                flush_thinking()
                parts.append(text)
                continue
            if block_type == "tool_use":
                flush_thinking()
                parts.append(
                    "\n```json\n"
                    + json.dumps(
                        {
                            "tool": cls._value(block, "name", ""),
                            "arguments": cls._value(block, "input", {}) or {},
                            "id": cls._value(block, "id", ""),
                        },
                        ensure_ascii=False,
                    )
                    + "\n```\n"
                )
        flush_thinking()
        return "".join(parts).strip()

    @classmethod
    def _input_content_blocks(cls, content: list) -> list[dict]:
        """Keep only fields accepted when an assistant turn is replayed."""
        blocks = []
        for block in content or []:
            block_type = cls._value(block, "type", "")
            if block_type == "thinking":
                thinking = {
                    "type": "thinking",
                    "thinking": cls._value(block, "thinking", "") or "",
                }
                signature = cls._value(block, "signature")
                if signature is not None:
                    thinking["signature"] = signature
                blocks.append(thinking)
            elif block_type == "redacted_thinking":
                blocks.append(
                    {
                        "type": "redacted_thinking",
                        "data": cls._value(block, "data", "") or "",
                    }
                )
            elif block_type == "text":
                blocks.append({"type": "text", "text": cls._value(block, "text", "") or ""})
            elif block_type == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": cls._value(block, "id", "") or "",
                        "name": cls._value(block, "name", "") or "",
                        "input": cls._value(block, "input", {}) or {},
                    }
                )
            else:
                plain = cls._plain(block)
                if isinstance(plain, dict) and block_type:
                    blocks.append(plain)
        return blocks

    def _response_metadata(self, response, formatted: str) -> dict:
        content = self._input_content_blocks(self._value(response, "content", []) or [])
        return {
            "provider": self.RESPONSE_STATE_PROVIDER,
            "version": self.RESPONSE_STATE_VERSION,
            "model": self._selected_model(),
            "endpoint": str(self.get_setting("endpoint") or ""),
            "message_hash": self._message_hash(formatted),
            "content": content,
        }

    def _response_text(self, response) -> LLMResponse:
        formatted = self._format_content(self._value(response, "content", []) or [])
        provider_usage = self._value(response, "usage")
        usage = {}
        for source, target in {
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "cache_read_input_tokens": "cache_read_tokens",
            "cache_creation_input_tokens": "cache_write_tokens",
        }.items():
            value = self._value(provider_usage, source)
            if value is not None:
                usage[target] = value
        # Anthropic reports uncached input separately from cache reads/writes.
        if "input_tokens" in usage:
            usage["input_tokens"] += usage.get("cache_read_tokens", 0) + usage.get("cache_write_tokens", 0)
        if "input_tokens" in usage and "output_tokens" in usage:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        return LLMResponse(formatted, self._response_metadata(response, formatted), usage=usage or None)

    def _stream_update(self, text: str, previous: str, on_update: Callable, extra_args: list) -> str:
        if len(text) - len(previous) > 1:
            on_update(*(text.strip(), *extra_args))
            return text
        return previous

    def generate_text(
        self,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: list[str] | None = None,
    ) -> str:
        history = [] if history is None else history
        system_prompt = [] if system_prompt is None else system_prompt
        client = self._get_client()
        kwargs = self._request_kwargs(prompt, history, system_prompt)
        response = client.messages.create(**kwargs)
        return self._response_text(response)

    def generate_text_stream(
        self,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        system_prompt: list[str] | None = None,
        on_update: Callable[[str], Any] = lambda _: None,
        extra_args: list | None = None,
    ) -> str:
        history = [] if history is None else history
        system_prompt = [] if system_prompt is None else system_prompt
        extra_args = [] if extra_args is None else extra_args
        self.running = True
        client = self._get_client()
        kwargs = self._request_kwargs(prompt, history, system_prompt)

        with client.messages.stream(**kwargs) as stream:
            visible = ""
            previous = ""
            thinking_open = False
            final_response = None
            streamed_content = []
            streamed_usage = {}
            current_content_index = None
            current_input_json = None

            def ensure_stream_block(block_type: str) -> dict:
                nonlocal current_content_index
                if current_content_index is None or current_content_index >= len(streamed_content):
                    streamed_content.append({"type": block_type})
                    current_content_index = len(streamed_content) - 1
                return streamed_content[current_content_index]

            for event in stream:
                if not self.running:
                    stream.close()
                    break
                event_type = self._value(event, "type", "")
                if event_type in {"message_start", "message_delta"}:
                    source = self._value(event, "message") if event_type == "message_start" else event
                    event_usage = self._plain(self._value(source, "usage", {}))
                    if isinstance(event_usage, dict):
                        streamed_usage.update({key: value for key, value in event_usage.items() if value is not None})
                if event_type == "message_stop":
                    final_response = self._value(event, "message")
                elif event_type == "content_block_start":
                    block = self._plain(self._value(event, "content_block", {}))
                    block_type = self._value(block, "type", "")
                    if block_type in {"text", "tool_use"} and thinking_open:
                        visible += "</think>\n"
                        thinking_open = False
                    if block_type == "text":
                        block = {"type": "text", "text": self._value(block, "text", "") or ""}
                    elif block_type == "thinking":
                        signature = self._value(block, "signature")
                        block = {
                            "type": "thinking",
                            "thinking": self._value(block, "thinking", "") or "",
                        }
                        if signature is not None:
                            block["signature"] = signature
                    elif block_type == "redacted_thinking":
                        block = {
                            "type": "redacted_thinking",
                            "data": self._value(block, "data", "") or "",
                        }
                    elif block_type == "tool_use":
                        block = {
                            "type": "tool_use",
                            "id": self._value(block, "id", "") or "",
                            "name": self._value(block, "name", "") or "",
                            "input": self._value(block, "input", {}) or {},
                        }
                    if block_type:
                        streamed_content.append(block)
                        current_content_index = len(streamed_content) - 1
                        current_input_json = (
                            "" if block_type == "tool_use" and not block.get("input") else None
                        )
                elif event_type == "content_block_delta":
                    delta = self._value(event, "delta", {})
                    delta_type = self._value(delta, "type", "")
                    if delta_type == "thinking_delta":
                        block = ensure_stream_block("thinking")
                        block["thinking"] = str(block.get("thinking", "") or "") + str(
                            self._value(delta, "thinking", "") or ""
                        )
                        if not thinking_open:
                            visible += "<think>"
                            thinking_open = True
                        visible += str(self._value(delta, "thinking", "") or "")
                    elif delta_type == "text_delta":
                        block = ensure_stream_block("text")
                        block["text"] = str(block.get("text", "") or "") + str(
                            self._value(delta, "text", "") or ""
                        )
                        if thinking_open:
                            visible += "</think>\n"
                            thinking_open = False
                        visible += str(self._value(delta, "text", "") or "")
                    elif delta_type == "input_json_delta":
                        if current_input_json is None:
                            current_input_json = ""
                        current_input_json += str(self._value(delta, "partial_json", "") or "")
                    elif delta_type == "signature_delta":
                        block = ensure_stream_block("thinking")
                        block["signature"] = str(block.get("signature", "") or "") + str(
                            self._value(delta, "signature", "") or ""
                        )
                    elif delta_type == "redacted_thinking_delta":
                        block = ensure_stream_block("redacted_thinking")
                        block["data"] = str(block.get("data", "") or "") + str(
                            self._value(delta, "data", "") or ""
                        )
                elif event_type == "content_block_stop":
                    if current_input_json is not None and current_content_index is not None:
                        try:
                            streamed_content[current_content_index]["input"] = json.loads(current_input_json)
                        except (TypeError, ValueError):
                            streamed_content[current_content_index]["input"] = {}
                    current_content_index = None
                    current_input_json = None

                if event_type == "content_block_delta":
                    previous = self._stream_update(visible, previous, on_update, extra_args)

            if final_response is None and self.running:
                try:
                    final_response = stream.get_final_message()
                except (AttributeError, AssertionError, RuntimeError):
                    final_response = None
            if final_response is None and self.running and streamed_content:
                final_response = {"content": streamed_content, "usage": streamed_usage}

        if final_response is not None:
            result = self._response_text(final_response)
            final_text = str(result)
            if final_text != visible.strip():
                on_update(*(final_text, *extra_args))
            return result

        if thinking_open:
            visible += "</think>"
        return LLMResponse(visible.strip())

    @staticmethod
    def get_extra_requirements() -> list:
        return ["anthropic"]

    def _get_client(self):
        import anthropic

        return anthropic.Client(
            api_key=self.get_setting("api"),
            base_url=self.get_setting("endpoint"),
        )

    def get_models(self):
        if not self.is_installed() or self.get_setting("api", False) == "":
            return
        client = self._get_client()
        result = ()
        for model in client.models.list():
            result += ((model.display_name, model.id),)

        self.models = result
        self.set_setting("models", result)
        self.settings_update()

    def get_duplication_settings(self) -> list[dict] | None:
        # Anthropic-compatible subclasses inherit this implementation. Keep
        # duplication limited to the canonical Anthropic handler.
        if self.key != "claude":
            return None
        return [
            ExtraSettings.EntrySetting(
                "endpoint",
                _("API Endpoint"),
                _("API base URL for the Anthropic-compatible provider"),
                self.get_setting("endpoint"),
            )
        ]

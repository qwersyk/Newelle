import gettext
import json
import os
import threading
from typing import Any, Callable

_ = gettext.gettext

from .openai_handler import OpenAIHandler
from ...handlers import ExtraSettings, ErrorSeverity
from ...utility import LLMResponse, extract_tools_from_prompts, balance_native_tool_call_responses
from ...utility.pip import install_module

# Range the handler is tested against; get_extra_requirements keeps the bare
# module name because is_installed() looks requirements up as importable modules.
LITELLM_REQUIREMENT = "litellm>=1.97.0,<1.106.0"


class LiteLLMHandler(OpenAIHandler):
    """Calls 100+ providers through the LiteLLM SDK.

    Models use LiteLLM ids such as ``anthropic/claude-sonnet-4-6``,
    ``bedrock/us.anthropic.claude-sonnet-4-6-v1:0``, ``vertex_ai/gemini-2.5-pro``
    or ``ollama/llama3.1``. The API key and endpoint are optional: without them
    LiteLLM reads each provider's own environment variables.
    """
    key = "litellm"
    default_models = (("anthropic/claude-sonnet-4-6", "anthropic/claude-sonnet-4-6"), )

    @staticmethod
    def get_extra_requirements() -> list:
        return ["litellm"]

    def install(self):
        pip_path = os.path.join(os.path.abspath(os.path.join(self.path, os.pardir)), "pip")
        install_module(LITELLM_REQUIREMENT, pip_path)
        self._is_installed_cache = None

    def uses_responses_api(self) -> bool:
        return False

    def get_video_mode(self) -> str:
        return self.get_setting("video_mode", False, "frames")

    def get_models(self, manual=False, multithread=False):
        if not multithread:
            threading.Thread(target=self.get_models, args=(manual, True)).start()
            return
        if not self.is_installed():
            return
        try:
            import litellm
            # LiteLLM's bundled model map; no request is made, so this works
            # before any provider credential is configured.
            models = sorted(
                name for name, info in litellm.model_cost.items()
                if isinstance(info, dict) and info.get("mode") == "chat"
                # Commitment rows are provisioned-throughput price entries, not callable ids.
                and "/" in name and "*" not in name and "commitment" not in name
            )
            self.models = tuple((name, name) for name in models)
            self.set_setting("models", json.dumps(self.models))
            self.settings_update()
        except Exception as e:
            if manual:
                self.throw("Error getting LiteLLM models: " + str(e), ErrorSeverity.WARNING)
            print("Error getting LiteLLM models: " + str(e))

    def get_extra_settings(self) -> list:
        settings = self.build_extra_settings(
            "LiteLLM", True, True, False, True, True, None,
            "https://docs.litellm.ai/docs/providers", False, True, True,
            self.supports_thinking(), True,
        )
        settings.insert(1, ExtraSettings.EntrySetting(
            "endpoint", _("API Base"),
            _("Optional. Set for self-hosted or Azure deployments (api_base); leave empty to use the provider default"),
            "",
        ))
        settings[0]["description"] = _("Optional. Leave empty to use the provider's own environment variables (ANTHROPIC_API_KEY, AWS credentials, ...)")
        settings.append(ExtraSettings.ComboSetting(
            "video_mode", _("Video Input"),
            _("Send videos natively to models that accept them, or sample up to 16 frames using ffmpeg (without audio)."),
            ((_("Video URL"), "native"), (_("Sample Frames"), "frames")),
            self.get_video_mode(),
        ))
        return settings

    def get_duplication_settings(self) -> list[dict] | None:
        return None

    def _completion_kwargs(self, messages: list, tools_list: list | None) -> dict:
        kwargs = {
            "model": self.get_setting("model"),
            "messages": messages,
            # Drop OpenAI-only params (penalties, reasoning_effort, ...) that
            # the selected provider does not accept instead of failing.
            "drop_params": True,
        }
        api_key = self.get_setting("api")
        if api_key:
            kwargs["api_key"] = api_key
        api_base = self.get_setting("endpoint")
        if api_base:
            kwargs["api_base"] = api_base
        if self.get_setting("advanced_params"):
            kwargs["top_p"] = self.get_setting("top-p")
            kwargs["temperature"] = self.get_setting("temperature")
            kwargs["presence_penalty"] = self.get_setting("presence-penalty")
            kwargs["frequency_penalty"] = self.get_setting("frequency-penalty")
        kwargs.update(self.get_thinking_params())
        headers = self.get_extra_headers()
        if headers:
            kwargs["extra_headers"] = headers
        kwargs.update(self.get_extra_body())
        if tools_list:
            kwargs["tools"] = tools_list
        return kwargs

    def _prepare(self, prompt: str, history: list, system_prompt: list) -> tuple[list, list | None]:
        native_tool_calling = self.get_setting("native_tool_calling", False, True)
        if native_tool_calling:
            tools_list, system_prompt = extract_tools_from_prompts(system_prompt)
        else:
            tools_list = None
        user = "Console" if prompt.startswith("[Tool") else "User"
        history.append({"User": user, "Message": prompt})
        messages = self.convert_history(history, system_prompt)
        if native_tool_calling:
            messages = balance_native_tool_call_responses(messages)
        return messages, tools_list

    @staticmethod
    def _tool_call_block(name: str, arguments: str | None, call_id: str | None) -> str:
        try:
            args = json.loads(arguments) if arguments else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        tool_call = {"tool": name, "arguments": args}
        if call_id:
            tool_call["id"] = call_id
        return "```json\n" + json.dumps(tool_call) + "\n```\n"

    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        import litellm

        messages, tools_list = self._prepare(prompt, history, system_prompt)
        response = litellm.completion(**self._completion_kwargs(messages, tools_list))
        if not response.choices:
            raise Exception(str(response))
        message = response.choices[0].message
        content = message.content or ""
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            content = "<think>" + reasoning + "</think>\n" + content
        for tool_call in getattr(message, "tool_calls", None) or []:
            content += self._tool_call_block(tool_call.function.name, tool_call.function.arguments, getattr(tool_call, "id", None))
        return LLMResponse(content.strip(), usage=self._response_usage(response))

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        import litellm

        self.running = True
        messages, tools_list = self._prepare(prompt, history, system_prompt)
        kwargs = self._completion_kwargs(messages, tools_list)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        response = litellm.completion(**kwargs)

        full_message = ""
        prev_message = ""
        is_reasoning = False
        tool_calls = {}
        usage = {}
        for chunk in response:
            if not self.running:
                break
            usage.update(self._response_usage(chunk))
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None)
            if delta.content:
                if is_reasoning:
                    full_message += "</think>\n"
                    is_reasoning = False
                full_message += delta.content
            elif reasoning:
                if not is_reasoning:
                    full_message += "<think>"
                    is_reasoning = True
                full_message += reasoning
            elif getattr(delta, "tool_calls", None):
                if is_reasoning:
                    full_message += "</think>"
                    is_reasoning = False
                for tc_delta in delta.tool_calls:
                    call = tool_calls.setdefault(tc_delta.index or 0, {"name": "", "arguments": "", "id": ""})
                    if getattr(tc_delta, "id", None):
                        call["id"] = tc_delta.id
                    if tc_delta.function.name:
                        call["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        call["arguments"] += tc_delta.function.arguments
                continue
            else:
                continue
            if len(full_message) - len(prev_message) > 1:
                on_update(*((full_message.strip(), ) + tuple(extra_args)))
                prev_message = full_message

        if is_reasoning:
            full_message += "</think>"
        for index in sorted(tool_calls):
            call = tool_calls[index]
            full_message += "\n" + self._tool_call_block(call["name"], call["arguments"], call["id"].strip() or None)
        return LLMResponse(full_message.strip(), usage=usage or None)

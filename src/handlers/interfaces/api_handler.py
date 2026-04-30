import io
import json
import os
import queue
import tempfile
import threading
import time
import uuid

from ...utility import convert_messages_openai_to_newelle, parse_tool_calls_from_assistant_content
from ..extra_settings import ExtraSettings
from .interface import Interface


def _chat_completion_log_print(label: str, obj, max_chars: int = 8000) -> None:
    """Debug logging for the OpenAI-compatible chat completions endpoint (stdout)."""
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        text = repr(obj)
    orig_len = len(text)
    if orig_len > max_chars:
        text = text[:max_chars] + f"\n... [truncated, {orig_len} chars total]"
    print(f"[API chat/completions] {label}\n{text}")


class APIInterface(Interface):
    key = "api"
    name = "OpenAI Compatible API Server"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._server = None

    @staticmethod
    def get_extra_requirements() -> list:
        return ["fastapi", "uvicorn"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting(
                key="api_key",
                title=_("API Key"),
                description=_("API key required to authenticate requests (leave empty to disable authentication)"),
                default="",
                password=True,
            ),
            ExtraSettings.EntrySetting(
                key="host",
                title=_("Host"),
                description=_("Host address to bind the API server to"),
                default="127.0.0.1",
            ),
            ExtraSettings.SpinSetting(
                key="port",
                title=_("Port"),
                description=_("Port to bind the API server to"),
                default=8080,
                min=1,
                max=65535,
                step=1,
            ),
        ]

    def _get_port(self):
        return self.get_setting("port", search_default=True, return_value=8080)

    def _get_host(self):
        return self.get_setting("host", search_default=True, return_value="127.0.0.1")

    def _create_app(self):
        from fastapi import FastAPI, UploadFile, File, Request, HTTPException
        from fastapi.responses import JSONResponse, StreamingResponse
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
        from typing import Optional
        from starlette.middleware.base import BaseHTTPMiddleware

        controller = self.controller
        api_key = self.get_setting("api_key", search_default=True, return_value="")

        class APIKeyMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if api_key:
                    auth_header = request.headers.get("Authorization", "")
                    bearer = f"Bearer {api_key}"
                    api_key_param = request.query_params.get("api_key", "")
                    if auth_header != bearer and api_key_param != api_key:
                        return JSONResponse(
                            status_code=401,
                            content={"error": {"message": "Invalid or missing API key", "type": "authentication_error"}},
                        )
                return await call_next(request)

        class ChatMessage(BaseModel):
            model_config = {"extra": "ignore"}

            role: str
            content: Optional[str] = None
            tool_calls: Optional[list] = None
            tool_call_id: Optional[str] = None
            name: Optional[str] = None

        class ChatCompletionRequest(BaseModel):
            model: Optional[str] = None
            messages: list[ChatMessage]
            stream: Optional[bool] = False
            tools: Optional[list] = None

        class SpeechRequest(BaseModel):
            model: Optional[str] = None
            input: str
            voice: Optional[str] = None
            response_format: Optional[str] = "mp3"
            stream: Optional[bool] = False

        app = FastAPI()
        app.add_middleware(APIKeyMiddleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/v1/models")
        def list_models():
            llm = controller.handlers.llm
            models = llm.get_models_list() if hasattr(llm, "get_models_list") else ()
            model_list = []
            for m in models:
                model_list.append({
                    "id": m[0],
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "newelle",
                })
            if not model_list:
                model_list.append({
                    "id": llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "newelle",
                })
            return {"object": "list", "data": model_list}

        def embed_openai_tools_in_system_prompt(system_prompt: list, tools: Optional[list]) -> None:
            """Prepend <tools>...</tools> so extract_tools_from_prompts picks them up."""
            if not tools:
                return
            normalized = []
            for t in tools:
                if not isinstance(t, dict):
                    continue
                if t.get("type") != "function":
                    continue
                fn = t.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                tool_name = fn.get("name")
                if not tool_name:
                    continue
                normalized.append({
                    "name": tool_name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            if not normalized:
                return
            system_prompt.insert(0, f"<tools>{json.dumps(normalized)}</tools>")

        @app.post("/v1/chat/completions")
        @app.post("/chats/completions")
        async def chat_completions(request: ChatCompletionRequest):
            req_dump = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            _chat_completion_log_print("request (raw body)", req_dump)

            llm = controller.handlers.llm
            last_user_message, history, system_prompt = convert_messages_openai_to_newelle(request.messages)
            embed_openai_tools_in_system_prompt(system_prompt, request.tools)

            _chat_completion_log_print(
                "request (normalized for LLM)",
                {
                    "last_user_message": last_user_message,
                    "history_len": len(history),
                    "history_tail": history[-5:] if history else [],
                    "system_prompt": system_prompt,
                },
                max_chars=6000,
            )

            if not last_user_message:
                print("[API chat/completions] response: 400 No user message provided")
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "No user message provided", "type": "invalid_request_error"}},
                )

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            model_name = request.model or (llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default")

            print(
                f"[API chat/completions] routing completion_id={completion_id} model={model_name!r} "
                f"stream={request.stream} messages={len(request.messages)}"
            )

            if request.stream:
                return self._stream_response(llm, completion_id, created, model_name, last_user_message, history, system_prompt)
            else:
                return self._non_stream_response(llm, completion_id, created, model_name, last_user_message, history, system_prompt)

        @app.post("/v1/audio/speech")
        async def create_speech(request: SpeechRequest):
            from fastapi.responses import Response

            tts = controller.handlers.tts
            voice = request.voice

            if request.stream:
                return self._stream_tts(tts, voice, request.input, request.response_format)
            else:
                return self._non_stream_tts(tts, voice, request.input, request.response_format)

        @app.post("/v1/audio/transcriptions")
        async def create_transcription(file: UploadFile = File(...), model: Optional[str] = None, language: Optional[str] = None, prompt: Optional[str] = None, temperature: Optional[float] = None):
            stt = controller.handlers.stt

            temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            try:
                content = await file.read()
                temp_file.write(content)
                temp_file.flush()
                temp_file.close()

                text = stt.recognize_file(temp_file.name)
                if text is None:
                    text = ""

                return {"text": text}
            except Exception as e:
                return JSONResponse(
                    status_code=500,
                    content={"error": {"message": f"Transcription failed: {str(e)}", "type": "server_error"}},
                )
            finally:
                try:
                    temp_file.close()
                    os.unlink(temp_file.name)
                except Exception:
                    pass

        return app

    def _non_stream_response(self, llm, completion_id, created, model_name, prompt, history, system_prompt):
        from fastapi.responses import JSONResponse

        try:
            result = llm.send_message(prompt, history, system_prompt)
        except Exception as e:
            result = f"[Error: {str(e)}]"

        if isinstance(result, str) and result.startswith("[Error:"):
            message = {"role": "assistant", "content": result}
            finish_reason = "stop"
        else:
            content_clean, tool_calls = parse_tool_calls_from_assistant_content(result if isinstance(result, str) else str(result))
            message = {"role": "assistant", "content": content_clean}
            if tool_calls:
                message["tool_calls"] = tool_calls
                finish_reason = "tool_calls"
            else:
                finish_reason = "stop"

        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        _chat_completion_log_print(f"response (non-stream) id={completion_id}", payload)

        return JSONResponse(content=payload)

    def _stream_response(self, llm, completion_id, created, model_name, prompt, history, system_prompt):
        from fastapi.responses import StreamingResponse

        q = queue.Queue()
        done_sentinel = object()
        error_container = [None]
        # Capture the return value of generate_text_stream, which includes tool-call
        # JSON blocks appended AFTER streaming ends — on_update only fires during
        # content chunks so final_full_message from the queue would miss them.
        final_result_container = [None]

        def on_update(full_message: str):
            q.put(("chunk", full_message))

        def run_llm():
            try:
                final_result_container[0] = llm.generate_text_stream(prompt, history, system_prompt, on_update=on_update)
            except Exception as e:
                error_container[0] = str(e)
            finally:
                q.put(done_sentinel)

        thread = threading.Thread(target=run_llm, daemon=True)
        thread.start()

        prev_len = 0

        def event_generator():
            nonlocal prev_len

            print(
                f"[API chat/completions] stream start id={completion_id} model={model_name!r} "
                f"prompt_len={len(prompt)} history_len={len(history)} system_prompt_parts={len(system_prompt)}"
            )

            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

            final_full_message = ""

            while True:
                item = q.get()
                if item is done_sentinel:
                    break

                _, full_message = item
                final_full_message = full_message
                delta = full_message[prev_len:]
                prev_len = len(full_message)

                if delta:
                    yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': delta}, 'finish_reason': None}]})}\n\n"

            # Prefer the return value (has tool calls appended post-stream) over
            # final_full_message which only accumulates on_update content chunks.
            message_for_parsing = final_result_container[0] if final_result_container[0] is not None else final_full_message

            if error_container[0] is not None:
                err_text = f"\n[Error: {error_container[0]}]"
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': err_text}, 'finish_reason': None}]})}\n\n"
                finish_reason = "stop"
            else:
                _, streamed_tool_calls = parse_tool_calls_from_assistant_content(message_for_parsing)
                if streamed_tool_calls:
                    delta_tc = []
                    for i, tc in enumerate(streamed_tool_calls):
                        delta_tc.append({
                            "index": i,
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        })
                    yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'tool_calls': delta_tc}, 'finish_reason': None}]})}\n\n"
                    finish_reason = "tool_calls"
                else:
                    finish_reason = "stop"

            _chat_completion_log_print(
                f"response (stream end) id={completion_id} finish_reason={finish_reason}",
                {
                    "assistant_raw_len": len(message_for_parsing),
                    "assistant_raw": message_for_parsing,
                    "stream_error": error_container[0],
                },
                max_chars=12000,
            )

            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}]})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    def _non_stream_tts(self, tts, voice, text, response_format):
        from fastapi.responses import Response

        try:
            temp_file = tempfile.NamedTemporaryFile(suffix=f".{response_format}", delete=False)
            temp_file.close()

            try:
                tts.save_audio(text, temp_file.name)

                with open(temp_file.name, "rb") as f:
                    audio_data = f.read()

                content_type = "audio/mpeg" if response_format == "mp3" else "audio/wav"

                return Response(
                    content=audio_data,
                    media_type=content_type,
                    headers={"Content-Disposition": f"attachment; filename=speech.{response_format}"},
                )
            finally:
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass
                finally:
                    if voice:
                        #tts.set_voice(original_voice)
                        pass
        except Exception as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"error": {"message": f"TTS failed: {str(e)}", "type": "server_error"}},
            )

    def _stream_tts(self, tts, voice, text, response_format):
        from fastapi.responses import StreamingResponse, JSONResponse
        from subprocess import Popen
        import subprocess
        import asyncio

        if not tts.streaming_enabled():
            return self._non_stream_tts(tts, voice, text, response_format)

        content_type = "audio/mpeg" if response_format == "mp3" else "audio/wav"

        original_voice = tts.get_current_voice()
        if voice:
            tts.set_voice(voice)

        fmt_args = tts.get_stream_format_args()
        output_format = response_format if response_format != "wav" else "wav"

        try:
            ffmpeg_process = Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error"] + fmt_args
                + ["-i", "pipe:0", "-f", output_format, "pipe:1"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return JSONResponse(
                status_code=500,
                content={"error": {"message": "ffmpeg not found", "type": "server_error"}},
            )

        q = queue.Queue()
        done_sentinel = object()
        error_sentinel = object()

        def reader():
            try:
                while True:
                    data = ffmpeg_process.stdout.read(4096)
                    if not data:
                        break
                    q.put(data)
            except Exception:
                pass
            finally:
                q.put(done_sentinel)

        def writer():
            try:
                for chunk in tts.get_audio_stream(text):
                    try:
                        ffmpeg_process.stdin.write(chunk)
                    except BrokenPipeError:
                        q.put(error_sentinel)
                        return
                try:
                    ffmpeg_process.stdin.close()
                except Exception:
                    pass
            except Exception as e:
                print(f"Streaming TTS error: {e}")
                q.put(error_sentinel)

        reader_thread = threading.Thread(target=reader, daemon=True)
        writer_thread = threading.Thread(target=writer, daemon=True)
        reader_thread.start()
        writer_thread.start()

        loop = asyncio.get_event_loop()

        async def audio_generator():
            while True:
                item = await loop.run_in_executor(None, q.get)
                if item is done_sentinel or item is error_sentinel:
                    break
                yield item
            try:
                ffmpeg_process.terminate()
            except Exception:
                pass
            if voice:
                tts.set_voice(original_voice)

        return StreamingResponse(audio_generator(), media_type=content_type)

    def start(self):
        if self.controller is None:
            return
        if not self.is_installed():
            print("Cannot start API server: dependencies not installed")
            return
        import uvicorn

        try:
            app = self._create_app()
            host = self._get_host()
            port = self._get_port()
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            self._server = uvicorn.Server(config)
            thread = threading.Thread(target=self._server.run, daemon=True)
            thread.start()
            print(f"API server started on {host}:{port}")
        except Exception as e:
            print(f"Failed to start API server: {e}")

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
            self._server = None
            print("API server stopped")

    def is_running(self):
        return self._server is not None and not self._server.should_exit

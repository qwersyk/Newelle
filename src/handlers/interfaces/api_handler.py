import io
import json
import os
import queue
import tempfile
import threading
import time
import uuid

from ...utility import convert_messages_openai_to_newelle
from ..extra_settings import ExtraSettings
from .interface import Interface


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
            ExtraSettings.ToggleSetting(
                key="auto_start",
                title=_("Auto Start"),
                description=_("Automatically start the API server when Newelle launches"),
                default=False,
            ),
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
            role: str
            content: str

        class ChatCompletionRequest(BaseModel):
            model: Optional[str] = None
            messages: list[ChatMessage]
            stream: Optional[bool] = False

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

        @app.post("/v1/chat/completions")
        @app.post("/chats/completions")
        async def chat_completions(request: ChatCompletionRequest):
            llm = controller.handlers.llm
            last_user_message, history, system_prompt = convert_messages_openai_to_newelle(request.messages)

            if not last_user_message:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "No user message provided", "type": "invalid_request_error"}},
                )

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            model_name = request.model or (llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default")

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

        return JSONResponse(content={
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _stream_response(self, llm, completion_id, created, model_name, prompt, history, system_prompt):
        from fastapi.responses import StreamingResponse

        q = queue.Queue()
        done_sentinel = object()
        error_container = [None]

        def on_update(full_message: str):
            q.put(("chunk", full_message))

        def run_llm():
            try:
                llm.generate_text_stream(prompt, history, system_prompt, on_update=on_update)
            except Exception as e:
                error_container[0] = str(e)
            finally:
                q.put(done_sentinel)

        thread = threading.Thread(target=run_llm, daemon=True)
        thread.start()

        prev_len = 0

        def event_generator():
            nonlocal prev_len

            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

            while True:
                item = q.get()
                if item is done_sentinel:
                    break

                _, full_message = item
                delta = full_message[prev_len:]
                prev_len = len(full_message)

                if delta:
                    yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': delta}, 'finish_reason': None}]})}\n\n"

            if error_container[0] is not None:
                err_text = f"\n[Error: {error_container[0]}]"
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': err_text}, 'finish_reason': None}]})}\n\n"

            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
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

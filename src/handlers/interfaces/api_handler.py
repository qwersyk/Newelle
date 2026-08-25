import base64
import io
import json
import os
import queue
import struct
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Union

from gi.repository import GLib

from ...utility import convert_messages_openai_to_newelle, parse_tool_calls_from_assistant_content
from ...utility.system import is_flatpak
from ..extra_settings import ExtraSettings
from .chat_interface import ChatInterface


class APIInterface(ChatInterface):
    key = "api"
    name = "OpenAI Compatible API Server"

    # ChatInterface folder/chat config (used by /v2/chat/completions)
    folder_name = "API"
    folder_color = "#e04369"
    folder_icon = "folder-symbolic"
    chat_name_prefix = "🌐 API"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._server = None
        # user_key -> event_q for in-progress runs paused at a tool interaction
        self._pending_streams: dict[str, queue.Queue] = {}
        # NDJSON log writer runs on a single lazy daemon thread fed by a queue
        # so disk I/O never blocks the FastAPI asyncio event loop. The queue /
        # thread are created on first write, lazily. _log_writer_init_lock
        # guards the lazy-start TOCTOU race; everything else is single-thread.
        self._log_writer_init_lock = threading.Lock()
        self._log_file_queue: "queue.Queue | None" = None
        # Cap the number of "log file write failed" warnings so a bad path
        # doesn't flood stdout with repeated errors.
        self._log_file_error_count = 0

    @staticmethod
    def get_extra_requirements() -> list:
        return ["fastapi", "uvicorn"]

    # Single source of truth for the on-disk log location. Used both as the
    # EntrySetting default (so the UI shows the value) and as the fallback in
    # _resolve_log_file_path() (so an empty user value still resolves sanely).
    # Mirrors controller.init_paths(): under Flatpak GLib already returns the
    # sandboxed data dir (~/.var/app/io.github.qwersyk.Newelle/data), and we do
    # NOT append the app folder in that case; on native builds we append
    # "Newelle" so logs live with the rest of the app data (~/.local/share/Newelle).
    _LOG_FILE_NAME = "api_logs.jsonl"

    @staticmethod
    def _default_log_file_path() -> str:
        data_dir = GLib.get_user_data_dir()
        if not is_flatpak():
            data_dir = os.path.join(data_dir, "Newelle")
        return os.path.join(data_dir, "api_logs.jsonl")

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
            ExtraSettings.ToggleSetting(
                key="log_requests",
                title=_("Enable API logging"),
                description=_("Log each request and response to stdout."),
                default=False,
            ),
            ExtraSettings.ToggleSetting(
                key="log_to_file",
                title=_("Save API logs to a JSON file"),
                description=_("Append each request and response to a .jsonl file."),
                default=False,
            ),
            ExtraSettings.EntrySetting(
                key="log_file_path",
                title=_("Log file path"),
                description=_("Path for the .jsonl log. ~ expands to home; the folder is created on demand."),
                default=self._default_log_file_path(),
            ),
            ExtraSettings.ToggleSetting(
                key="use_bang_for_commands",
                title=_("Use ! instead of / for commands in /v2 API"),
                description=_("Use ! for /v2 API commands, for example !start."),
                default=False,
            ),
        ]



    def _get_port(self):
        return self.get_setting("port", search_default=True, return_value=8080)

    def _get_host(self):
        return self.get_setting("host", search_default=True, return_value="127.0.0.1")

    def _is_logging_enabled(self) -> bool:
        """Return True when the user opted-in to per-request API logging."""
        return bool(self.get_setting("log_requests", search_default=True, return_value=False))

    def _is_logging_to_file(self) -> bool:
        """Return True when the user opted-in to persisting API logs to a file."""
        return bool(self.get_setting("log_to_file", search_default=True, return_value=False))

    def _use_bang_for_commands(self) -> bool:
        """Return True when /v2 API commands should use ! instead of /."""
        return bool(self.get_setting("use_bang_for_commands", search_default=True, return_value=False))

    def _try_handle_v2_command(self, user_id, text: str) -> str | None:
        """Handle a /v2 command with the configured command prefix."""
        if self._use_bang_for_commands():
            if not text.startswith("!"):
                return None
            text = "/" + text[1:]
        return self.try_handle_command(user_id, text)

    def _resolve_log_file_path(self) -> str:
        """Return the configured NDJSON log file path, expanding `~`.

        Always returns the Flatpak-aware default if the user value is blank,
        so callers can rely on receiving a non-empty string.
        """
        default = self._default_log_file_path()
        raw = self.get_setting(
            "log_file_path",
            search_default=True,
            return_value=default,
        )
        cleaned = (raw or "").strip() or default
        return os.path.expanduser(cleaned)

    def _ensure_log_writer_thread(self) -> "queue.Queue":
        """Start a single background drainer thread (idempotent) and return its queue.

        Disk I/O for the NDJSON log file is funneled through one daemon thread
        so a slow filesystem never stalls the FastAPI asyncio event loop. The
        thread is created lazily on first write and lives until process exit
        (it's a daemon — no shutdown signaling needed). Double-checked locking
        makes first-call startup safe under concurrent request handlers.
        """
        if self._log_file_queue is not None:
            return self._log_file_queue
        with self._log_writer_init_lock:
            if self._log_file_queue is not None:
                return self._log_file_queue
            q: "queue.Queue" = queue.Queue()
            self._log_file_queue = q

            def drainer():
                while True:
                    item = q.get()
                    if item is None:
                        break
                    line, path = item
                    try:
                        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
                        with open(path, "a", encoding="utf-8") as f:
                            f.write(line)
                        self._log_file_error_count = 0
                    except Exception as e:
                        self._log_file_error_count += 1
                        if self._log_file_error_count <= 3:
                            print(f"[API] failed to write log entry to {path}: {e}")

            threading.Thread(target=drainer, daemon=True, name="api-log-writer").start()
            return q

    def _write_log_file_entry(self, stage: str, payload) -> None:
        """Enqueue (timestamped, serialised) log entry for the background writer.

        Each line shares the unified ``{timestamp, stage, payload}`` schema so
        downstream consumers don't have to fork on entry shape. The on-disk
        file is the authoritative record: payloads are written *un-truncated*,
        even when the stdout side truncates for readability.

        No-op when 'log_to_file' is off. The actual disk write happens on a
        dedicated background thread (see ``_ensure_log_writer_thread``), so a
        slow / misconfigured path cannot stall the API event loop. Errors in
        the worker are swallowed but the first few are surfaced to stdout so
        a misconfigured path is observable; the error counter resets on the
        first successful write so a recovered path surfaces a fresh warning if
        it breaks again.
        """
        if not self._is_logging_to_file():
            return
        path = self._resolve_log_file_path()
        if not path:
            return
        try:
            stamped = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "payload": payload,
            }
            line = json.dumps(stamped, ensure_ascii=False, default=str) + "\n"
        except (TypeError, ValueError) as e:
            self._record_log_file_error(f"failed to serialize log entry: {e}")
            return

        # Off-thread write — never blocks the asyncio event loop.
        self._ensure_log_writer_thread().put((line, path))

    def _record_log_file_error(self, message: str) -> None:
        """Surface at most the first 3 file-logging errors to stdout."""
        self._log_file_error_count += 1
        if self._log_file_error_count <= 3:
            print(f"[API] {message}")

    def _log(self, msg: str) -> None:
        """Print *msg* to stdout (when stdout logging is enabled) and append a
        corresponding entry to the JSONL log file (when file logging is enabled).

        Stdout runs first so a slow disk write never delays the live signal the
        operator sees while debugging.
        """
        if self._is_logging_enabled():
            print(msg)
        if self._is_logging_to_file():
            self._write_log_file_entry("event", msg)

    def _structured_log_print(self, endpoint: str, label: str, obj, max_chars: int = 8000) -> None:
        """Structured logging for an OpenAI-compatible API endpoint.

        Always appends the un-truncated payload as one NDJSON line to the log file
        when the 'log_to_file' toggle is enabled. Additionally prints a
        truncated, pretty-printed version to stdout when 'log_requests' is on.
        The file is the authoritative record; stdout is a developer convenience.
        """
        if self._is_logging_to_file():
            self._write_log_file_entry(label, obj)
        if not self._is_logging_enabled():
            return
        try:
            text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        except TypeError:
            text = repr(obj)
        orig_len = len(text)
        if orig_len > max_chars:
            text = text[:max_chars] + f"\n... [truncated, {orig_len} chars total]"
        print(f"[API {endpoint}] {label}\n{text}")

    def _chat_completion_log_print(self, label: str, obj, max_chars: int = 8000) -> None:
        """Structured logging for the chat completions endpoint."""
        self._structured_log_print("chat/completions", label, obj, max_chars)

    def _embedding_log_print(self, label: str, obj, max_chars: int = 8000) -> None:
        """Structured logging for the embeddings endpoint."""
        self._structured_log_print("embeddings", label, obj, max_chars)


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
            user: Optional[str] = None

        class SpeechRequest(BaseModel):
            model: Optional[str] = None
            input: str
            voice: Optional[str] = None
            response_format: Optional[str] = "mp3"
            stream: Optional[bool] = False

        class EmbeddingRequest(BaseModel):
            model_config = {"extra": "ignore"}

            model: Optional[str] = None
            input: Union[str, list]
            encoding_format: Optional[str] = "float"
            dimensions: Optional[int] = None
            user: Optional[str] = None

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
        @app.get("/v2/models")
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
            self._chat_completion_log_print("request (raw body)", req_dump)

            llm = controller.handlers.llm
            last_user_message, history, system_prompt = convert_messages_openai_to_newelle(request.messages)
            embed_openai_tools_in_system_prompt(system_prompt, request.tools)

            # When the user opted-in to logging, dump the full system prompt,
            # full chat history and the last user message so the entire request
            # round-trip can be inspected or replayed. _chat_completion_log_print
            # is a no-op when logging is disabled.
            self._chat_completion_log_print(
                "request (system prompt + history + last user message)",
                {
                    "system_prompt": system_prompt,
                    "history": history,
                    "last_user_message": last_user_message,
                },
            )

            if not last_user_message:
                self._log("[API chat/completions] response: 400 No user message provided")
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "No user message provided", "type": "invalid_request_error"}},
                )

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            model_name = request.model or (llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default")

            self._log(
                f"[API chat/completions] routing completion_id={completion_id} model={model_name!r} "
                f"stream={request.stream} messages={len(request.messages)}"
            )
            history = history[:-1]
            if request.stream:
                return self._stream_response(llm, completion_id, created, model_name, last_user_message, history, system_prompt)
            else:
                return self._non_stream_response(llm, completion_id, created, model_name, last_user_message, history, system_prompt)

        # ------------------------------------------------------------------ #
        # /v2/chat/completions — agent endpoint with tool support, commands,
        # and per-user persistent chat.  Only the last user message matters.
        # ------------------------------------------------------------------ #

        @app.post("/v2/chat/completions")
        async def chat_completions_v2(request: ChatCompletionRequest):
            req_dump = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            self._chat_completion_log_print("v2 request (raw body)", req_dump)

            user_key = (request.user or "default").strip() or "default"

            # Extract only the last user message (history is ignored — use the
            # persistent chat owned by this user for full context).
            last_user_message, _hist, _sys = convert_messages_openai_to_newelle(request.messages)

            if not last_user_message:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "No user message provided", "type": "invalid_request_error"}},
                )

            llm = controller.handlers.llm
            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            model_name = request.model or (
                llm.get_selected_model() if hasattr(llm, "get_selected_model") else "default"
            )

            stripped = last_user_message.strip()

            # ── /option with a paused run ────────────────────────────────────
            # Resolve the interaction first so the LLM thread unblocks, then
            # stream/collect the continuation from the saved event queue.
            command_prefix = "!" if self._use_bang_for_commands() else "/"
            if stripped.startswith(f"{command_prefix}option") and user_key in self._pending_streams:
                pending_q = self._pending_streams.pop(user_key)
                # Resolve the interaction (fires callback → unblocks LLM thread).
                # The returned "✅ …" string is intentionally discarded here;
                # the actual output comes from the resumed stream.
                self._try_handle_v2_command(user_key, stripped)
                self._log(
                    f"[API v2/chat/completions] resuming paused run user={user_key!r} "
                    f"completion_id={completion_id} stream={request.stream}"
                )
                if request.stream:
                    return self._v2_resume_stream(
                        user_key, completion_id, created, model_name, pending_q
                    )
                else:
                    return self._v2_resume_non_stream(
                        user_key, completion_id, created, model_name, pending_q
                    )

            # ── Commands ─────────────────────────────────────────────────────
            cmd_response = self._try_handle_v2_command(user_key, last_user_message)
            if cmd_response is not None:
                self._chat_completion_log_print(
                    f"v2 command response user={user_key!r}", {"response": cmd_response}
                )
                if request.stream:
                    from fastapi.responses import StreamingResponse as _SR

                    _s = self._sse_chunk  # alias

                    def _cmd_sse():
                        yield _s(completion_id, created, model_name,
                                 role="assistant", delta_content="")
                        yield _s(completion_id, created, model_name,
                                 delta_content=cmd_response)
                        yield _s(completion_id, created, model_name, finish_reason="stop")
                        yield "data: [DONE]\n\n"

                    return _SR(_cmd_sse(), media_type="text/event-stream")

                return JSONResponse(content={
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": cmd_response},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })

            # ── Full agent run ───────────────────────────────────────────────
            self._log(
                f"[API v2] user={user_key!r} completion_id={completion_id} "
                f"stream={request.stream} model={model_name!r}"
            )

            if request.stream:
                return self._v2_stream_response(
                    user_key, completion_id, created, model_name, last_user_message
                )
            else:
                return self._v2_non_stream_response(
                    user_key, completion_id, created, model_name, last_user_message
                )

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

        @app.post("/v1/embeddings")
        async def create_embeddings(request: EmbeddingRequest):
            req_dump = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            self._embedding_log_print("request (raw body)", req_dump)

            embedding_handler = controller.handlers.embedding
            if embedding_handler is None:
                return JSONResponse(
                    status_code=503,
                    content={"error": {"message": "No embedding handler configured", "type": "server_error"}},
                )

            raw_input = request.input
            if isinstance(raw_input, str):
                texts = [raw_input]
            elif isinstance(raw_input, list):
                if len(raw_input) == 0:
                    return JSONResponse(
                        status_code=400,
                        content={"error": {"message": "'input' must not be empty", "type": "invalid_request_error"}},
                    )
                if all(isinstance(item, str) for item in raw_input):
                    texts = list(raw_input)
                else:
                    # Token-id arrays are not supported since the local embedder takes raw text.
                    return JSONResponse(
                        status_code=400,
                        content={"error": {"message": "Token-id inputs are not supported; provide a string or list of strings", "type": "invalid_request_error"}},
                    )
            else:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": "'input' must be a string or list of strings", "type": "invalid_request_error"}},
                )

            self._embedding_log_print("request (texts to embed)", {"texts": texts})

            try:
                embeddings = embedding_handler.get_embedding(texts)
            except Exception as e:
                return JSONResponse(
                    status_code=500,
                    content={"error": {"message": f"Embedding failed: {str(e)}", "type": "server_error"}},
                )

            try:
                rows = embeddings.tolist()
            except AttributeError:
                rows = list(embeddings)

            encoding_format = (request.encoding_format or "float").lower()
            data = []
            for idx, vector in enumerate(rows):
                vec_list = list(vector)
                if encoding_format == "base64":
                    packed = struct.pack(f"{len(vec_list)}f", *(float(v) for v in vec_list))
                    embedding_value = base64.b64encode(packed).decode("ascii")
                else:
                    embedding_value = [float(v) for v in vec_list]
                data.append({
                    "object": "embedding",
                    "index": idx,
                    "embedding": embedding_value,
                })

            model_name = request.model
            if not model_name:
                model_name = embedding_handler.get_setting("model", search_default=True, return_value=None) \
                    if hasattr(embedding_handler, "get_setting") else None
            if not model_name:
                model_name = getattr(embedding_handler, "key", "embedding")

            total_tokens = sum(len(t.split()) for t in texts)
            return {
                "object": "list",
                "data": data,
                "model": model_name,
                "usage": {
                    "prompt_tokens": total_tokens,
                    "total_tokens": total_tokens,
                },
            }

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
        self._chat_completion_log_print(f"response (non-stream) id={completion_id}", payload)

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

            self._log(
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

            self._chat_completion_log_print(
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

    # ------------------------------------------------------------------ #
    #              /v2 response helpers (tool-aware, per-user chat)       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _sse_chunk(completion_id, created, model_name,
                   delta_content=None, role=None, finish_reason=None) -> str:
        delta: dict = {}
        if role is not None:
            delta["role"] = role
        if delta_content is not None:
            delta["content"] = delta_content
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    @staticmethod
    def _render_tool_event(event: dict) -> str | None:
        """Return a plain-text string for a tool event, or None if nothing to show."""
        if event.get("type") == "tool_interaction":
            lines = [f"\nTool '{event['tool_name']}' needs your input:"]
            if event.get("display_text"):
                lines.append(event["display_text"][:300])
            for opt in event.get("options", []):
                lines.append(f"  {opt['index'] + 1}) {opt['title']}")
            lines.append("Reply with /option <n> in your next message.")
            return "\n".join(lines)
        if event.get("type") == "tool_result":
            display = event.get("display_text", "")
            if display:
                return f"\n[Tool '{event['tool_name']}': {display[:200]}]"
        return None

    def _start_process_message_thread(self, user_key, message) -> queue.Queue:
        """Spin up process_message in a thread; return the event queue."""
        event_q: queue.Queue = queue.Queue()

        def on_chunk(delta: str):
            event_q.put(("text", delta))

        def on_tool_event(event: dict):
            event_q.put(("tool", event))

        def run():
            try:
                self.process_message(
                    user_key, message, on_chunk=on_chunk, on_tool_event=on_tool_event
                )
            except Exception as e:
                event_q.put(("error", str(e)))
            finally:
                event_q.put(("done", None))

        threading.Thread(target=run, daemon=True).start()
        return event_q

    def _drain_queue_to_stream(self, user_key, completion_id, created, model_name,
                               event_q: queue.Queue):
        """Generator: forward events from *event_q* as SSE chunks.

        When a tool_interaction is encountered, the options are emitted, the
        queue is saved under *user_key* so the next /option request can resume,
        and the stream closes with finish_reason=stop.
        """
        _sse = self._sse_chunk  # local alias

        # Accumulate streamed text/tool output so the v2 stream-end log can
        # capture the full generated message (mirrors v1's `assistant_raw`).
        # Only pay the copying cost when the user has opted-in to logging.
        log_on = self._is_logging_enabled()
        accumulated_text = ""
        stream_error = None

        while True:
            kind, data = event_q.get()

            if kind == "done":
                self._chat_completion_log_print(
                    f"v2 stream end id={completion_id}",
                    {
                        "assistant_message": accumulated_text,
                        "assistant_message_len": len(accumulated_text),
                        "stream_error": stream_error,
                    },
                )
                yield _sse(completion_id, created, model_name, finish_reason="stop")
                yield "data: [DONE]\n\n"
                return

            if kind == "error":
                stream_error = data
                # Include the error suffix in the accumulated snapshot so the
                # log mirrors exactly what the client received.
                if log_on:
                    accumulated_text += f"\n\n[Error: {data}]"
                self._chat_completion_log_print(
                    f"v2 stream error id={completion_id}",
                    {
                        "assistant_message": accumulated_text,
                        "assistant_message_len": len(accumulated_text),
                        "stream_error": stream_error,
                    },
                )
                yield _sse(completion_id, created, model_name,
                           delta_content=f"\n\n[Error: {data}]")
                yield _sse(completion_id, created, model_name, finish_reason="stop")
                yield "data: [DONE]\n\n"
                return

            if kind == "text":
                if data:
                    if log_on:
                        accumulated_text += data
                    yield _sse(completion_id, created, model_name, delta_content=data)

            elif kind == "tool":
                rendered = self._render_tool_event(data)
                if rendered:
                    if log_on:
                        accumulated_text += rendered
                    yield _sse(completion_id, created, model_name, delta_content=rendered)

                if data.get("type") == "tool_interaction":
                    # Save queue so the next /option request can resume from here.
                    self._pending_streams[user_key] = event_q
                    self._chat_completion_log_print(
                        f"v2 stream paused for interaction id={completion_id}",
                        {
                            "assistant_message": accumulated_text,
                            "assistant_message_len": len(accumulated_text),
                            "tool_interaction": data,
                            "stream_error": stream_error,
                        },
                    )
                    yield _sse(completion_id, created, model_name, finish_reason="stop")
                    yield "data: [DONE]\n\n"
                    return  # close this stream; LLM thread stays alive in background

    def _v2_stream_response(self, user_key, completion_id, created, model_name, message):
        from fastapi.responses import StreamingResponse

        event_q = self._start_process_message_thread(user_key, message)

        def event_generator():
            yield self._sse_chunk(completion_id, created, model_name,
                                  role="assistant", delta_content="")
            yield from self._drain_queue_to_stream(
                user_key, completion_id, created, model_name, event_q
            )

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    def _v2_resume_stream(self, user_key, completion_id, created, model_name,
                          event_q: queue.Queue):
        """Stream the continuation of a run that was paused at a tool interaction."""
        from fastapi.responses import StreamingResponse

        def event_generator():
            yield self._sse_chunk(completion_id, created, model_name,
                                  role="assistant", delta_content="")
            yield from self._drain_queue_to_stream(
                user_key, completion_id, created, model_name, event_q
            )

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    def _v2_non_stream_response(self, user_key, completion_id, created, model_name, message):
        from fastapi.responses import JSONResponse

        event_q = self._start_process_message_thread(user_key, message)
        content, finish_reason = self._collect_queue(user_key, event_q)

        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        self._chat_completion_log_print(f"v2 response (non-stream) id={completion_id}", payload)
        return JSONResponse(content=payload)

    def _v2_resume_non_stream(self, user_key, completion_id, created, model_name,
                              event_q: queue.Queue):
        """Collect the continuation of a paused run into a single JSON response."""
        from fastapi.responses import JSONResponse

        content, finish_reason = self._collect_queue(user_key, event_q)

        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        self._chat_completion_log_print(f"v2 response (resume non-stream) id={completion_id}", payload)
        return JSONResponse(content=payload)

    def _collect_queue(self, user_key, event_q: queue.Queue) -> tuple[str, str]:
        """Drain *event_q* synchronously; return (content, finish_reason).

        If a tool_interaction is encountered the queue is saved to
        ``_pending_streams`` and we return early with the options appended.
        """
        content = ""
        finish_reason = "stop"

        while True:
            kind, data = event_q.get()

            if kind == "done":
                break

            if kind == "error":
                content += f"\n\n[Error: {data}]"
                break

            if kind == "text":
                content += data

            elif kind == "tool":
                rendered = self._render_tool_event(data)
                if rendered:
                    content += rendered

                if data.get("type") == "tool_interaction":
                    # Save queue; the next /option request will resume.
                    self._pending_streams[user_key] = event_q
                    finish_reason = "stop"
                    break  # return early; LLM thread stays alive

        return content, finish_reason

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
            self._write_state_file()
            print(f"API server started on {host}:{port}")
        except Exception as e:
            print(f"Failed to start API server: {e}")

    def stop(self):
        self._clear_state_file()
        if self._server is not None:
            self._server.should_exit = True
            self._server = None
            print("API server stopped")

    def _is_locally_running(self):
        return self._server is not None and not self._server.should_exit

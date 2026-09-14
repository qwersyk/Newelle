"""Controller-owned recording lifetime and optional transcription.

Only plain Audio metadata is persisted. Workers, callbacks and retrieval snapshots
belong to a particular request and never enter the pickle or provider payload.
"""
import copy
import gettext
import logging
import os
import shutil
import threading
import uuid
import wave

from gi.repository import GLib

from .media import audio_history_text, audio_text, extract_audio

_ = gettext.gettext
logger = logging.getLogger(__name__)


class AudioInputManager:
    """Manage audio turns through composition with the application controller."""

    def __init__(self, controller):
        self.controller = controller
        self._turns = {}

    def get_state(self, path):
        return self._turns.get(path)

    def prepare_audio_input(self, audio_path, is_current=None):
        """Preserve a capture before its caller removes/reuses the temporary WAV."""
        with wave.open(audio_path, "rb") as recording:
            if not recording.getnframes():
                raise ValueError(_("The recording is empty. Please record again."))
        directory = os.path.join(self.controller.data_dir, "recordings")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, uuid.uuid4().hex + ".wav")
        shutil.copyfile(audio_path, path)
        timing = (self.controller.settings.get_string("audio-transcription-timing")
                  if self.controller.settings.get_boolean("audio-transcribe") else "off")
        self._turns[path] = {
            "path": path, "timing": timing, "status": "pending" if timing != "off" else "off",
            "stt": self.controller.handlers.stt, "is_current": is_current or (lambda: True),
            "started": False, "done": threading.Event(),
        }
        return f"```audio\n{path}\n```\n"

    def bind_audio_turn(self, chat_id, is_current=None):
        """Attach a request to the last user turn, including tool continuations."""
        chat = self.controller.get_chat_by_id(chat_id)
        supports_audio = self.controller.get_model_for_chat(chat).supports_audio()
        if not supports_audio:
            self._transcribe_for_text_model(chat_id, is_current)
            chat = self.controller.get_chat_by_id(chat_id)
        latest = next((i for i in range(len(chat) - 1, -1, -1)
                       if chat[i].get("User") == "User" and not chat[i].get("ToolContext")), None)
        if latest is None:
            return None
        message = chat[latest]
        path, caption = extract_audio(message.get("Message", ""))
        if path is None:
            return None
        message.setdefault("UUID", int(uuid.uuid4()))
        metadata = message["Audio"] = copy.deepcopy(message.get("Audio", {}))
        timing = metadata.get("timing", "off")
        state = self._turns.get(path)
        if state and "uuid" in state and (state["uuid"], state["chat_id"]) != (message["UUID"], chat_id):
            # A branch or copied message owns a separate turn. Never retarget a
            # worker which may still be completing the original recording.
            recording_message = self.prepare_audio_input(path, is_current)
            path, _unused = extract_audio(recording_message)
            message["Message"] = recording_message + caption
            state = self._turns[path]
            state.update(timing=timing, transcript=caption.strip(),
                         status="complete" if caption.strip() else ("off" if timing == "off" else "pending"))
        if state is not None and (state.get("status") == "failed" or not state["is_current"]()):
            timing = (self.controller.settings.get_string("audio-transcription-timing")
                      if self.controller.settings.get_boolean("audio-transcribe") else "off")
            state = None
        if state is None:
            status = "complete" if caption.strip() else ("off" if timing == "off" else "pending")
            state = {"path": path, "timing": timing, "status": status,
                     "started": False, "done": threading.Event(), "stt": self.controller.handlers.stt,
                     "is_current": is_current or (lambda: True), "transcript": caption.strip()}
            self._turns[path] = state
        if is_current is not None and not state["started"]:
            state["is_current"] = is_current
        if state["status"] in ("complete", "off"):
            state["done"].set()
        state.update(chat_id=chat_id, uuid=message["UUID"])
        metadata.update(timing=state["timing"], status=state["status"])
        # Freeze retrieval once per user turn, including every tool continuation.
        if not supports_audio or "context" not in state:
            state["context"] = copy.deepcopy(chat[:latest])
        self.controller.save_chats()
        GLib.idle_add(self.controller.ui_controller.refresh_audio_message, chat_id, message["UUID"])
        model = self.controller.get_model_for_chat(chat)
        if not model.supports_audio():
            state["timing"] = metadata["timing"] = "before"
        if not os.path.isfile(path) and (model.supports_audio() or not caption.strip()):
            raise ValueError(_("The audio recording is missing. Please record again."))
        if state["timing"] == "before" and state["status"] != "complete":
            try:
                transcript = self._recognize_audio(path, state)
            except Exception as exc:
                state["status"] = metadata["status"] = "failed"
                self.controller.save_chats()
                GLib.idle_add(self.controller.ui_controller.refresh_audio_message, chat_id, message["UUID"])
                raise ValueError(_("Transcription failed. The recording is saved; retry the message or disable transcription.")) from exc
            if not state["is_current"]():
                raise ValueError(_("Audio request cancelled"))
            state.update(status="complete", transcript=transcript)
            metadata["status"] = "complete"
            message["Message"] = f"```audio\n{path}\n```\n{transcript}"
            state["done"].set()
            self.controller.save_chats()
            GLib.idle_add(self.controller.ui_controller.refresh_audio_message, chat_id, message["UUID"])
            if state.get("on_transcript"):
                GLib.idle_add(state["on_transcript"], transcript)
        state.setdefault("input_message", message["Message"])
        state.setdefault("input_status", state["status"])
        return state

    def _transcribe_for_text_model(self, chat_id, is_current):
        """Use ordinary STT before context retrieval for models without audio."""
        is_current = is_current or (lambda: True)
        for message in list(self.controller.get_chat_by_id(chat_id)):
            if message.get("User") != "User":
                continue
            path, caption = extract_audio(message.get("Message", ""))
            if path is None:
                continue
            message.setdefault("UUID", int(uuid.uuid4()))
            state = self._turns.get(path)
            if not caption.strip():
                try:
                    if state and state["started"] and state["status"] == "pending":
                        state["done"].wait()
                    if state and state["status"] == "complete":
                        caption = state["transcript"]
                    else:
                        caption = self._recognize_audio(path, {"stt": self.controller.handlers.stt})
                except Exception as exc:
                    message.setdefault("Audio", {})["status"] = "failed"
                    self.controller.save_chats()
                    GLib.idle_add(self.controller.ui_controller.refresh_audio_message, chat_id, message["UUID"])
                    raise ValueError(_("Speech recognition failed. The recording is saved; check your speech recognition settings and retry.")) from exc
            if not is_current() or not any(
                saved.get("UUID") == message["UUID"]
                for saved in self.controller.get_chat_by_id(chat_id)
            ):
                raise ValueError(_("Audio request cancelled"))
            message["Message"] = f"```audio\n{path}\n```\n{caption.strip()}"
            message.setdefault("Audio", {}).update(timing="before", status="complete")
            if state:
                state.update(timing="before", status="complete", transcript=caption.strip(),
                             input_message=message["Message"], input_status="complete")
                state["done"].set()
                if state.get("on_transcript"):
                    GLib.idle_add(state["on_transcript"], caption.strip())
            self.controller.save_chats()
            GLib.idle_add(self.controller.ui_controller.refresh_audio_message, chat_id, message["UUID"])

    def audio_request_history(self, history, state):
        """Keep the original audio input across GUI tool continuations too."""
        history = copy.deepcopy(history)
        if state:
            frozen = {extract_audio(m.get("Message", ""))[0]: m
                      for m in state["context"] if extract_audio(m.get("Message", ""))[0]}
            frozen[state["path"]] = {
                "Message": state["input_message"],
                "Audio": {"timing": state["timing"], "status": state["input_status"]},
            }
            for entry in history:
                path, _caption = extract_audio(entry.get("Message", ""))
                if path in frozen:
                    entry["Message"] = frozen[path]["Message"]
                    entry["Audio"] = copy.deepcopy(frozen[path].get("Audio", {}))
        return history

    def audio_prompt_variable(self, name, state, fallback):
        if state is not None and name in ("history", "message"):
            chat = self.audio_request_history(self.controller.get_chat_by_id(state["chat_id"]), state)
            if name == "message":
                return audio_text(chat[-1]["Message"]) if chat else ""
            return "\n".join(f"{m['User']}: {audio_text(m['Message'])}"
                             for m in self.controller.get_history(chat=chat))
        return fallback(name)

    def audio_context_query(self, current_message, state):
        if state and state["timing"] != "before":
            return next((caption for entry in reversed(state["context"])
                         if entry.get("User") == "User" and not entry.get("ToolContext")
                         and (caption := extract_audio(entry.get("Message", ""))[1].strip())), "")
        return current_message

    def _recognize_audio(self, path, state):
        stt = state["stt"]
        if stt is None or not stt.is_installed():
            raise ValueError(_("Speech recognition unavailable"))
        transcript = stt.recognize_file(path)
        if not transcript or not transcript.strip():
            raise ValueError(_("No speech was recognized"))
        return transcript.strip()

    def start_audio_transcription(self, state):
        """Called at model dispatch, after all request inputs have been copied."""
        if state is None or state["timing"] != "after" or state["started"] or state["status"] == "complete":
            return
        state["started"] = True
        state["status"] = "pending"
        state["done"].clear()
        path = state["path"]

        def recognize():
            try:
                state["transcript"] = self._recognize_audio(path, state)
                state["status"] = "complete"
            except Exception:
                logger.exception("Audio transcription failed")
                state["status"] = "failed"
            finally:
                state["done"].set()
                GLib.idle_add(self._apply_audio_transcript, path, state)
        threading.Thread(target=recognize, name="audio-transcript", daemon=True).start()

    def _apply_audio_transcript(self, path, state):
        if not state["is_current"]() or self._turns.get(path) is not state:
            return False
        for message in self.controller.get_chat_by_id(state["chat_id"]):
            if message.get("UUID") != state["uuid"] or extract_audio(message.get("Message", ""))[0] != path:
                continue
            message.setdefault("Audio", {}).update(timing=state["timing"], status=state["status"])
            if state["status"] == "complete":
                message["Message"] = f"```audio\n{path}\n```\n{state['transcript']}"
                if state.get("on_transcript"):
                    state["on_transcript"](state["transcript"])
            self.controller.save_chats()
            self.controller.ui_controller.refresh_audio_message(state["chat_id"], state["uuid"])
            if state["status"] == "failed":
                self.controller.ui_controller.send_notification(_("Audio transcription failed. The model response is unaffected."))
            break
        return False

    def audio_retrieval_chat(self, chat):
        """Exclude the current audio turn from retrieval in off/after modes."""
        for index in range(len(chat) - 1, -1, -1):
            message = chat[index]
            if message.get("User") != "User" or message.get("ToolContext"):
                continue
            path, _caption = extract_audio(message.get("Message", ""))
            if path:
                state = self._turns.get(path, {})
                timing = state.get("timing", message.get("Audio", {}).get("timing", "off"))
                if timing != "before":
                    return audio_history_text(copy.deepcopy(state.get("context", chat[:index])))
            break
        return audio_history_text(chat)

    def merge_audio_transcripts(self, chat, current=None):
        """Preserve transcript updates when extension postprocessing replaces history."""
        saved = {m.get("UUID"): m for m in (current or []) if m.get("Audio", {}).get("status") == "complete"}
        for message in chat:
            path, _caption = extract_audio(message.get("Message", ""))
            existing = saved.get(message.get("UUID"))
            if (path and existing and extract_audio(existing.get("Message", ""))[0] == path
                    and message.get("Audio", {}).get("status") != "complete"):
                message["Message"] = existing["Message"]
                message["Audio"] = copy.deepcopy(existing["Audio"])
            state = self._turns.get(path)
            if (state and state.get("uuid") == message.get("UUID")
                    and state["is_current"]() and state["status"] == "complete"
                    and message.get("Audio", {}).get("status") != "complete"):
                message["Message"] = f"```audio\n{path}\n```\n{state['transcript']}"
                message.setdefault("Audio", {}).update(timing=state["timing"], status="complete")
        return chat

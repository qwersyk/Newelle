from collections.abc import Callable
import importlib 
import os 
import json 
import shutil
import sys
import inspect 

from gi.repository import Gtk, Adw

from .handlers import Handler

from .handlers.llm import LLMHandler
from .handlers.stt import STTHandler
from .handlers.tts import TTSHandler
from .handlers.rag import RAGHandler
from .handlers.memory import MemoryHandler
from .handlers.embeddings import EmbeddingHandler
from .handlers.websearch import WebSearchHandler
from .ui_controller import UIController
from .tools import Command
from .subagents import (
    InvalidSubagentError,
    normalize_subagent_definition,
    validate_subagent_id_component,
)


def validate_subagent_contributions(extension) -> dict[str, dict]:
    """Validate and namespace one extension's named subagents.

    Extension definitions carry a local ``id``.  The returned mapping uses a
    stable application-wide ID and includes source metadata while retaining
    only canonical definition fields.
    """
    extension_id = validate_subagent_id_component(
        getattr(extension, "id", None), "extension id"
    )
    contributions = extension.get_subagents() or []
    if not isinstance(contributions, (list, tuple)):
        raise InvalidSubagentError(
            f"Extension '{extension_id}' get_subagents() must return a list"
        )

    result = {}
    local_ids = set()
    for contribution in contributions:
        if not isinstance(contribution, dict):
            raise InvalidSubagentError(
                f"Extension '{extension_id}' returned a non-object subagent"
            )
        local_id = validate_subagent_id_component(
            contribution.get("id"), "extension subagent id"
        )
        if local_id in local_ids:
            raise InvalidSubagentError(
                f"Extension '{extension_id}' returned duplicate subagent id "
                f"'{local_id}'"
            )
        local_ids.add(local_id)
        identifier = f"extension:{extension_id}:{local_id}"
        definition = normalize_subagent_definition(contribution)
        definition.update(
            {
                "id": identifier,
                "source": "extension",
                "read_only": True,
                "extension_id": extension_id,
                "source_extension": extension_id,
                "local_id": local_id,
            }
        )
        result[identifier] = definition
    return result


class NewelleExtension(Handler):
    """The base class for all extensions"""
    
    # Name and ID of the extension
    # Name and ID must be less than 50 characters
    name = "Demo Extension"
    id = "demoextension"

    def __init__(self, pip_path : str, extension_path: str, settings):
        """
        Initialize the extension. No resource consuming action should be done on initialization

        Args:
            settings (): Gio application settings
            pip_path: path to the pip directory 
            extension_path: path to the extension cache directory 
        """
        self.pip_path = pip_path
        self.path = self.pip_path
        self.extension_path = extension_path
        self.settings = settings
        self.key = self.id
        self.schema_key = "extensions-settings"
        pass

    def set_handlers(self, llm: LLMHandler, stt: STTHandler, tts:TTSHandler|None, secondary_llm: LLMHandler, embedding: EmbeddingHandler, rag: RAGHandler|None, memory: MemoryHandler|None, websearch: WebSearchHandler):
        """Set the handlers for the extension

        Args:
            llm: LLMHandler 
            stt: STTHandler 
            tts: TTSHandler|None if disabled None is given 
            secondary_llm: LLMHandler (if disabled, is the same of LLMHandler) 
            embedding: EmbeddingHandler 
            rag: RAGHandler|None if disabled None is given 
            memory: MemoryHandler|None if disabled None is given 
        """
        self.llm = llm 
        self.stt = stt
        self.tts = tts
        self.secondary_llm = secondary_llm
        self.embedding = embedding
        self.rag = rag
        self.memory = memory
        self.websearch = websearch

    def get_llm_handlers(self) -> list[dict]:
        """
        Returns the list of LLM handlers

        Returns: 
            list: list of LLM handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": LLMHanlder - The class of the handler,
            } 
        """
        return [] 

    def get_tts_handlers(self) -> list[dict]:
        """
        Returns the list of TTS handlers

        Returns: 
            list: list of TTS handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": TTSHandler - The class of the handler,
            }
            
        """
        return [] 

    def get_stt_handlers(self) -> list[dict]:
        """
        Returns the list of STT handlers

        Returns:
            list: list of STT handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": STTHandler - The class of the handler,
            }
        """
        return [] 

    def get_memory_handlers(self) -> list[dict]:
        """
        Returns the list of memory handlers

        Returns:
            list: list of memory handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": MemoryHandler - The class of the handler,
            }
        """
        return []

    def get_embedding_handlers(self) -> list[dict]:
        """
        Returns the list of embedding handlers

        Returns:
            list: list of embedding handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": EmbeddingHandler - The class of the handler,
            }
        """
        return []

    def get_rag_handlers(self) -> list[dict]:
        """
        Returns the list of RAG handlers

        Returns:
            list: list of RAG handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": RAGHandler - The class of the handler,
            }
        """
        return []

    def get_websearch_handlers(self) -> list[dict]:
        """
        Returns list of websearch handlers

        Returns:
            list: list of websearch handlers in this format
            {
                "key": "key of handler",
                "title": "title of handler",
                "description": "description of handler",
                "class": WebSearchHandler - The class of handler,
            }
        """
        return []

    def get_interface_handlers(self) -> list[dict]:
        """
        Returns list of interface handlers

        Returns:
            list: list of interface handlers in this format
            {
                "key": "key of handler",
                "title": "title of handler",
                "description": "description of handler",
                "class": Interface - The class of handler,
            }
        """
        return []

    def get_image_generator_handlers(self) -> list[dict]:
        """
        Returns list of image generator handlers

        Returns:
            list: list of image generator handlers in this format
            {
                "key": "key of handler",
                "title": "title of handler",
                "description": "description of handler",
                "class": ImageGeneratorHandler - The class of handler,
            }
        """
        return []
    
    def get_additional_prompts(self) -> list:
        """
        Returns the list of additional prompts

        Returns:
            list: list of additional prompts in this format
            {
                "key": "key of the prompt",
                "setting_name": "name of the settings that gets toggled",
                "title": "Title of the prompt to be shown in settings",
                "description": "Description of the prompt to be shown in settings",
                "editable": bool, If the user can edit the prompt
                "show_in_settings": bool If the prompt should be shown in the settings,
                "default": bool, default value of the setting
                "text": "Default Text of the prompt"
            }            
        """
        return []

    def get_tools(self) -> list:
        return []

    def get_subagents(self) -> list[dict]:
        """Return named subagents contributed by this extension.

        Each entry must contain an extension-local ``id`` plus the canonical
        subagent fields ``name``, ``description``, ``system_prompt``,
        ``include_agents_md``, ``tools``, ``skills``, ``provider``, ``model``,
        ``use_secondary_model`` and ``default_on``. Fields other than ``id``
        and ``name`` may be omitted and receive safe defaults.
        Extension subagents are live, read-only contributions; Newelle never
        copies them into the user's global ``subagents`` setting.
        """
        return []

    def get_modes(self) -> dict:
        """Returns the Modes contributed by this extension.

        Each key is a unique Mode name; each value is a mode dict with the
        same shape used by :class:`ModeManager`::

            {
                "description": "<str>",      # one-line summary
                "icon": "<str>",             # GTK symbolic icon name
                "tools":  {"<tool_name>": "<state>", ...},   # optional
                "skills": {"<skill_name>": "<state>", ...},  # optional
                "subagents": {"<subagent_id>": "<state>", ...}, # optional
                "prompts": {                                      # optional
                    "<prompt_key>": {
                        "state": "<state>",
                        "override": "<replacement text>",         # optional
                    },
                },
            }

        where ``<state>`` is ``"enable"``, ``"remove"`` or ``"no_change"``.

        Extension modes are added only when no mode with that name already
        exists, so they never clobber built-ins or user-created modes.

        Returns:
            dict: ``{name: mode_dict}`` (empty by default).
        """
        return {}

    def get_commands(self) -> list[Command]:
        """Returns the list of slash commands provided by this extension.
        
        Returns:
            list: List of Command objects
        """
        return []

    def get_replace_codeblocks_langs(self) -> list:
        """Get the list of codeblock langs that the extension handles and replaces

        Returns:
            list: list of codeblock langs that the extension handles and replaces 
        """
        return []

    def provides_both_widget_and_answer(self, codeblock: str, lang: str) -> bool:
        """
        Returns True if the extension provides both a widget and an answer

        Args:
            codeblock: str: text in the codeblock generated by the llm
            lang: str: language of the codeblock

        Returns:
            bool: True if the extension provides both a widget and an answer
        """
        return False

    def restore_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None: 
        """
        Restores a GTK widget on chat loading, optional, fallbacks to get_gtk_widget

        Args:
            codeblock: str: text in the codeblock generated by the llm
            lang: str: language of the codeblock
            msg_uuid: str: uuid of the message

        Returns:
            Gtk.Widget: widget to be shown in the chat or None if not provided 
        """
        # Retrocompatibility only, check if get_gtk_widget supports uuid 
        # there is no need to implement this in your extension 
        if len(inspect.signature(self.get_gtk_widget).parameters) == 2:
            return self.get_gtk_widget(codeblock, lang)
        return self.get_gtk_widget(codeblock, lang, msg_uuid)
    
    def get_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None: 
        """
        Returns the GTK widget to be shown in the chat, optional

        Args:
            codeblock: str: text in the codeblock generated by the llm
            lang: str: language of the codeblock
            msg_uuid: str: uuid of the message

        Returns:
            Gtk.Widget: widget to be shown in the chat or None if not provided 
        """
        return None

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        """
        Returns the answer to the codeblock

        Args:
            codeblock: str: text in the codeblock generated by the llm 
            lang: str: language of the codeblock 

        Returns:
            str: answer to the codeblock (will be given to the llm) or None if not provided
        """
        return None

    def preprocess_history(self, history: list, prompts : list) -> tuple[list, list]:
        """
        Called on the history before it is sent to the LLM. History is given in Newelle format
        """
        return history, prompts

    def postprocess_history(self, history: list, bot_response: str) -> tuple[list, str]:
        """
        Called on the history after it is received from the LLM. History is given in Newelle format
        """
        return history, bot_response

    def set_ui_controller(self, ui_controller: UIController):
        self.ui_controller = ui_controller
        
    def set_chat_tab(self, chat_tab):
        """Set the current chat tab for commands to access.
        
        Args:
            chat_tab: The ChatTab instance
        """
        self.chat_tab = chat_tab
        
    def on_uninstall(self):
        """Hook called when the extension is being uninstalled. Override to clean up resources."""
        pass

    def add_tab_menu_entries(self) -> list:
        """List of TabButtonDescriptions 

        Returns:
            list: List of TabButtonDescriptions to add to the add tab menu 
        """
        return []

class ExtensionLoader:
    """
    Class that loads the extensions

    Attributes: 
        extension_dir: directory where the extensions files are located 
        pip: path to the pip directory 
        extension_cache: path to the extension cache directory 
        settings: Gio application settings 
        extensions: list of extensions 
        disabled_extensions: list of disabled extensions 
        codeblocks: list of codeblocks and their corresponding extensions 
        filemap: map from extension id to file name 
    """
    def __init__(self, extension_dir, project_dir=None, pip_path="", extension_cache="", settings=None):
        self.extension_dir = extension_dir
        if project_dir is not None:
            self.project_dir = project_dir
        else:
            self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.pip = pip_path
        self.extension_cache = extension_cache
        self.settings = settings
    
        if self.settings is None:
            self.extensions_settings = {}
        else:
            self.extensions_settings = json.loads(self.settings.get_string("extensions-settings"))

        self.extensions : list[NewelleExtension] = []
        self.extensionsmap : dict[str, NewelleExtension] = {}
        self.disabled_extensions : list[NewelleExtension] = []
        self.codeblocks : dict[str, NewelleExtension] = {}
        self.filemap : dict[str, str] = {}

    def get_extensions(self) -> list[NewelleExtension]:
        return self.extensions

    def get_enabled_extensions(self) -> list[NewelleExtension]:
        return [x for x in self.get_extensions() if x not in self.disabled_extensions]

    _HANDLER_CONTRIBUTION_METHODS = {
        "llm": "get_llm_handlers",
        "tts": "get_tts_handlers",
        "stt": "get_stt_handlers",
        "memory": "get_memory_handlers",
        "embedding": "get_embedding_handlers",
        "rag": "get_rag_handlers",
        "websearch": "get_websearch_handlers",
        "interface": "get_interface_handlers",
        "image_generator": "get_image_generator_handlers",
    }

    def _get_contributing_extensions(
        self, extension_ids=None, include_disabled=False
    ):
        extensions = (
            self.extensions if include_disabled else self.get_enabled_extensions()
        )
        if extension_ids is None:
            return extensions
        extension_ids = set(extension_ids)
        return [
            extension for extension in extensions if extension.id in extension_ids
        ]

    def get_handler_contributions(
        self, extension_ids=None, include_disabled=False
    ) -> dict[str, set[str]]:
        """Return handler keys contributed by the selected extensions."""
        contributions = {
            category: set() for category in self._HANDLER_CONTRIBUTION_METHODS
        }
        for extension in self._get_contributing_extensions(
            extension_ids, include_disabled
        ):
            for category, method_name in self._HANDLER_CONTRIBUTION_METHODS.items():
                try:
                    handlers = getattr(extension, method_name)() or []
                except Exception as error:
                    print(
                        f"Error reading {category} contributions from "
                        f"extension '{extension.id}': {error}"
                    )
                    continue
                contributions[category].update(
                    handler["key"]
                    for handler in handlers
                    if isinstance(handler, dict) and "key" in handler
                )
        return contributions

    def get_contribution_types(
        self, extension_ids=None, include_disabled=False
    ) -> set[str]:
        """Return resource types provided by selected enabled extensions.

        This small capability summary lets the controller avoid rebuilding
        unrelated registries when an extension is added, removed, or toggled.
        """
        extensions = self._get_contributing_extensions(
            extension_ids, include_disabled
        )
        contribution_types = set()
        handler_contributions = self.get_handler_contributions(
            extension_ids, include_disabled
        )
        if handler_contributions["interface"]:
            contribution_types.add("interfaces")
        if any(
            keys
            for category, keys in handler_contributions.items()
            if category != "interface"
        ):
            contribution_types.add("handlers")

        if self.get_subagent_contributions(
            extension_ids, include_disabled
        ):
            contribution_types.add("subagents")

        methods = {
            "tools": "get_tools",
            "prompts": "get_additional_prompts",
            "mini_apps": "add_tab_menu_entries",
            "modes": "get_modes",
        }
        for extension in extensions:
            for contribution_type, method_name in methods.items():
                try:
                    if getattr(extension, method_name)():
                        contribution_types.add(contribution_type)
                except Exception as error:
                    print(
                        f"Error reading {contribution_type} contributions from "
                        f"extension '{extension.id}': {error}"
                    )
        return contribution_types

    def get_subagent_contributions(
        self, extension_ids=None, include_disabled=False
    ) -> dict[str, dict]:
        """Return validated, namespaced subagents from selected extensions."""
        contributions = {}
        for extension in self._get_contributing_extensions(
            extension_ids, include_disabled
        ):
            try:
                extension_subagents = validate_subagent_contributions(extension)
            except Exception as error:
                print(
                    f"Error reading subagent contributions from extension "
                    f"'{extension.id}': {error}"
                )
                continue
            for identifier, definition in extension_subagents.items():
                if identifier in contributions:
                    print(
                        f"Ignoring duplicate extension subagent '{identifier}'"
                    )
                    continue
                contributions[identifier] = definition
        return contributions

    def load_integrations(self, AVAILABLE_INTEGRATIONS):
        for integration in AVAILABLE_INTEGRATIONS:
            integration_class = integration(self.pip, self.extension_cache, self.settings)
            self.extensions_settings[integration_class.id] = {}
            self.save_settings()
            for lang in integration_class.get_replace_codeblocks_langs():
                if lang not in self.codeblocks:
                    self.codeblocks[lang] = integration_class
            self.extensions.append(integration_class)
            self.extensionsmap[integration_class.id] = integration_class

    def set_ui_controller(self, ui_controller):
        for extension in self.get_enabled_extensions():
            extension.set_ui_controller(ui_controller)
            
    def load_extensions(self):
        """Load extensions from the extension directory"""
        sys.path.insert(0, self.project_dir)
        for file in os.listdir(self.extension_dir):
            if file.endswith(".py"):
                try: 
                    spec = importlib.util.spec_from_file_location("newelle.name", os.path.join(self.extension_dir, file))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for class_name, class_obj in module.__dict__.items():
                        if isinstance(class_obj, type) and issubclass(class_obj, NewelleExtension) and class_obj != NewelleExtension:
                            extension = class_obj(self.pip, self.extension_cache, self.settings)
                            # Create entry in settings
                            if extension.id not in self.extensions_settings:
                                self.extensions_settings[extension.id] = {}
                                self.save_settings()

                            # Save properties about enabled and codeblocks
                            if extension.id in self.extensions_settings and ("disabled" not in self.extensions_settings[extension.id] or not self.extensions_settings[extension.id]["disabled"]):
                                for lang in extension.get_replace_codeblocks_langs():
                                    if lang not in self.codeblocks:
                                        self.codeblocks[lang] = extension
                            else:
                                self.disabled_extensions.append(extension)
                            self.extensions.append(extension)
                            self.filemap[extension.id] = file
                            self.extensionsmap[extension.id] = extension
                            break
                except Exception as e:
                    print("Error loding file: ", file, e)
            
        sys.path.remove(self.project_dir)

    def set_handlers(self, llm: LLMHandler, stt: STTHandler, tts:TTSHandler|None, secondary_llm: LLMHandler, embedding: EmbeddingHandler, rag: RAGHandler|None, memory: MemoryHandler|None, websearch: WebSearchHandler | None):
        for extension in self.extensions:
            extension.set_handlers(llm, stt, tts, secondary_llm, embedding, rag, memory, websearch)
    
    def add_tools(self, tool_registry):
        for extension in self.extensions:
            for tool in extension.get_tools():
                if extension in self.disabled_extensions:
                    continue
                tool_registry.register_tool(tool)

    def get_commands(self) -> list:
        """Get all commands from enabled extensions."""
        commands = []
        for extension in self.extensions:
            if extension in self.disabled_extensions:
                continue
            commands.extend(extension.get_commands())
        return commands
    
    def remove_tools(self, tool_registry, extension: NewelleExtension):
        for tool in extension.get_tools():
            tool_registry.remove_tool(tool.name)

    def add_handlers(self, AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAG, AVAILABLE_WEBSEARCH, AVAILABLE_INTERFACES=None, AVAILABLE_IMAGE_GENERATORS=None):
        """Add the handlers of each extension to the available handlers

        Args:
            AVAILABLE_LLMS (): list of available llms
            AVAILABLE_TTS (): list of available tts
            AVAILABLE_STT (): list of available stt
            AVAILABLE_MEMORIES (): list of available memories
            AVAILABLE_EMBEDDINGS (): list of available embeddings
            AVAILABLE_RAG (): list of available rags
            AVAILABLE_WEBSEARCH (): list of available websearch
            AVAILABLE_INTERFACES (): list of available interfaces
        """
        for extension in self.extensions:
            if extension in self.disabled_extensions:
                continue
            handlers = extension.get_llm_handlers()
            for handler in handlers:
                AVAILABLE_LLMS[handler["key"]] = handler
            handlers = extension.get_tts_handlers()
            for handler in handlers:
                AVAILABLE_TTS[handler["key"]] = handler
            handlers = extension.get_stt_handlers()
            for handler in handlers:
                AVAILABLE_STT[handler["key"]] = handler
            handlers = extension.get_memory_handlers()
            for handler in handlers:
                AVAILABLE_MEMORIES[handler["key"]] = handler
            handlers = extension.get_embedding_handlers()
            for handler in handlers:
                AVAILABLE_EMBEDDINGS[handler["key"]] = handler
            handlers = extension.get_rag_handlers()
            for handler in handlers:
                AVAILABLE_RAG[handler["key"]] = handler
            handlers = extension.get_websearch_handlers()
            for handler in handlers:
                AVAILABLE_WEBSEARCH[handler["key"]] = handler
            if AVAILABLE_INTERFACES is not None:
                handlers = extension.get_interface_handlers()
                for handler in handlers:
                    AVAILABLE_INTERFACES[handler["key"]] = handler
            if AVAILABLE_IMAGE_GENERATORS is not None:
                handlers = extension.get_image_generator_handlers()
                for handler in handlers:
                    AVAILABLE_IMAGE_GENERATORS[handler["key"]] = handler

    def add_prompts(self, PROMPTS, AVAILABLE_PROMPTS):
        """Add the prompts of each extension to the available prompts

        Args:
            PROMPTS (): the prompts texts list 
            AVAILABLE_PROMPTS (): the available prompts list 
        """
        for extension in self.extensions:
            if extension in self.disabled_extensions:
                continue
            prompts = extension.get_additional_prompts()
            for prompt in prompts:
                if prompt not in AVAILABLE_PROMPTS:
                    AVAILABLE_PROMPTS.append(prompt)
                PROMPTS[prompt["key"]] = prompt["text"]

    def add_modes(self, mode_manager):
        """Add the modes contributed by each extension to the ModeManager.

        Args:
            mode_manager: the application :class:`ModeManager`. Extension modes
                are merged via :meth:`ModeManager.add_modes`, which never
                clobbers built-ins or user-created modes.
        """
        for extension in self.extensions:
            if extension in self.disabled_extensions:
                continue
            modes = extension.get_modes()
            if modes:
                mode_manager.add_modes(modes)

    def remove_handlers(self, extension, AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAG, AVAILABLE_WEBSEARCH):
        """Remove handlers of an extension

        Args:
            AVAILABLE_LLMS (): list of available llms 
            AVAILABLE_TTS (): list of available tts
            AVAILABLE_STT (): list of available stt
        """
        handlers = extension.get_llm_handlers()
        for handler in handlers:
            AVAILABLE_LLMS.pop(handler["key"])
        handlers = extension.get_tts_handlers()
        for handler in handlers:
            AVAILABLE_TTS.pop(handler["key"])
        handlers = extension.get_stt_handlers()
        for handler in handlers:
            AVAILABLE_STT.pop(handler["key"])
        handlers = extension.get_memory_handlers()
        for handler in handlers:
            AVAILABLE_MEMORIES.pop(handler["key"])
        handlers = extension.get_embedding_handlers()
        for handler in handlers:
            AVAILABLE_EMBEDDINGS.pop(handler["key"])
        handlers = extension.get_rag_handlers()
        for handler in handlers:
            AVAILABLE_RAG.pop(handler["key"])
        handlers = extension.get_websearch_handlers()
        for handler in handlers:
            AVAILABLE_WEBSEARCH.pop(handler["key"])

    def remove_prompts(self, extension, PROMPTS, AVAILABLE_PROMPTS):
        """Remove prompts of an extension

        Args:
            PROMPTS (): the prompts texts list 
            AVAILABLE_PROMPTS (): the available prompts list 
        """
        prompts = extension.get_additional_prompts()
        for prompt in prompts:
            if prompt in AVAILABLE_PROMPTS:
                AVAILABLE_PROMPTS.remove(prompt)
            PROMPTS.pop(prompt["key"])

    def remove_extension(self, extension : NewelleExtension | str):
        """
        Remove an extension - deletes the file

        Args:
            extension: the extension to remove 
        """
        if not isinstance(extension, str):
            extension = extension.id
        ext_obj = self.get_extension_by_id(extension)
        if ext_obj is not None and hasattr(ext_obj, 'on_uninstall'):
            try:
                ext_obj.on_uninstall()
            except Exception as e:
                print(f"Error during extension uninstall cleanup: {e}")
        os.remove(os.path.join(self.extension_dir, self.filemap[extension]))
        self.extensions_settings.pop(extension, None)
        self.save_settings()

    def add_extension(self, file_path: str, filename: str | None = None):
        """
        Add an extension - copies the file

        Args:
            file_path: the path of the file to copy
            filename: optional destination filename
        """
        destination_name = os.path.basename(filename or file_path)
        shutil.copyfile(file_path, os.path.join(self.extension_dir, destination_name))

    def get_extension_by_id(self, id: str) -> NewelleExtension | None:
        """
        Get an extension by its id

        Args:
            id: the id of the extension 

        Returns:
            NewelleExtension | None: the extension or None if not found 
        """
        if id in self.extensionsmap:
            return self.extensionsmap[id]
        return None

    def enable(self, extension : NewelleExtension | str):
        """
        Enable an extension

        Args:
            extension: the extension to enable 
        """
        if not isinstance(extension, str):
            extension = extension.id
        if self.get_extension_by_id(extension) in self.disabled_extensions:
            self.disabled_extensions.remove(self.get_extension_by_id(extension))
        self.extensions_settings[extension]["disabled"] = False
        self.save_settings()

    def disable(self, extension : NewelleExtension | str): 
        """
        Disable an extension

        Args:
            extension: the extension to disable 
        """
        if not isinstance(extension, str):
            extension = extension.id
        self.extensions_settings[extension]["disabled"] = True
        extension_object = self.get_extension_by_id(extension)
        if extension_object not in self.disabled_extensions:
            self.disabled_extensions.append(extension_object)
        self.save_settings()

    def save_settings(self):
        """Save the extensions settings"""
        if self.settings is None:
            return
        self.settings.set_string("extensions-settings", json.dumps(self.extensions_settings))
    
    def check_validity(self, extension : NewelleExtension):
        """
        Check if the extension is valid

        Args:
            extension: the extension to check 

        Returns:
            bool: True if valid, False otherwise 
        """
        if not hasattr(extension, "id") or not hasattr(extension, "name") or len(extension.id) > 50 or len(extension.name) > 50 or extension.id == NewelleExtension.id or extension.name == NewelleExtension.name:
            print("Error: invalid extension, missing id or name")
            return False
        for h in extension.get_llm_handlers():
            if not self.check_handler(h, LLMHandler):
                return False
        for h in extension.get_tts_handlers():
            if not self.check_handler(h, TTSHandler):
                return False
        for h in extension.get_stt_handlers():
            if not self.check_handler(h, STTHandler):
                return False
        for p in extension.get_additional_prompts():
            if not self.check_prompt(p):
                return False
        return True

    def check_handler(self, handler : dict, compare):
        if "key" not in handler or "title" not in handler or "description" not in handler or "class" not in handler:
            print("Error: invalid handler, missing key or title or description or class")
            return False
        if not issubclass(handler["class"], compare):
            print("Error: invalid handler, class does not match")
            return False
        return True

    def check_prompt(self, prompt):
        if "key" not in prompt or "setting_name" not in prompt or "title" not in prompt or "description" not in prompt:
            print("Error: invalid prompt, missing key or setting_name or title or description")
            return False
        return True

    def preprocess_history(self, history: list, prompts : list) -> tuple[list, list]:
        """
        Called on the history before it is sent to the LLM. History is given in Newelle format
        """
        for extension in self.get_enabled_extensions():
            try:
                history, prompts = extension.preprocess_history(history, prompts)
            except Exception as e:
                print(e)
        return history, prompts

    def postprocess_history(self, history: list, bot_response: str) -> tuple[list, str]:
        """
        Called on the history after it is received from the LLM. History is given in Newelle format
        """
        for extension in self.get_enabled_extensions():
            try:
                history, bot_response = extension.postprocess_history(history, bot_response)
            except Exception as e:
                print(e)
        return history, bot_response

    def get_add_tab_buttons(self) -> list:
        buttons = []
        for extension in self.get_enabled_extensions():
            try:
                buttons += extension.add_tab_menu_entries()
            except Exception as e:
                print(e)
        return buttons

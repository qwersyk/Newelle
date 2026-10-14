"""MCP server interface: expose Newelle tools to third-party MCP clients.

Every tool registered in the controller's ``ToolRegistry`` is advertised over
the Model Context Protocol (streamable HTTP transport) with its full JSON
schema — the tool-level lazy loading used for the internal LLM prompt is
intentionally ignored here, remote clients always see complete schemas.
Individual tools can be excluded permanently through the per-tool toggles in
the interface settings, and an optional setting restricts the exposed set to
the tools that are currently enabled in Newelle.

Tools that require user interaction (command confirmations, file-edit
approvals, …) are forwarded to the MCP client as elicitation forms: the
client's user picks one of the ``InteractionOption`` choices, the matching
callback runs inside Newelle and the tool output is returned as the result.
Clients without elicitation support get an error instead.
"""

import asyncio
import json
import threading
import time
from gettext import gettext as _
from urllib.parse import parse_qs

from ...tools import ToolResult
from ..extra_settings import ExtraSettings
from .interface import Interface

# Default endpoint is <host>:<port>/mcp (FastMCP's streamable_http_path).
DEFAULT_PORT = 8081


class BearerAuthMiddleware:
    """Pure-ASGI middleware rejecting requests without the configured API key.

    Follows the same authentication semantics as the OpenAI-compatible API
    interface: ``Authorization: Bearer <key>`` header or ``?api_key=<key>``
    query parameter. When no key is configured the wrapper is not installed
    at all, so unauthenticated local setups pay no overhead.
    """

    def __init__(self, app, api_key: str):
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send):
        if self.api_key and scope.get("type") == "http":
            headers = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in scope.get("headers", [])
            }
            authorization = headers.get("authorization", "")
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            query_key = (query.get("api_key") or [None])[0]
            if authorization.lower() != f"bearer {self.api_key}" and query_key != self.api_key:
                body = json.dumps(
                    {"error": {"message": "Invalid or missing API key", "type": "authentication_error"}}
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class MCPInterface(Interface):
    key = "mcp"
    name = "MCP Server"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._server = None
        # Low-level mcp Server behind the FastMCP wrapper, set when the
        # server is built; its request_context is only valid inside handlers.
        self._lowlevel = None

    @staticmethod
    def get_extra_requirements() -> list:
        return ["mcp", "uvicorn"]

    # ------------------------------------------------------------------ #
    #                            Settings                                 #
    # ------------------------------------------------------------------ #

    def get_extra_settings(self) -> list:
        tool_settings = []
        if self.controller is not None:
            for tool in self.controller.tools.get_all_tools():
                description = (tool.description or "").strip()
                if len(description) > 120:
                    description = description[:120] + "…"
                tool_settings.append(
                    ExtraSettings.ToggleSetting(
                        key=self._exposure_setting_key(tool.name),
                        title=tool.title or tool.name,
                        description=description,
                        default=True,
                    )
                )
        if not tool_settings:
            tool_settings = [
                ExtraSettings.InfoSetting(
                    key="no_tools",
                    title=_("No tools available"),
                    description=_("Tools appear here once integrations and extensions register them."),
                )
            ]
        return [
            ExtraSettings.EntrySetting(
                key="host",
                title=_("Host"),
                description=_("Host address to bind the MCP server to"),
                default="127.0.0.1",
            ),
            ExtraSettings.SpinSetting(
                key="port",
                title=_("Port"),
                description=_("Port to bind the MCP server to"),
                default=DEFAULT_PORT,
                min=1,
                max=65535,
                step=1,
            ),
            ExtraSettings.EntrySetting(
                key="api_key",
                title=_("API Key"),
                description=_("API key required to authenticate MCP clients (leave empty to disable authentication)"),
                default="",
                password=True,
            ),
            ExtraSettings.ToggleSetting(
                key="only_enabled_tools",
                title=_("Only expose enabled tools"),
                description=_("Expose only the tools that are currently enabled in Newelle; tools disabled in the app are hidden from MCP clients"),
                default=False,
            ),
            ExtraSettings.NestedSetting(
                key="exposed_tools",
                title=_("Exposed tools"),
                description=_("Every listed tool is advertised to MCP clients with its full schema. Switch a tool off to never expose it."),
                extra_settings=tool_settings,
            ),
        ]

    @staticmethod
    def _exposure_setting_key(tool_name: str) -> str:
        return f"expose-{tool_name}"

    def _is_tool_exposed(self, tool) -> bool:
        """Whether *tool* may be advertised and executed by MCP clients.

        Unexposed-by-default is intentionally not consulted: this setting is
        the single source of truth for this interface and defaults to exposed.
        ``search_default=False`` keeps the lookup from recursing into
        ``get_extra_settings`` (which rebuilds the per-tool toggle list).
        """
        return bool(self.get_setting(self._exposure_setting_key(tool.name), False, True))

    def _only_enabled_tools(self) -> bool:
        return bool(self.get_setting("only_enabled_tools", False, False))

    def _is_tool_enabled(self, tool) -> bool:
        """Whether *tool* counts as enabled in Newelle right now.

        Delegates to the controller so the global tools settings, the
        websearch toggle and the active Mode overrides apply exactly as they
        do for the internal LLM. Only consulted when the only-enabled setting
        is on; otherwise every registered tool counts as enabled.
        """
        if not self._only_enabled_tools() or self.controller is None:
            return True
        return any(enabled.name == tool.name for enabled in self.controller.get_enabled_tools())

    def _get_exposed_tools(self) -> list:
        if self.controller is None:
            return []
        return [
            tool
            for tool in self.controller.tools.get_all_tools()
            if self._is_tool_exposed(tool) and self._is_tool_enabled(tool)
        ]

    def _get_host(self) -> str:
        return self.get_setting("host", search_default=True, return_value="127.0.0.1")

    def _get_port(self) -> int:
        return int(self.get_setting("port", search_default=True, return_value=DEFAULT_PORT))

    def _get_api_key(self) -> str:
        return self.get_setting("api_key", search_default=True, return_value="")

    # ------------------------------------------------------------------ #
    #                        Server construction                          #
    # ------------------------------------------------------------------ #

    def _build_mcp_server(self):
        import mcp.types as mcp_types
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings

        server = FastMCP(
            name="Newelle",
            instructions="Newelle desktop AI assistant. Tools listed by this server run inside the Newelle application with the user's configured permissions.",
            stateless_http=True,
            # Responses must stream over SSE instead of a single JSON body:
            # in JSON mode the SDK discards every server-initiated message
            # that is not the final response, so the elicitation requests
            # used to resolve interactive tools would never reach the client.
            # Spec-compliant clients accept both content types anyway.
            # The SDK's DNS-rebinding guard rejects every request carrying an
            # Origin header and its allow-list has no wildcard entry, so
            # accepting cross-origin requests (see the CORS layer wired in
            # start()) requires disabling that guard. Content-Type validation
            # still runs regardless of this flag.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        # FastMCP derives tool schemas from Python signatures, which would lose
        # the descriptions and nested types of the Newelle tool schemas. The
        # low-level request handlers below bypass its tool manager entirely and
        # serve the registry's own JSON schemas (no lazy loading).
        lowlevel = server._mcp_server
        self._lowlevel = lowlevel

        @lowlevel.list_tools()
        async def handle_list_tools() -> list:
            return [self._tool_to_mcp(tool, mcp_types) for tool in self._get_exposed_tools()]

        @lowlevel.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> list:
            # Tool execution talks to GTK and may block on user-installed
            # state, so it must never run on the uvicorn event loop thread.
            result = await asyncio.to_thread(self._run_tool, name, arguments)
            if isinstance(result, ToolResult) and result.requires_interaction:
                # Elicitation is a server->client request, so it can only be
                # sent from the event loop, not from the worker threads that
                # run the tool and wait for its output.
                reason = await self._resolve_interaction_via_elicitation(name, result)
                if reason is not None:
                    raise RuntimeError(reason)
            text = await asyncio.to_thread(self._finish_tool_result, name, result)
            return [mcp_types.TextContent(type="text", text=text)]

        return server

    @staticmethod
    def _tool_to_mcp(tool, mcp_types):
        try:
            schema = tool.schema
        except Exception:
            schema = None
        if not isinstance(schema, dict):
            schema = {}
        schema = dict(schema)
        schema.setdefault("type", "object")
        return mcp_types.Tool(
            name=tool.name,
            description=tool.description or "",
            inputSchema=schema,
        )

    # ------------------------------------------------------------------ #
    #                        Tool execution                               #
    # ------------------------------------------------------------------ #

    def _run_tool(self, name: str, arguments):
        """Run *name* on the GTK main thread from a worker thread.

        Returns the raw tool result without consuming it: interactive
        ``ToolResult``s come back still pending so the caller can resolve
        them through elicitation before waiting for the output. Raises on
        unknown/excluded tools; the MCP server turns exceptions into
        ``isError`` tool results.
        """
        controller = self.controller
        if controller is None:
            raise RuntimeError("MCP interface is not connected to the controller")
        tool = controller.tools.get_tool(name)
        if tool is None or not self._is_tool_exposed(tool) or not self._is_tool_enabled(tool):
            raise RuntimeError(f"Tool '{name}' is not exposed by this server")
        with controller.workspace_request():
            return controller.execute_tool_on_main_thread(name, dict(arguments or {}))

    def _finish_tool_result(self, name: str, result) -> str:
        """Wait for the tool output from a worker thread and format it as text."""
        if not isinstance(result, ToolResult):
            if result is None:
                return "Tool executed successfully."
            return str(result)
        if result.requires_interaction:
            # Defensive: unresolved interactions reach here only if the
            # elicitation phase was skipped. Cancel to release the result
            # semaphore instead of deadlocking on get_output().
            try:
                result.cancel()
            except Exception:
                pass
            raise RuntimeError(
                f"Tool '{name}' requires interaction in the Newelle app and cannot be executed through MCP."
            )
        # Non-interactive results set their output before returning; resolved
        # interactive ones when the accepted callback finishes, so this call
        # is what blocks until the tool is done.
        output = result.get_output()
        if result.is_cancelled:
            return _("Tool execution was cancelled.")
        parts = []
        if output is not None:
            if isinstance(output, (dict, list)):
                parts.append(json.dumps(output, ensure_ascii=False, default=str))
            else:
                parts.append(str(output))
        if result.display_text and result.display_text != output:
            parts.append(result.display_text)
        parts.extend(result.get_context_messages())
        return "\n".join(parts) if parts else "Tool executed successfully."

    async def _resolve_interaction_via_elicitation(self, name: str, result: ToolResult) -> str | None:
        """Forward an interactive tool result to the MCP client as elicitation.

        Must run on the event loop inside a request handler — elicitation is
        a server-to-client request, so the session is only reachable there.
        Returns ``None`` once the chosen ``InteractionOption`` callback has
        been invoked; otherwise returns the failure reason shown to the
        client and the result has been cancelled.
        """
        import mcp.types as mcp_types

        def refuse(reason: str) -> str:
            # Cancel to release the result semaphore instead of leaving the
            # tool pending forever.
            try:
                result.cancel()
            except Exception:
                pass
            return reason

        options = list(result.interaction_options or [])
        if not options:
            # Widget-input tools (e.g. ask_user) collect free-form answers
            # through widgets that only exist in the chat UI; there are no
            # options to map onto an elicitation form.
            return refuse(
                _(
                    "Tool '{name}' requires free-form input that can only be provided in the Newelle app and is not supported through MCP."
                ).format(name=name)
            )
        try:
            session = self._lowlevel.request_context.session
        except LookupError:
            return refuse(_("Interaction for tool '{name}' failed: no active MCP session.").format(name=name))
        if not session.check_client_capability(mcp_types.ClientCapabilities(elicitation={})):
            return refuse(
                _(
                    "Tool '{name}' requires interaction and the MCP client does not support elicitation."
                ).format(name=name)
            )
        requested_schema = {
            "type": "object",
            "properties": {
                "choice": {
                    "type": "string",
                    "enum": [option.title for option in options],
                    "description": _("Choose an option to continue"),
                }
            },
            "required": ["choice"],
        }
        try:
            elicit = await session.elicit_form(
                message=_("The tool '{name}' needs your confirmation to proceed.").format(name=name),
                requestedSchema=requested_schema,
            )
        except Exception:
            return refuse(_("Interaction for tool '{name}' failed: the MCP connection was lost.").format(name=name))
        choice = elicit.content.get("choice") if elicit.action == "accept" and elicit.content else None
        index = next((i for i, option in enumerate(options) if option.title == choice), None)
        if index is None:
            # Declined, dismissed, or an answer that matches no option.
            return refuse(_("Interaction for tool '{name}' was declined by the user.").format(name=name))
        # Interactive callbacks are written for off-thread invocation (they
        # marshal any GTK work themselves), same as the chat and web
        # interfaces that resolve interactions remotely.
        await asyncio.to_thread(options[index].callback)
        # The interaction is resolved: clear the flag so the output phase
        # treats this result like any completed tool.
        result.requires_interaction = False
        return None

    # ------------------------------------------------------------------ #
    #                          Lifecycle                                  #
    # ------------------------------------------------------------------ #

    def start(self):
        if self.controller is None:
            return
        if not self.is_installed():
            print("Cannot start MCP server: dependencies not installed")
            return
        try:
            import uvicorn
            from starlette.middleware.cors import CORSMiddleware

            host = self._get_host()
            port = self._get_port()
            app = self._build_mcp_server().streamable_http_app()
            api_key = self._get_api_key()
            if api_key:
                # Auth must sit INSIDE the CORS layer: browsers send preflight
                # OPTIONS requests without credentials, so auth would reject
                # them before CORS could answer, breaking browser clients.
                app = BearerAuthMiddleware(app, api_key)
            # Accept cross-origin requests from any origin (mirrors the
            # OpenAI-compatible API interface). With allow_credentials the
            # origin is reflected instead of "*", which browsers require.
            app = CORSMiddleware(
                app=app,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["Mcp-Session-Id"],
            )
            config = uvicorn.Config(app, host=host, port=port, log_level="warning")
            self._server = uvicorn.Server(config)
            threading.Thread(target=self._server.run, daemon=True, name="mcp-interface").start()
            # Only report success (and write the cross-process state file)
            # once uvicorn actually bound the port, so a port conflict does
            # not leave a stale "running" state behind.
            deadline = time.monotonic() + 5
            while not self._server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            if not self._server.started:
                print(f"Failed to start MCP server on {host}:{port}")
                self._server = None
                return
            self._write_state_file()
            print(f"MCP server started on http://{host}:{port}/mcp")
        except Exception as e:
            print(f"Failed to start MCP server: {e}")
            self._server = None

    def stop(self):
        self._clear_state_file()
        if self._server is not None:
            self._server.should_exit = True
            self._server = None
            print("MCP server stopped")

    def _is_locally_running(self):
        return self._server is not None and not self._server.should_exit

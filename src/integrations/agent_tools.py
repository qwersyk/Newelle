import threading
import json
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
from ..ui.widgets.subagent import SubagentWidget


class AgentToolsIntegration(NewelleExtension):
    id = "agent_tools"
    name = "Agent Tools"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    def send_result(self, result: str):
        res = ToolResult()
        self.result = result
        res.set_output(None)
        return res

    @property
    def controller(self):
        return self.ui_controller.window.controller

    def _run_subagent(self, task: str, system_prompt: str, tools: str, skills: str = ""):
        """Run a subagent with the given task, system prompt, tools and skills.

        Args:
            task: The task description for the subagent.
            system_prompt: System prompt that sets the subagent's behaviour.
            tools: Comma-separated list of tool names to give the subagent.
            skills: Comma-separated list of skill names to activate (optional).
        """
        result = ToolResult()
        widget = SubagentWidget(task)
        result.set_widget(widget)
        subagent_result = result

        def run():
            try:
                ctrl = self.controller

                from ..tools import ToolRegistry
                sub_registry = ToolRegistry()
                requested_tools = [t.strip() for t in tools.split(",") if t.strip()]
                # Add send_result tool
                self.result = None
                sub_registry.register_tool(Tool(
                    name="send_result",
                    description="Send the result of the subagent to the main agent.",
                    func=self.send_result,
                    title="Send Result",
                ))
                for tool_name in requested_tools:
                    tool_obj = ctrl.tools.get_tool(tool_name)
                    if tool_obj is not None:
                        sub_registry.register_tool(tool_obj)

                prompts = [system_prompt]
                if skills.strip() and hasattr(ctrl, "skill_manager"):
                    for skill_name in [s.strip() for s in skills.split(",") if s.strip()]:
                        skill_output = ctrl.skill_manager.activate_skill(skill_name)
                        prompts.append(skill_output)

                tools_prompt_json = sub_registry.get_tools_prompt()
                if tools_prompt_json:
                    from ..constants import PROMPTS
                    tools_instruction = PROMPTS.get("tools", "").replace("{TOOLS}", tools_prompt_json)
                    prompts.append(tools_instruction)
                prompts.append("You MUST call send_result tool at the end of the task. Pass any relevant information to the main agent.")
                chat_id = ctrl.create_call_chat()

                widget.set_status(_("Running…"))

                last_message = [""]

                def on_message(text: str):
                    if text[:len(last_message[-1])] != last_message[-1][:len(last_message[-1])]:
                        last_message.append("\n\n")
                        last_message.append("")
                    last_message[-1] = text
                    widget.update_message("".join(last_message))

                def on_tool_result(tool_name: str, tool_result: ToolResult):
                    widget.set_status(_("Tool: ") + tool_name)
                    widget.add_tool_widget(tool_name, tool_result)

                original_registry = ctrl.tools
                ctrl.tools = sub_registry
                try:
                    final = ctrl.run_llm_with_tools(
                        message=task,
                        chat_id=chat_id,
                        system_prompt=prompts,
                        on_message_callback=on_message,
                        on_tool_result_callback=on_tool_result,
                    )
                finally:
                    ctrl.tools = original_registry

                widget.finish(success=True)
                if self.result is None:
                    result.set_output(final if final else "".join(last_message))
                else:
                    result.set_output(self.result)

            except Exception as e:
                widget.finish(success=False, summary=str(e))
                result.set_output(f"Subagent error: {e}")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return result

    def _restore_subagent(self, tool_uuid: str, task: str, system_prompt: str, tools: str, skills: str = ""):
        widget = SubagentWidget(task)
        widget.finish(success=True, summary=_("Completed"))
        output = self.ui_controller.get_tool_result_by_id(tool_uuid)
        r = ToolResult()
        r.set_widget(widget)
        r.set_output(output)
        return r

    def get_tools(self) -> list:
        return [
            Tool(
                name="run_subagent",
                description=(
                    "Run a subagent to solve a task autonomously. "
                    "The subagent gets its own system prompt and a subset of tools. "
                    "Use this when a task requires multiple tool calls that can be delegated."
                ),
                func=self._run_subagent,
                title="Run Subagent",
                restore_func=self._restore_subagent,
                default_on=True,
                icon_name="system-run-symbolic",
                tools_group=_("Agent"),
            ),
        ]
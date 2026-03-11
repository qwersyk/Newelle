import threading
import json
from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult, create_io_tool
from ..ui.widgets.subagent import SubagentWidget
from ..ui.widgets.scheduled_task import ScheduledTaskWidget


class AgentToolsIntegration(NewelleExtension):
    id = "agent_tools"
    name = "Agent Tools"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.subagent_results = {}

    @property
    def controller(self):
        return self.ui_controller.window.controller

    def _run_subagent(self, task: str, system_prompt: str, tools: str, skills: str = "", tool_uuid=None):
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
        self.subagent_results[tool_uuid] = result
        
        def run():
            try:
                ctrl = self.controller

                from ..tools import ToolRegistry
                sub_registry = ToolRegistry()
                skill_manager = getattr(ctrl, "skill_manager", None)
                requested_tools = [t.strip() for t in tools.split(",") if t.strip()]
                # Add send_result tool
                def send_result(result:str):
                    self.subagent_results[tool_uuid] = result
                    res = ToolResult()
                    res.set_output(None)
                    return res

                sub_registry.register_tool(Tool(
                    name="send_result",
                    description="Send the result of the subagent to the main agent.",
                    func=send_result,
                    title="Send Result",
                ))
                for tool_name in requested_tools:
                    tool_obj = ctrl.tools.get_tool(tool_name)
                    if tool_obj is not None:
                        sub_registry.register_tool(tool_obj)

                prompts = [system_prompt]
                if skills.strip() and skill_manager is not None:
                    for skill_name in [s.strip() for s in skills.split(",") if s.strip()]:
                        skill_output = skill_manager.activate_skill(skill_name)
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

                final = ctrl.run_llm_with_tools(
                    message=task,
                    chat_id=chat_id,
                    system_prompt=prompts,
                    on_message_callback=on_message,
                    on_tool_result_callback=on_tool_result,
                    force_tools_on_main_thread=True,
                    tool_registry=sub_registry,
                    skill_manager=skill_manager,
                )

                widget.finish(success=True)
                if self.subagent_results[tool_uuid] is None:
                    result.set_output(final if final else "".join(last_message))
                else:
                    result.set_output(self.subagent_results[tool_uuid])

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

    def _schedule_task(self, task: str, run_at: str = "", cron: str = ""):
        """Schedule a future agent run in a visible chat."""
        scheduled_task = self.controller.create_scheduled_task(
            task=task,
            run_at=run_at.strip() or None,
            cron=cron.strip() or None,
        )

        result = ToolResult()

        # Create and set the widget
        widget = ScheduledTaskWidget(
            task=task,
            schedule_type=scheduled_task["schedule_type"],
            run_at=scheduled_task.get("run_at"),
            cron=scheduled_task.get("cron"),
            next_run_at=scheduled_task.get("next_run_at"),
            task_id=scheduled_task["id"],
        )
        result.set_widget(widget)

        # Set output as JSON for the LLM
        result.set_output(
            json.dumps(
                {
                    "success": True,
                    "id": scheduled_task["id"],
                    "task": scheduled_task["task"],
                    "schedule_type": scheduled_task["schedule_type"],
                    "run_at": scheduled_task["run_at"],
                    "cron": scheduled_task["cron"],
                    "next_run_at": scheduled_task["next_run_at"],
                    "enabled": scheduled_task["enabled"],
                },
                indent=2,
            )
        )

        return result

    def _restore_schedule_task(self, tool_uuid: str, task: str, run_at: str = "", cron: str = ""):
        """Restore the scheduled task widget from chat history."""
        # Get the saved output from chat history
        output = self.ui_controller.get_tool_result_by_id(tool_uuid)

        # Parse the saved output to get schedule info
        schedule_type = "once"
        saved_run_at = run_at
        saved_cron = cron
        next_run_at = None

        if output:
            try:
                data = json.loads(output)
                schedule_type = data.get("schedule_type", "once")
                saved_run_at = data.get("run_at", run_at)
                saved_cron = data.get("cron", cron)
                next_run_at = data.get("next_run_at")
            except json.JSONDecodeError:
                pass

        result = ToolResult()

        # Create a completed widget
        widget = ScheduledTaskWidget(
            task=task,
            schedule_type=schedule_type,
            run_at=saved_run_at,
            cron=saved_cron,
            next_run_at=next_run_at,
            task_id=tool_uuid[:8] if tool_uuid else "",
        )

        # Mark as completed
        widget.update_status(_("Task Created"), "success")

        result.set_widget(widget)
        result.set_output(output)
        return result
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
            Tool(
                name="schedule_task",
                description=(
                    "Schedule a background agent task that will create a visible chat when it runs. "
                    "Provide either run_at for a one-time run or cron for a recurring schedule."
                    "The task argument is the prompt to be executed by the agent. Give a long and detailed task prompt."
                ),
                func=self._schedule_task,
                title="Schedule Task",
                restore_func=self._restore_schedule_task,
                default_on=True,
                icon_name="alarm-symbolic",
                tools_group=_("Agent"),
            ),
        ]

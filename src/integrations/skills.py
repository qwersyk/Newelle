from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult


class SkillsIntegration(NewelleExtension):
    id = "skills"
    name = "Skills"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.skill_manager = None

    def set_skill_manager(self, skill_manager):
        self.skill_manager = skill_manager

    def _activate_skill(self, name: str):
        if self.skill_manager is None:
            result = ToolResult()
            result.set_output("Skills system not initialized.")
            return result

        output = self.skill_manager.activate_skill(name)
        result = ToolResult()
        result.set_output(output)
        return result

    def get_tools(self) -> list:
        if self.skill_manager is None:
            return []

        enabled = self.skill_manager.get_enabled_skills()
        if not enabled:
            return []

        skill_names = [s.name for s in enabled]
        description = (
            "Load the full instructions for an Agent Skill. "
            "Use this when a task matches one of the available skills. "
            f"Available skills: {', '.join(skill_names)}"
        )

        return [
            Tool(
                name="activate_skill",
                description=description,
                func=self._activate_skill,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the skill to activate",
                            "enum": skill_names,
                        }
                    },
                    "required": ["name"],
                },
                tools_group="Skills",
                icon_name="skills-symbolic",
                default_on=True,
                prompt_editable=False,
            )
        ]

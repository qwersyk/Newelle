import os
import re
import json


class Skill:
    """Represents a single Agent Skill discovered from a SKILL.md file."""

    def __init__(self, name, description, location, body, base_dir, source_dir=None):
        self.name = name
        self.description = description
        self.location = location
        self.body = body
        self.base_dir = base_dir
        self.source_dir = source_dir or base_dir


SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".eggs", "build", "dist"}


def parse_frontmatter(text):
    """Parse YAML frontmatter from a SKILL.md file without requiring PyYAML.

    Handles basic key: value pairs and multi-line values using block scalars
    or unquoted strings with colons.
    """
    if not text.startswith("---"):
        return {}, text

    end_match = re.search(r"\n---\s*\n", text[3:])
    if not end_match:
        return {}, text

    yaml_block = text[3:3 + end_match.start()]
    body = text[3 + end_match.end():]

    result = {}
    for line in yaml_block.strip().splitlines():
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if key:
            result[key] = value

    return result, body.strip()


class SkillManager:
    """Discovers, parses, and manages Agent Skills from multiple skills directories.

    Searches for SKILL.md files in the following directories (in priority order):
      1. Project/<project>/.newelle/skills/  (client-native project location)
      2. Project/<project>/.agents/skills/   (cross-client project location)
      3. User~/.newelle/skills/              (client-native user location)
      4. User~/.agents/skills/               (cross-client user location)

    When skills with the same name exist in multiple directories, the first
    directory in priority order wins.
    """

    def __init__(self, skills_dirs, settings):
        """Initialize with one or more skill directories.

        Args:
            skills_dirs: A single path string or list of paths to search for skills.
            settings: Application settings object.
        """
        if isinstance(skills_dirs, str):
            skills_dirs = [skills_dirs]
        self.skills_dirs = list(skills_dirs)
        self.settings = settings
        self.skills = {}
        self.activated_skills = set()

    def _load_settings(self):
        raw = self.settings.get_string("skills-settings")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save_settings(self, skills_settings):
        self.settings.set_string("skills-settings", json.dumps(skills_settings))

    def discover(self):
        """Scan all skills_dirs for subdirectories containing SKILL.md.

        Skills discovered earlier in the list take priority over later ones
        when names collide.
        """
        self.skills.clear()
        for skills_dir in self.skills_dirs:
            if not os.path.isdir(skills_dir):
                continue
            for entry in self._walk_skills(skills_dir, max_depth=4):
                skill = self._parse_skill(entry, skills_dir)
                if skill is not None and skill.name not in self.skills:
                    self.skills[skill.name] = skill

    def _walk_skills(self, root, max_depth):
        """Yield paths to SKILL.md files within max_depth levels."""
        if max_depth < 0:
            return
        try:
            entries = os.listdir(root)
        except PermissionError:
            return

        for name in entries:
            if name in SKIP_DIRS:
                continue
            full = os.path.join(root, name)
            if not os.path.isdir(full):
                continue
            skill_path = os.path.join(full, "SKILL.md")
            if os.path.isfile(skill_path):
                yield skill_path
            else:
                yield from self._walk_skills(full, max_depth - 1)

    def _parse_skill(self, skill_path, source_dir=None):
        """Parse a SKILL.md file and return a Skill, or None on failure.

        Args:
            skill_path: Path to the SKILL.md file.
            source_dir: The skills directory this skill was discovered in.
        """
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"Skills: could not read {skill_path}: {e}")
            return None

        meta, body = parse_frontmatter(text)
        name = meta.get("name", "")
        description = meta.get("description", "")

        if not description:
            print(f"Skills: skipping {skill_path} (no description)")
            return None

        if not name:
            name = os.path.basename(os.path.dirname(skill_path))

        base_dir = os.path.dirname(skill_path)
        return Skill(
            name=name,
            description=description,
            location=skill_path,
            body=body,
            base_dir=base_dir,
            source_dir=source_dir,
        )

    def is_skill_enabled(self, skill_name):
        skills_settings = self._load_settings()
        if skill_name in skills_settings:
            return skills_settings[skill_name].get("enabled", True)
        return True

    def set_skill_enabled(self, skill_name, enabled):
        skills_settings = self._load_settings()
        if skill_name not in skills_settings:
            skills_settings[skill_name] = {}
        skills_settings[skill_name]["enabled"] = enabled
        self._save_settings(skills_settings)

    def get_enabled_skills(self):
        return [s for s in self.skills.values() if self.is_skill_enabled(s.name)]

    def get_catalog(self):
        """Return a formatted catalog string for the prompt."""
        enabled = self.get_enabled_skills()
        if not enabled:
            return ""

        lines = []
        for skill in enabled:
            lines.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(lines)

    def activate_skill(self, name):
        """Return the full skill body with structured wrapping, or an error message."""
        skill = self.skills.get(name)
        if skill is None:
            return f"Skill '{name}' not found. Available skills: {', '.join(self.skills.keys())}"

        if not self.is_skill_enabled(name):
            return f"Skill '{name}' is disabled."

        self.activated_skills.add(name)

        resources = self._list_resources(skill.base_dir)
        resource_section = ""
        if resources:
            resource_lines = "\n".join(f"  - {r}" for r in resources)
            resource_section = f"\n\nBundled resources:\n{resource_lines}"

        return (
            f"<skill_content name=\"{skill.name}\">\n"
            f"{skill.body}\n\n"
            f"Skill directory: {skill.base_dir}\n"
            f"Relative paths in this skill are relative to the skill directory."
            f"{resource_section}\n"
            f"</skill_content>"
        )

    def _list_resources(self, base_dir, max_files=50):
        """List non-SKILL.md files in the skill directory."""
        resources = []
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f == "SKILL.md":
                    continue
                rel = os.path.relpath(os.path.join(root, f), base_dir)
                resources.append(rel)
                if len(resources) >= max_files:
                    return resources
        return resources

    def remove_skill(self, name):
        """Remove a skill directory from disk and the registry."""
        import shutil
        skill = self.skills.get(name)
        if skill is None:
            return False
        try:
            shutil.rmtree(skill.base_dir)
        except OSError as e:
            print(f"Skills: could not remove {skill.base_dir}: {e}")
            return False
        del self.skills[name]
        skills_settings = self._load_settings()
        skills_settings.pop(name, None)
        self._save_settings(skills_settings)
        return True

    def add_skill_from_path(self, source_dir):
        """Copy a skill directory into the primary skills folder and discover it."""
        import shutil
        if not self.skills_dirs:
            return None
        primary_dir = self.skills_dirs[0]
        os.makedirs(primary_dir, exist_ok=True)
        dir_name = os.path.basename(source_dir)
        dest = os.path.join(primary_dir, dir_name)
        if os.path.exists(dest):
            counter = 1
            while os.path.exists(f"{dest}_{counter}"):
                counter += 1
            dest = f"{dest}_{counter}"
        shutil.copytree(source_dir, dest)
        skill_path = os.path.join(dest, "SKILL.md")
        if os.path.isfile(skill_path):
            skill = self._parse_skill(skill_path, primary_dir)
            if skill is not None:
                self.skills[skill.name] = skill
                return skill
        return None

"""Create and edit modes through a tabbed preferences dialog.

Modes are generic overlays for prompts, tools, skills, and subagents. Every
configurable item has three states: inherit the profile, force enable, or force
disable. Prompt text can additionally be replaced for the lifetime of the mode.
"""

import gettext

from gi.repository import Gtk, Adw, Gdk

from ...modes import (
    BUILT_IN_MODE_NAMES,
    DEFAULT_MODE_ICON,
    MODE_ICON_CHOICES,
    NO_CHANGE,
    ENABLE,
    REMOVE,
)
from .multiline import MultilineEntry

_ = gettext.gettext

class ModeEditorDialog(Adw.PreferencesDialog):
    """Dialog to create or edit a mode."""

    def __init__(self, controller, window, mode_name: str | None = None):
        super().__init__()
        self.controller = controller
        self.window = window
        self.mode_manager = controller.mode_manager

        self.editing = mode_name is not None
        self.original_name = mode_name
        self.is_builtin = mode_name in BUILT_IN_MODE_NAMES
        self._syncing_group_controls = False
        self._tool_controls = {}
        self._tool_group_controls = {}
        self._tool_to_group = {}
        self._prompt_status_labels = {}

        self.set_title(_("Edit Mode") if self.editing else _("New Mode"))
        self.set_search_enabled(False)
        self.set_content_width(760)
        self.set_content_height(720)

        existing = self.mode_manager.get_mode(mode_name) if self.editing else None
        self._working = {
            "name": mode_name or "",
            "description": (existing or {}).get("description", ""),
            "icon": (existing or {}).get("icon", DEFAULT_MODE_ICON),
            "tools": dict((existing or {}).get("tools", {})),
            "skills": dict((existing or {}).get("skills", {})),
            "subagents": dict((existing or {}).get("subagents", {})),
            "prompts": {
                key: dict(config)
                for key, config in (existing or {}).get("prompts", {}).items()
            },
        }

        # Multiple PreferencesPages give the dialog a native libadwaita page
        # switcher, keeping each kind of override focused and scannable.
        self.general_page = self._add_page(
            _("General"), "settings-symbolic", "general"
        )
        self.prompts_page = self._add_page(
            _("Prompts"), "question-round-outline-symbolic", "prompts"
        )
        self.tools_page = self._add_page(_("Tools"), "tools-symbolic", "tools")
        self.skills_page = self._add_page(
            _("Skills"), "skills-symbolic", "skills"
        )
        self.subagents_page = self._add_page(
            _("Subagents"), "system-users-symbolic", "subagents"
        )

        self._build_identity_group()
        self._build_prompts_group()
        self._build_tools_group()
        self._build_skills_group()
        self._build_subagents_group()
        for page in (
            self.general_page,
            self.prompts_page,
            self.tools_page,
            self.skills_page,
            self.subagents_page,
        ):
            self._build_actions_group(page, include_delete=page is self.general_page)

    def _add_page(self, title, icon_name, name):
        page = Adw.PreferencesPage(title=title, icon_name=icon_name, name=name)
        self.add(page)
        return page

    # ------------------------------------------------------------------ #
    # General: name / icon / description
    # ------------------------------------------------------------------ #
    def _build_identity_group(self):
        group = Adw.PreferencesGroup(
            title=_("Mode details"),
            description=_("Choose how this mode appears in the mode switcher"),
        )
        self.general_page.add(group)

        name_row = Adw.EntryRow(title=_("Name"), text=self._working["name"])
        if self.is_builtin:
            name_row.set_editable(False)
        else:
            name_row.connect("changed", self._on_name_changed)
        self.name_row = name_row
        group.add(name_row)

        desc_row = Adw.EntryRow(
            title=_("Description"), text=self._working["description"]
        )
        desc_row.connect("changed", self._on_desc_changed)
        group.add(desc_row)

        icon_row = Adw.ActionRow(title=_("Icon"))
        self.icon_buttons = []
        self.icon_flow = Gtk.FlowBox(
            max_children_per_line=8,
            min_children_per_line=6,
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            row_spacing=4,
            column_spacing=4,
        )

        # Only show icons that exist in the current icon theme (Adwaita/system).
        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        current = self._working["icon"] or DEFAULT_MODE_ICON
        available_icons = [n for n in MODE_ICON_CHOICES if icon_theme.has_icon(n)]
        if current and current not in available_icons and icon_theme.has_icon(current):
            available_icons.insert(0, current)
        if not available_icons:
            available_icons = [DEFAULT_MODE_ICON]

        for icon_name in available_icons:
            button = Gtk.ToggleButton(
                css_classes=["flat", "circular", "mode-icon-picker-btn"],
                tooltip_text=icon_name.replace("-symbolic", "").replace("-", " ").title(),
            )
            button.set_child(Gtk.Image.new_from_icon_name(icon_name))
            if icon_name == current:
                button.set_active(True)

            def on_toggled(b, name=icon_name):
                if b.get_active():
                    self._working["icon"] = name
                    for other in self.icon_buttons:
                        if other is not b:
                            other.set_active(False)
                elif not any(ob.get_active() for ob in self.icon_buttons):
                    b.set_active(True)

            button.connect("toggled", on_toggled)
            self.icon_buttons.append(button)
            self.icon_flow.append(button)

        icon_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=120,
            max_content_height=200,
        )
        icon_scroll.set_child(self.icon_flow)
        icon_row.add_suffix(icon_scroll)
        group.add(icon_row)

    # ------------------------------------------------------------------ #
    # Prompt state and text overrides
    # ------------------------------------------------------------------ #
    def _build_prompts_group(self):
        # Import lazily: constants imports handler widgets during application
        # startup, and importing it at module scope here would form a cycle.
        from ...constants import AVAILABLE_PROMPTS, PROMPTS

        group = Adw.PreferencesGroup(
            title=_("Prompt behavior"),
            description=_(
                "Inherit each profile prompt, force it on or off, and optionally replace its text in this mode"
            ),
        )
        self.prompts_page.add(group)

        if not AVAILABLE_PROMPTS:
            group.add(Adw.ActionRow(title=_("No prompts available")))
            return

        base_prompts = self.controller.newelle_settings.prompts
        for prompt in AVAILABLE_PROMPTS:
            key = prompt["key"]
            config = self._working["prompts"].get(key, {})
            base_text = base_prompts.get(key, PROMPTS.get(key, ""))
            displayed_text = config.get("override", base_text)

            row = Adw.ExpanderRow(
                title=prompt["title"],
                subtitle=prompt["description"],
            )
            row.add_prefix(
                Gtk.Image(
                    icon_name="text-x-generic-symbolic",
                    css_classes=["dim-label"],
                )
            )
            row.add_suffix(
                self._build_state_control(
                    config.get("state", NO_CHANGE),
                    self._on_prompt_state_selected,
                    key,
                )
            )

            editor_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=6,
                margin_start=12,
                margin_end=12,
                margin_top=8,
                margin_bottom=12,
            )
            editor_header = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
            )
            status = Gtk.Label(halign=Gtk.Align.START, hexpand=True)
            status.add_css_class("dim-label")
            self._prompt_status_labels[key] = status
            self._update_prompt_status(key)
            editor_header.append(status)

            reset_button = Gtk.Button(
                icon_name="edit-undo-symbolic",
                css_classes=["flat"],
                valign=Gtk.Align.CENTER,
                tooltip_text=_("Use profile prompt text"),
            )
            editor_header.append(reset_button)
            editor_box.append(editor_header)

            entry = MultilineEntry()
            entry.set_text(displayed_text)
            entry.prompt_key = key
            entry.prompt_base_text = base_text
            entry.set_on_change(self._on_prompt_text_changed)
            reset_button.connect("clicked", self._on_reset_prompt, entry)
            editor_box.append(entry)
            row.add_row(editor_box)
            group.add(row)

    def _prompt_config(self, key):
        return self._working["prompts"].setdefault(
            key, {"state": NO_CHANGE}
        )

    def _on_prompt_state_selected(self, toggle_group, _pspec, key):
        value = toggle_group.props.active_name
        if value:
            self._prompt_config(key)["state"] = value

    def _on_prompt_text_changed(self, entry):
        key = entry.prompt_key
        config = self._prompt_config(key)
        text = entry.get_text()
        if text == entry.prompt_base_text:
            config.pop("override", None)
        else:
            config["override"] = text
        self._update_prompt_status(key)

    def _on_reset_prompt(self, _button, entry):
        entry.set_text(entry.prompt_base_text)

    def _update_prompt_status(self, key):
        status = self._prompt_status_labels.get(key)
        if status is None:
            return
        config = self._working["prompts"].get(key, {})
        status.set_label(
            _("Mode-specific text")
            if "override" in config
            else _("Using profile text")
        )

    # ------------------------------------------------------------------ #
    # Tools: three-state overrides grouped by tool metadata
    # ------------------------------------------------------------------ #
    def _build_tools_group(self):
        group = Adw.PreferencesGroup(
            title=_("Tool behavior"),
            description=_(
                "Set a whole group at once, then refine individual tools if needed"
            ),
        )
        self.tools_page.add(group)

        tools = self.controller.tools.get_all_tools()
        if not tools:
            group.add(Adw.ActionRow(title=_("No tools available")))
            return

        tool_groups = {}
        ungrouped_tools = []
        for tool in tools:
            if tool.tools_group:
                tool_groups.setdefault(tool.tools_group, []).append(tool)
            else:
                ungrouped_tools.append(tool)

        for group_name, grouped_tools in tool_groups.items():
            tool_count = len(grouped_tools)
            tools_label = _("tools") if tool_count != 1 else _("tool")
            group_row = Adw.ExpanderRow(
                title=group_name,
                subtitle=("{} {}").format(tool_count, tools_label),
            )
            group_row.add_prefix(
                Gtk.Image(
                    icon_name="folder-symbolic", css_classes=["dim-label"]
                )
            )

            keys = [tool.name for tool in grouped_tools]
            states = {
                self._working["tools"].get(key, NO_CHANGE) for key in keys
            }
            group_state = states.pop() if len(states) == 1 else None
            group_control = self._build_state_control(
                group_state,
                self._on_tool_group_selected,
                group_name,
                keys,
                tooltip_prefix=_("Apply to group: "),
            )
            self._tool_group_controls[group_name] = group_control
            group_row.add_suffix(group_control)

            for tool in grouped_tools:
                self._tool_to_group[tool.name] = group_name
                control = self._build_state_control(
                    self._working["tools"].get(tool.name, NO_CHANGE),
                    self._on_item_state_selected,
                    "tools",
                    tool.name,
                )
                self._tool_controls[tool.name] = control
                group_row.add_row(
                    self._build_state_row(
                        title=tool.title,
                        subtitle=tool.description,
                        icon_name=tool.icon_name or "tools-symbolic",
                        control=control,
                    )
                )
            group.add(group_row)

        for tool in ungrouped_tools:
            control = self._build_state_control(
                self._working["tools"].get(tool.name, NO_CHANGE),
                self._on_item_state_selected,
                "tools",
                tool.name,
            )
            self._tool_controls[tool.name] = control
            group.add(
                self._build_state_row(
                    title=tool.title,
                    subtitle=tool.description,
                    icon_name=tool.icon_name or "tools-symbolic",
                    control=control,
                )
            )

    def _on_tool_group_selected(
        self, toggle_group, _pspec, group_name, tool_keys
    ):
        if self._syncing_group_controls:
            return
        value = toggle_group.props.active_name
        if not value:
            return

        self._syncing_group_controls = True
        try:
            for key in tool_keys:
                self._working["tools"][key] = value
                control = self._tool_controls.get(key)
                if control is not None and control.props.active_name != value:
                    control.set_active_name(value)
        finally:
            self._syncing_group_controls = False

    def _sync_tool_group(self, tool_key):
        if self._syncing_group_controls:
            return
        group_name = self._tool_to_group.get(tool_key)
        control = self._tool_group_controls.get(group_name)
        if control is None:
            return
        group_keys = [
            key for key, name in self._tool_to_group.items() if name == group_name
        ]
        states = {
            self._working["tools"].get(key, NO_CHANGE) for key in group_keys
        }
        self._syncing_group_controls = True
        try:
            if len(states) == 1:
                control.set_active_name(states.pop())
            else:
                control.set_active(Gtk.INVALID_LIST_POSITION)
        finally:
            self._syncing_group_controls = False

    # ------------------------------------------------------------------ #
    # Skills
    # ------------------------------------------------------------------ #
    def _build_skills_group(self):
        group = Adw.PreferencesGroup(
            title=_("Skill behavior"),
            description=_("Override which skills are available in this mode"),
        )
        self.skills_page.add(group)
        skills = []
        if hasattr(self.controller, "skill_manager"):
            skills = list(self.controller.skill_manager.skills.values())
        if not skills:
            group.add(Adw.ActionRow(title=_("No skills available")))
            return
        for skill in skills:
            control = self._build_state_control(
                self._working["skills"].get(skill.name, NO_CHANGE),
                self._on_item_state_selected,
                "skills",
                skill.name,
            )
            group.add(
                self._build_state_row(
                    title=skill.name,
                    subtitle=skill.description,
                    icon_name="skills-symbolic",
                    control=control,
                )
            )

    # ------------------------------------------------------------------ #
    # Subagents
    # ------------------------------------------------------------------ #
    def _build_subagents_group(self):
        group = Adw.PreferencesGroup(
            title=_("Subagent behavior"),
            description=_(
                "Override which named subagents can be launched in this mode"
            ),
        )
        self.subagents_page.add(group)
        manager = getattr(self.controller, "subagent_manager", None)
        definitions = manager.get_subagents() if manager is not None else {}
        if not definitions:
            group.add(Adw.ActionRow(title=_("No subagents available")))
            return

        if isinstance(definitions, dict):
            items = definitions.items()
        else:
            items = (
                (getattr(definition, "id", ""), definition)
                for definition in definitions
            )
        items = sorted(
            (
                (identifier, definition)
                for identifier, definition in items
                if identifier
            ),
            key=lambda item: str(
                self._subagent_value(item[1], "name", item[0])
            ).casefold(),
        )
        for identifier, definition in items:
            control = self._build_state_control(
                self._working["subagents"].get(identifier, NO_CHANGE),
                self._on_item_state_selected,
                "subagents",
                identifier,
            )
            provider = self._subagent_value(definition, "provider")
            model = self._subagent_value(definition, "model")
            use_secondary_model = bool(
                self._subagent_value(
                    definition, "use_secondary_model", False
                )
            )
            description = self._subagent_value(definition, "description", "")
            if use_secondary_model:
                model_summary = "{} · {}".format(
                    _("Secondary LLM"),
                    model or _("provider default"),
                )
                subtitle = (
                    "{}\n{}".format(description, model_summary)
                    if description
                    else model_summary
                )
            elif provider:
                model_summary = "{} · {}".format(
                    provider, model or _("provider default")
                )
                subtitle = (
                    "{}\n{}".format(description, model_summary)
                    if description
                    else model_summary
                )
            else:
                subtitle = description or _("Uses the main model")
            group.add(
                self._build_state_row(
                    title=self._subagent_value(definition, "name", identifier),
                    subtitle=subtitle,
                    icon_name="system-users-symbolic",
                    control=control,
                )
            )

    @staticmethod
    def _subagent_value(definition, key, default=None):
        if isinstance(definition, dict):
            return definition.get(key, default)
        return getattr(definition, key, default)

    # ------------------------------------------------------------------ #
    # Shared state controls
    # ------------------------------------------------------------------ #
    def _build_state_row(self, title, subtitle, icon_name, control):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.add_prefix(Gtk.Image(icon_name=icon_name))
        row.add_suffix(control)
        return row

    def _build_state_control(
        self,
        current,
        callback,
        *callback_args,
        tooltip_prefix="",
    ):
        control = Adw.ToggleGroup()
        control.add_css_class("state-toggle-group")
        control.set_valign(Gtk.Align.CENTER)
        for value, label, icon_name, style_class in (
            (NO_CHANGE, _("No change"), "edit-undo-symbolic", "dim-label"),
            (ENABLE, _("Enable"), "object-select-symbolic", "success"),
            (REMOVE, _("Disable"), "circle-crossed-symbolic", "error"),
        ):
            toggle = Adw.Toggle()
            toggle.set_name(value)
            toggle.set_tooltip(tooltip_prefix + label)
            icon = Gtk.Image(icon_name=icon_name)
            icon.add_css_class(style_class)
            toggle.set_child(icon)
            control.add(toggle)
        if current in (NO_CHANGE, ENABLE, REMOVE):
            control.set_active_name(current)
        else:
            control.set_active(Gtk.INVALID_LIST_POSITION)
        control.connect("notify::active-name", callback, *callback_args)
        return control

    def _on_item_state_selected(self, toggle_group, _pspec, target, key):
        value = toggle_group.props.active_name
        if not value:
            return
        self._working[target][key] = value
        if target == "tools":
            self._sync_tool_group(key)

    # ------------------------------------------------------------------ #
    # Actions and general handlers
    # ------------------------------------------------------------------ #
    def _build_actions_group(self, page, include_delete=False):
        group = Adw.PreferencesGroup()
        page.add(group)

        save_button = Gtk.Button(
            label=_("Save Mode"),
            css_classes=["suggested-action"],
            hexpand=True,
        )
        save_button.connect("clicked", self._on_save)
        group.add(save_button)

        if include_delete and self.editing and not self.is_builtin:
            delete_button = Gtk.Button(
                label=_("Delete Mode"),
                css_classes=["destructive-action"],
                hexpand=True,
            )
            delete_button.connect("clicked", self._on_delete)
            group.add(delete_button)

    def _on_name_changed(self, row):
        self._working["name"] = row.get_text().strip()
        self.name_row.remove_css_class("error")

    def _on_desc_changed(self, row):
        self._working["description"] = row.get_text()

    # ------------------------------------------------------------------ #
    # Save / delete
    # ------------------------------------------------------------------ #
    def _resolve_name(self) -> str | None:
        name = self._working["name"]
        if not name:
            return None
        if self.is_builtin:
            return self.original_name
        if name in self.mode_manager.get_modes() and (
            not self.editing or name != self.original_name
        ):
            return None
        return name

    def _clean_working_overrides(self):
        tools = {
            key: value
            for key, value in self._working["tools"].items()
            if value != NO_CHANGE
        }
        skills = {
            key: value
            for key, value in self._working["skills"].items()
            if value != NO_CHANGE
        }
        subagents = {
            key: value
            for key, value in self._working["subagents"].items()
            if value != NO_CHANGE
        }
        prompts = {}
        for key, config in self._working["prompts"].items():
            state = config.get("state", NO_CHANGE)
            cleaned = {"state": state}
            if "override" in config:
                cleaned["override"] = config["override"]
            if state != NO_CHANGE or "override" in cleaned:
                prompts[key] = cleaned
        return tools, skills, subagents, prompts

    def _on_save(self, _button):
        name = self._resolve_name()
        if name is None:
            self.name_row.add_css_class("error")
            self.set_visible_page(self.general_page)
            return

        tools, skills, subagents, prompts = self._clean_working_overrides()
        mode_data = {
            "description": self._working["description"],
            "icon": self._working["icon"],
            "tools": tools,
            "skills": skills,
            "subagents": subagents,
            "prompts": prompts,
        }

        if self.editing:
            self.mode_manager.update_mode(
                self.original_name,
                new_name=name,
                **mode_data,
            )
        else:
            self.mode_manager.create_mode(name, **mode_data)

        self._reload_mode_state()
        self.close()

    def _on_delete(self, _button):
        if self.is_builtin or not self.editing:
            return
        self.mode_manager.delete_mode(self.original_name)
        self._reload_mode_state()
        self.close()

    def _reload_mode_state(self):
        active = self.mode_manager.get_active_mode()
        if hasattr(self.controller, "skill_manager"):
            self.controller.skill_manager.set_mode_overrides(
                active.get("skills", {})
            )
        self.controller.update_settings()
        self.window.refresh_mode_buttons()

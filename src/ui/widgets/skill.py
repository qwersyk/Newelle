from gi.repository import Gtk, Pango


class SkillWidget(Gtk.Box):
    """Compact card shown in chat when a skill is activated."""

    def __init__(self, skill_name, skill_description="", resource_count=0):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("card")
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(4)
        self.set_margin_end(4)

        icon = Gtk.Image(
            icon_name="skills-symbolic",
            pixel_size=24,
            valign=Gtk.Align.CENTER,
            margin_start=12,
        )
        icon.add_css_class("accent")
        self.append(icon)

        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            valign=Gtk.Align.CENTER,
            margin_top=10,
            margin_bottom=10,
            hexpand=True,
        )

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_label = Gtk.Label(
            label=skill_name,
            halign=Gtk.Align.START,
            ellipsize=Pango.EllipsizeMode.END,
        )
        name_label.add_css_class("heading")
        title_box.append(name_label)

        badge = Gtk.Label(label="activated")
        badge.add_css_class("success")
        badge.add_css_class("caption")
        title_box.append(badge)

        text_box.append(title_box)

        if skill_description:
            desc_label = Gtk.Label(
                label=skill_description,
                halign=Gtk.Align.START,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=60,
            )
            desc_label.add_css_class("dim-label")
            desc_label.add_css_class("caption")
            text_box.append(desc_label)

        if resource_count > 0:
            res_label = Gtk.Label(
                label=f"{resource_count} bundled resource{'s' if resource_count != 1 else ''}",
                halign=Gtk.Align.START,
            )
            res_label.add_css_class("dim-label")
            res_label.add_css_class("caption")
            text_box.append(res_label)

        self.append(text_box)

        check = Gtk.Image(
            icon_name="object-select-symbolic",
            pixel_size=16,
            valign=Gtk.Align.CENTER,
            margin_end=12,
        )
        check.add_css_class("success")
        self.append(check)

from collections.abc import Callable
from gi.repository import Adw, Gtk, Gdk

class ProfileRow(Adw.ActionRow):
    def __init__(self, profile, picture, selected, add=False, allow_delete=False):
        super().__init__(height_request=50, width_request=250, use_markup=False, activatable=False)
        self.profile = profile
        self.add = add
        # Set properties
        self.on_forget_f = lambda _: None
        self.set_name(profile)
        self.set_title(profile)
        # Create prefix widget (GtkOverlay)
        overlay = Gtk.Overlay(width_request=40)
        self.add_prefix(overlay)

        # Create avatar widget
        if add:
            avatar = Adw.Avatar(size=36, text=profile, icon_name="plus-symbolic")
        elif picture is not None: 
            avatar = Adw.Avatar(custom_image=Gdk.Texture.new_from_filename(picture), text=profile, show_initials=True, size=36)
            avatar.get_last_child().get_last_child().set_icon_size(Gtk.IconSize.NORMAL)
        else:    
            avatar = Adw.Avatar(text=profile, show_initials=True, size=36)
        avatar.set_tooltip_text(_("Select profile"))
        # Signal handler for avatar clicked
        overlay.add_overlay(avatar)

        # Create checkmark widget
        if selected:
            checkmark = Gtk.Image(focusable=False, halign=Gtk.Align.END, valign=Gtk.Align.END)
            checkmark.set_from_icon_name("check-plain-symbolic")
            checkmark.set_pixel_size(11)
            # Apply style to checkmark
            checkmark.add_css_class("blue-checkmark")
            overlay.add_overlay(checkmark)

        if allow_delete:
            # Create suffix widget (GtkButton)
            forget_button = Gtk.Button()
            forget_button.set_icon_name("user-trash-symbolic")
            forget_button.set_valign(Gtk.Align.CENTER)
            forget_button.set_tooltip_text("Delete Profile")
            # Signal handler for forget button clicked
            forget_button.connect("clicked", self.on_forget)
            # Apply style to forget button
            forget_button.add_css_class("circular")
            self.add_suffix(forget_button)

    def set_on_forget(self, f : Callable):
        self.on_forget_f = f

    def on_forget(self, widget):
        self.on_forget_f(self.profile)

from gi.repository import Gtk

def apply_css_to_widget(widget, css_string):
    provider = Gtk.CssProvider()
    context = widget.get_style_context()

    # Load the CSS from the string
    provider.load_from_data(css_string.encode())

    # Add the provider to the widget's style context
    context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)



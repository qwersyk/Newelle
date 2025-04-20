import os
from gi.repository import Gtk, Pango
from ...utility.system import open_folder

class DocumentReaderWidget(Gtk.Box):
    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, **kwargs)
        # Add styling and padding
        self.add_css_class("osd")
        self.add_css_class("toolbar")
        self.add_css_class("code") 
        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._document_widgets = {} # Store revealer, spinner, spinner_revealer per file_path

        # 1. Initial Status Label
        self._status_label = Gtk.Label(
            label="Reading documents...",
            halign=Gtk.Align.START,
        )
        self._status_label.add_css_class("pulsing-label")
        self._status_label.add_css_class("heading")
        self.append(self._status_label)

        # 2. Container for document list
        self._document_list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=5,
            margin_top=5,
            margin_bottom=5
        )
        self.append(self._document_list_box)

    def _open_folder_for_file(self, button, file_path):
        """Callback to open the directory containing the file."""
        folder_path = os.path.dirname(file_path)
        if folder_path:
            open_folder(folder_path)
        else:
            # Handle case where path might be just a filename
            open_folder(".") # Open current directory as fallback

    def _create_document_row(self, file_path):
        """Creates a row widget for a single document."""
        file_name = os.path.basename(file_path)
        
        button = Gtk.Button()
        button.connect("clicked", self._open_folder_for_file, file_path)

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Use a generic document icon
        icon = Gtk.Image(icon_name="text-x-generic-symbolic") # Or "document-open-symbolic"
        row_box.append(icon)

        label = Gtk.Label(
            label=file_name,
            halign=Gtk.Align.START,
            tooltip_text=file_path, # Show full path on hover
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END
        )
        row_box.append(label)

        # Spinner setup for this specific row
        spinner_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=250,
            reveal_child=False # Initially hidden, shown when added
        )
        spinner = Gtk.Spinner(tooltip_text="Reading...")
        spinner_revealer.set_child(spinner)
        row_box.append(spinner_revealer)

        button.set_child(row_box)
        return button, spinner, spinner_revealer

    def add_document(self, file_path):
        """Adds a document entry to the list and shows its spinner."""
        if file_path in self._document_widgets:
            print(f"Warning: Document {file_path} already added.")
            return # Avoid adding duplicates

        row_button, spinner, spinner_revealer = self._create_document_row(file_path)

        # Revealer for the entire row's slide-in animation
        row_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=300
        )
        row_revealer.set_child(row_button)

        self._document_list_box.append(row_revealer)
        row_revealer.set_reveal_child(True) # Animate the row in

        # Start and reveal the spinner for this document
        spinner.start()
        spinner_revealer.set_reveal_child(True)

        # Store references to control the spinner later
        self._document_widgets[file_path] = (row_revealer, spinner, spinner_revealer)

    def finish_document(self, file_path):
        """Call this when processing for a specific document is finished."""
        if file_path in self._document_widgets:
            _row_revealer, spinner, spinner_revealer = self._document_widgets[file_path]
            if spinner.is_spinning(): # Check if it's actually spinning
                 spinner.stop()
            spinner_revealer.set_reveal_child(False) # Hide the spinner
        else:
            print(f"Warning: Tried to finish non-existent document entry: {file_path}")


    def finish_reading(self, final_message="Finished reading documents."):
        """Call this when the entire document reading process is complete."""
        self._status_label.remove_css_class("pulsing-label")
        self._status_label.set_label(final_message)

        # Ensure all spinners are stopped and hidden as a fallback
        for file_path, (_row_revealer, spinner, spinner_revealer) in self._document_widgets.items():
             if spinner.is_spinning():
                 spinner.stop()
             if spinner_revealer.get_reveal_child():
                 spinner_revealer.set_reveal_child(False)

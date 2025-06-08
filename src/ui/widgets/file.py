import os
from gi.repository import Gtk, Gdk

class File(Gtk.Image):
    def __init__(self, path, file_name):
        if os.path.isdir(os.path.join(os.path.expanduser(path), file_name)):
            if file_name == "Desktop":
                name = "user-desktop"
            elif file_name == "Documents":
                name = "folder-documents"
            elif file_name == "Downloads":
                name = "folder-download"
            elif file_name == "Music":
                name = "folder-music"
            elif file_name == "Pictures":
                name = "folder-pictures"
            elif file_name == "Public":
                name = "folder-publicshare"
            elif file_name == "Templates":
                name = "folder-templates"
            elif file_name == "Videos":
                name = "folder-videos"
            elif file_name == ".var/app/io.github.qwersyk.Newelle/Newelle":
                name = "user-bookmarks"
            else:
                name = "folder"
        else:
            name = self._get_file_icon(file_name)
        super().__init__(icon_name=name)

        self.path = path
        self.file_name = file_name
        self.drag_source = Gtk.DragSource.new()
        self.drag_source.set_actions(Gdk.DragAction.COPY)
        self.drag_source.connect("prepare", self.move)
        self.add_controller(self.drag_source)

    def _get_file_icon(self, file_name):
        """Determine the appropriate icon for a file based on its extension."""
        if '.' not in file_name:
            return "text-x-generic"
        
        extension = file_name.lower().split('.')[-1]
        
        # Image files
        image_extensions = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff', 'tif', 'svg', 'webp', 'ico', 'xpm']
        if extension in image_extensions:
            return "image-x-generic"
        
        # Video files
        video_extensions = ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv', 'webm', 'm4v', '3gp', 'ogv']
        if extension in video_extensions:
            return "video-x-generic"
        
        # Audio files
        audio_extensions = ['mp3', 'wav', 'flac', 'ogg', 'aac', 'm4a', 'wma', 'opus']
        if extension in audio_extensions:
            return "audio-x-generic"
        
        # Document files
        if extension == 'pdf':
            return "application-pdf"
        
        doc_extensions = ['doc', 'docx', 'odt', 'rtf']
        if extension in doc_extensions:
            return "x-office-document"
        
        spreadsheet_extensions = ['xls', 'xlsx', 'ods', 'csv']
        if extension in spreadsheet_extensions:
            return "x-office-spreadsheet"
        
        presentation_extensions = ['ppt', 'pptx', 'odp']
        if extension in presentation_extensions:
            return "x-office-presentation"
        
        # Archive files
        archive_extensions = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'deb', 'rpm']
        if extension in archive_extensions:
            return "package-x-generic"
        
        # Code files
        code_extensions = ['py', 'js', 'html', 'css', 'cpp', 'c', 'h', 'java', 'php', 'rb', 'go', 'rs']
        if extension in code_extensions:
            return "text-x-script"
        
        # Web files
        if extension in ['html', 'htm']:
            return "text-html"
        
        if extension in ['css']:
            return "text-css"
        
        # Configuration files
        config_extensions = ['conf', 'cfg', 'ini', 'json', 'xml', 'yaml', 'yml', 'toml']
        if extension in config_extensions:
            return "text-x-generic-template"
        
        # Executable files
        executable_extensions = ['exe', 'msi', 'deb', 'rpm', 'appimage', 'flatpak', 'snap']
        if extension in executable_extensions:
            return "application-x-executable"
        
        # Script files
        script_extensions = ['sh', 'bash', 'zsh', 'fish', 'bat', 'cmd', 'ps1']
        if extension in script_extensions:
            return "text-x-script"
        
        # Text files
        text_extensions = ['txt', 'md', 'rst', 'log', 'readme']
        if extension in text_extensions:
            return "text-x-generic"
        
        # Font files
        font_extensions = ['ttf', 'otf', 'woff', 'woff2', 'eot']
        if extension in font_extensions:
            return "font-x-generic"
        
        # Default fallback
        return "text-x-generic"

    def move(self, drag_source, x, y):
        snapshot = Gtk.Snapshot.new()
        self.do_snapshot(self, snapshot)
        paintable = snapshot.to_paintable()
        drag_source.set_icon(paintable, int(x), int(y))

        data = os.path.normpath(os.path.expanduser(f"{self.path}/{self.file_name}"))
        return Gdk.ContentProvider.new_for_value(data)


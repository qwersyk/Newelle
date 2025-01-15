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
            if file_name[len(file_name) - 4:len(file_name)] in [".png", ".jpg"]:
                name = "image-x-generic"
            else:
                name = "text-x-generic"
        super().__init__(icon_name=name)

        self.path = path
        self.file_name = file_name
        self.drag_source = Gtk.DragSource.new()
        self.drag_source.set_actions(Gdk.DragAction.COPY)
        self.drag_source.connect("prepare", self.move)
        self.add_controller(self.drag_source)

    def move(self, drag_source, x, y):
        snapshot = Gtk.Snapshot.new()
        self.do_snapshot(self, snapshot)
        paintable = snapshot.to_paintable()
        drag_source.set_icon(paintable, int(x), int(y))

        data = os.path.normpath(os.path.expanduser(f"{self.path}/{self.file_name}"))
        return Gdk.ContentProvider.new_for_value(data)


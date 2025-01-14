from gi.repository import GObject, Adw, Gio, Gtk

class ComboRowHelper(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        combo: Adw.ComboRow,
        options: tuple[tuple[str, str]],
        selected_value: str,
    ):
        super().__init__()
        self.combo = combo
        self.__combo = combo
        self.__factory = Gtk.SignalListItemFactory()
        self.__factory.connect("setup", self.__on_setup_listitem)
        self.__factory.connect("bind", self.__on_bind_listitem)
        combo.set_factory(self.__factory)

        self.__store = Gio.ListStore(item_type=self.ItemWrapper)
        i = 0
        selected_index = 0
        for option in options:
            if option[1] == selected_value:
                selected_index = i
            i += 1
            self.__store.append(self.ItemWrapper(option[0], option[1]))
        combo.set_model(self.__store)

        combo.set_selected(selected_index)
        combo.connect("notify::selected-item", self.__on_selected)
    class ItemWrapper(GObject.Object):
        def __init__(self, name: str, value: str):
            super().__init__()
            self.name = name
            self.value = value

    def __on_selected(self, combo: Adw.ComboRow, selected_item: GObject.ParamSpec) -> None:
        value = self.__combo.get_selected_item().value
        self.emit("changed", value)

    def __on_setup_listitem(self, factory: Gtk.ListItemFactory, list_item: Gtk.ListItem) -> None:
        label = Gtk.Label()
        list_item.set_child(label)
        list_item.row_w = label

    def __on_bind_listitem(self, factory: Gtk.ListItemFactory, list_item: Gtk.ListItem) -> None:
        label = list_item.get_child()
        label.set_text(list_item.get_item().name)


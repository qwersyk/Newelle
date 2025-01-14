from gi.repository import Gtk, Pango

class BarChartBox(Gtk.Box):
    def __init__(self, data_dict,percentages):
        Gtk.Box.__init__(self,orientation=Gtk.Orientation.VERTICAL, margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10, css_classes=["card","chart"])

        self.data_dict = data_dict
        max_value = max(self.data_dict.values())
        if percentages and max_value<=100:
            max_value = 100
        for label, value in self.data_dict.items():
            bar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=10, margin_start=10,
                         margin_bottom=10, margin_end=10)

            bar = Gtk.ProgressBar()
            bar.set_fraction(value / max_value)

            label = Gtk.Label(label=label,wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.set_halign(Gtk.Align.CENTER)
            bar_box.append(label)
            bar_box.append(bar)
            self.append(bar_box)


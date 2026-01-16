from gi.repository import Gtk, Gio, GLib, Gdk
import matplotlib.pyplot as plt
import os
from matplotlib.figure import Figure
from matplotlib.backends.backend_gtk4agg import FigureCanvasGTK4Agg


from matplotlib.figure import Figure


class LatexCanvas(FigureCanvasGTK4Agg):
    def __init__(self, latex: str, size: int, color, inline: bool = False) -> None:
        fig = Figure()
        fig.patch.set_alpha(0)
        ax = fig.add_subplot()
        txt = ax.text(0.5, 0.5, r'$' + latex + r'$', fontsize=size, ha='center', va='center', color=(color.red, color.blue, color.green))  
        ax.axis('off')
        fig.tight_layout()
        fig.canvas.draw()
        fig_size = txt.get_window_extent()
        h = int(fig_size.height)
        w = int(fig_size.width)
        self.dims = (w, h)
        super().__init__(fig)
        self.set_hexpand(True)
        self.set_vexpand(True)
        if inline:
            self.set_halign(Gtk.Align.START)
            self.set_valign(Gtk.Align.END)
            self.set_size_request(w, h)
        else:
            self.set_size_request(w, h + int(h * (0.1)))
        self.set_css_classes(['latex_renderer'])

class InlineLatex(Gtk.Box):

    def __init__(self, latex: str, size: int) -> None:
        super().__init__()
        self.color = self.get_style_context().get_color()
        self.latex = latex
        self.size = size
        self.picture = LatexCanvas(latex, self.size, self.color, inline=True)
        if self.picture.dims[0] > 300:
            scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_width=True, hexpand=True)
            scroll.set_child(self.picture)
            scroll.set_size_request(300, -1)
            self.append(scroll)
        else:
            self.append(self.picture)


class DisplayLatex(Gtk.Box): 

    def __init__(self, latex:str, size:int, cache_dir: str, inline: bool = False) -> None:
        super().__init__()
        self.cachedir = cache_dir
        self.size = size 


        self.latex = latex 
        self.color = self.get_style_context().get_color()
        # Create Gtk.Picture
        self.picture = LatexCanvas(latex, self.size, self.color, inline)
        if not inline:
            self.scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, propagate_natural_width=True)
            self.scroll.set_child(self.picture)
            self.create_control_box()
            self.controller()
            overlay = Gtk.Overlay() 
            overlay.set_child(self.scroll)
            overlay.add_overlay(self.control_box)
            self.overlay = overlay
            self.append(overlay)
        else:
            self.append(self.picture)


    def zoom_in(self, *_):
        self.size += 10
        self.picture = LatexCanvas(self.latex, self.size, self.color)
        self.scroll.set_child(self.picture) 
    
    def zoom_out(self, *_):
        if self.size < 10:
            return
        self.size -= 10
        self.picture = LatexCanvas(self.latex, self.size, self.color)
        self.scroll.set_child(self.picture)

    def create_control_box(self):
        self.control_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, css_classes=["flat"], visible=False)
        self.copy_button = Gtk.Button(halign=Gtk.Align.START, css_classes=["flat", "accent"], icon_name="edit-copy-symbolic", valign=Gtk.Align.START)
        self.copy_button.connect("clicked", self.copy_button_clicked)
        
        self.zoom_out_button = Gtk.Button(halign=Gtk.Align.START, css_classes=["flat", "error"], icon_name="zoom-out-symbolic", valign=Gtk.Align.START)
        self.zoom_out_button.connect("clicked", self.zoom_out)
        self.control_box.append(self.zoom_out_button)
        
        self.zoom_in_button = Gtk.Button(halign=Gtk.Align.START, css_classes=["flat", "success"], icon_name="zoom-in-symbolic", valign=Gtk.Align.START)
        self.zoom_in_button.connect("clicked", self.zoom_in)
        self.control_box.append(self.zoom_in_button)


        self.control_box.append(self.copy_button)


    def controller(self):
        ev = Gtk.EventControllerMotion.new()
        ev.connect("enter", lambda x, y, data: self.control_box.set_visible(True))
        ev.connect("leave", lambda data: self.control_box.set_visible(False))
        self.add_controller(ev)
    
    def copy_button_clicked(self, widget):
        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_for_value(self.latex))
        self.copy_button.set_icon_name("object-select-symbolic")


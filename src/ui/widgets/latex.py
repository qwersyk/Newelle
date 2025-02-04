from gi.repository import Gtk, Gio, GLib
import matplotlib.pyplot as plt
import uuid
import os 

class DisplayLatex(Gtk.Box):
    cachedir = GLib.get_user_data_dir()
 
    def __init__(self, latex:str, size:int) -> None:
       super().__init__(width_request=-1)
       color = self.get_style_context().lookup_color("window_fg_color")[1]
       id = latex + str(size) + str(color.red) + str(color.green) + str(color.blue)
        
       # Create equation image
       plt.figure()
       plt.text(0.5, 0.5, r'$' + latex + r'$', fontsize=100, ha='center', color=(color.red, color.blue, color.green)) 
       plt.axis('off')
       # Get file name and save it in the cache
       fname = os.path.join(self.cachedir, uuid.uuid4().hex + '-symbolic.svg')

       plt.tight_layout(pad=0)
       plt.savefig(fname, transparent=True, bbox_inches='tight', pad_inches=0)
        # Create Gtk.Picture
       self.picture = Gtk.Picture()
       self.picture.set_file(Gio.File.new_for_path(fname)) 
       self.picture.set_size_request(-1, size)
       self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
       plt.close()

       self.append(self.picture)

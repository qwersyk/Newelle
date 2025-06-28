import os
import sys
import threading
import select
from gi.repository import GLib


class StdoutCapture:
    """Custom stdout capture class that forwards output to original stdout and callback"""
    
    def __init__(self, callback):
        self.callback = callback
        self.original_stdout = sys.__stdout__
    
    def write(self, text):
        # Forward to original stdout
        self.original_stdout.write(text)
        self.original_stdout.flush()
        
        # Send to callback for monitoring
        if self.callback:
            GLib.idle_add(self.callback, text)
        
        return len(text)
    
    def flush(self):
        self.original_stdout.flush()
    
    def isatty(self):
        return self.original_stdout.isatty()


class StdoutMonitor:
    """File descriptor level stdout monitor that captures subprocess output"""
    
    def __init__(self, callback):
        self.callback = callback
        self.active = False
        self.stdout_read_fd = None
        self.stdout_write_fd = None
        self.original_stdout_fd = None
        self.monitor_thread = None
    
    def start_monitoring(self):
        """Start monitoring stdout at file descriptor level"""
        if self.active:
            return
            
        self.active = True
        
        # Create a pipe for capturing stdout at file descriptor level
        self.stdout_read_fd, self.stdout_write_fd = os.pipe()
        
        # Save the original stdout file descriptor
        self.original_stdout_fd = os.dup(1)  # 1 is stdout
        
        # Redirect stdout to our pipe
        os.dup2(self.stdout_write_fd, 1)
        
        # Start a thread to read from the pipe and forward to both original stdout and our capture
        self.monitor_thread = threading.Thread(target=self._monitor_stdout_fd, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop monitoring stdout"""
        if not self.active:
            return
            
        self.active = False
        
        # Restore the original stdout file descriptor
        if self.original_stdout_fd is not None:
            os.dup2(self.original_stdout_fd, 1)
            os.close(self.original_stdout_fd)
            self.original_stdout_fd = None
        
        # Close our pipe
        if self.stdout_write_fd is not None:
            os.close(self.stdout_write_fd)
            self.stdout_write_fd = None
        if self.stdout_read_fd is not None:
            os.close(self.stdout_read_fd)
            self.stdout_read_fd = None
        
        # Wait for monitoring thread to finish
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1.0)
            self.monitor_thread = None
    
    def _monitor_stdout_fd(self):
        """Monitor the stdout file descriptor in a separate thread"""
        try:
            while self.active:
                # Use select to check if data is available for reading
                if self.stdout_read_fd is None:
                    break
                    
                ready, _, _ = select.select([self.stdout_read_fd], [], [], 0.1)
                
                if ready:
                    # Read data from our pipe
                    try:
                        data = os.read(self.stdout_read_fd, 4096)
                        if data:
                            text = data.decode('utf-8', errors='replace')
                            
                            # Write to original stdout so it still appears in terminal
                            if self.original_stdout_fd is not None:
                                os.write(self.original_stdout_fd, data)
                            
                            # Send to our capture buffer
                            if self.callback:
                                GLib.idle_add(self.callback, text)
                    except OSError:
                        # Pipe was closed
                        break
        except Exception as e:
            print(f"Stdout monitoring error: {e}", file=sys.stderr)
    
    def is_active(self):
        """Check if monitoring is currently active"""
        return self.active 
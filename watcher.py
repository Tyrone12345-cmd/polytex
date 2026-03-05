import os
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class _PDFHandler(FileSystemEventHandler):
    """React to PDF file changes with debounced callback."""

    def __init__(self, callback, debounce=1.0):
        self._callback = callback
        self._debounce = debounce
        self._timer = None
        self._lock = threading.Lock()

    def _fire(self):
        with self._lock:
            self._timer = None
        if self._callback:
            try:
                self._callback()
            except Exception:
                pass

    def _schedule(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def on_any_event(self, event):
        if event.is_directory:
            return
        src = getattr(event, 'src_path', '')
        dest = getattr(event, 'dest_path', '')
        if src.lower().endswith('.pdf') or (dest and dest.lower().endswith('.pdf')):
            self._schedule()


class FileWatcher:
    """Monitor directories for PDF file changes using watchdog."""

    def __init__(self):
        self._observer = None
        self._callback = None
        self._lock = threading.Lock()

    def set_callback(self, callback):
        self._callback = callback

    def watch(self, directories):
        """Start watching directories. Stops any previous watcher."""
        with self._lock:
            self.stop()
            dirs = [d for d in directories if d and os.path.isdir(d)]
            if not dirs:
                return
            handler = _PDFHandler(self._callback)
            self._observer = Observer()
            for d in dirs:
                self._observer.schedule(handler, d, recursive=False)
            self._observer.daemon = True
            self._observer.start()

    def stop(self):
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None

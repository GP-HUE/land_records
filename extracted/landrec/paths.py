"""Path resolution that works both as a normal install and as a frozen .exe.

When packaged with PyInstaller (--onefile), read-only resources (static HTML,
sample documents, the bundled Tesseract) live inside the extracted bundle at
sys._MEIPASS, while writable data (SQLite DB, uploads, signing key) must live
next to the executable so it persists.
"""
import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """Directory holding bundled read-only resources (landrec/, samples/, tesseract/)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # normal install: package dir is .../landrec, so parent is the project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """Directory for writable data (DB, uploads, secret). Next to the exe if frozen."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

__version__ = "0.0.20"
__author__ = "Meshpool"

from . import winproc as _winproc

_winproc.install()   # H-28: no console window flashes for any process we start on Windows
from .api import run_task  # noqa: F401  safe entry point for apps (never raises)

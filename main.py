"""main.py -- Entry point for FastCAD Component Reviewer.

Run this file to launch the application:
    python main.py

Module layout:
    models.py       -- PlacementGroup dataclass
    designators.py  -- Parsing and designator expansion (pure functions)
    fastcad.py      -- FastCadController (window focus + keyboard automation)
    app.py          -- FastCadReviewerApp (UI + review lifecycle + hotkeys)
    main.py         -- This file: entry point only
"""

import os
import sys
import tkinter as tk
import pyautogui

from app import FastCadReviewerApp


def _resource_path(name: str) -> str:
    """Resolve bundled resource paths for both source and PyInstaller builds."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    return os.path.join(base_dir, name)


_ICON_PATH = _resource_path("fastCADreview.ico")


def _set_icon(root: tk.Tk) -> None:
    if os.path.exists(_ICON_PATH):
        root.iconbitmap(_ICON_PATH)


def main() -> None:
    pyautogui.FAILSAFE = True
    root = tk.Tk()
    _set_icon(root)
    FastCadReviewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

"""app.py — FastCAD Component Reviewer — main application class.

Reads description/placement lines from the two text panes, builds a flat
sequence of component designators, and drives FastCAD (via FastCadController)
to select and zoom to each component in turn.

Hotkeys:
    SPACE — advance to the next component
    A     — move to the previous component
    D     — skip to the next designator segment (after a comma on the same line)
    S     — skip to the next line (jump to the next description row)
    W     — skip to the previous line (jump to the previous description row)
    ESC   — stop (preserves position; Start resumes, Reset clears)
"""

import queue
import re
import time
from typing import Dict, List, Optional, Tuple

import keyboard
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from designators import expand_designator_segments, parse_text_lines
from fastcad import FastCadController
from models import PlacementGroup


def _parse_description(description: str) -> str:
    """Remove everything up to and including the first colon in a description.
    
    Examples:
        'CAP-CER-SM:.1uF,0402,25V,10%,X7R' -> '.1uF,0402,25V,10%,X7R'
        'IC-PWR SUPERV-SM:STM6322,2.9V,SOT23-5,140mS,OPEN DRAIN' -> 'STM6322,2.9V,SOT23-5,140mS,OPEN DRAIN'
        'No colon here' -> 'No colon here'
    """
    colon_pos = description.find(':')
    if colon_pos == -1:
        return description
    return description[colon_pos + 1:].strip()


class FastCadReviewerApp:
    APP_TITLE = "FastCAD Component Reviewer"
    DEFAULT_EVENT_LOOP_MS = 5
    KEY_DEBOUNCE_S = 0.1
    ROW_HIGHLIGHT_COLOR = "#2D2D2D"
    STATUS_HIGHLIGHT_COLOR = "#2D2D2D"
    APP_BG_COLOR = "#1F1F1F"
    PANEL_BG_COLOR = "#33495E"
    PANEL_ALT_BG_COLOR = "#2D2D2D"
    INPUT_BG_COLOR = "#1F1F1F"
    FG_COLOR = "#EEEEEE"
    MUTED_FG_COLOR = "#a1a1a1"
    DISABLED_FG_COLOR = "#a1a1a1"
    ACCENT_COLOR = "#33495E"
    ACCENT_PRESS_COLOR = "#256497"
    RESET_BTN_COLOR = "#33495E"
    RESET_BTN_HOVER_COLOR = "#2e495f"
    RESET_BTN_PRESS_COLOR = "#256497"
    BTN_TEXT_COLOR = "#6FC0FE"
    START_BTN_TEXT_COLOR = "#9BCAFE"
    USC_RED = "#F12315"
    BORDER_COLOR = "#2e495f"
    SELECT_BG_COLOR = "#2e495f"

    FONT_UI = ("Bahnschrift", 11)
    FONT_UI_BOLD = ("Bahnschrift", 11, "bold")
    FONT_TITLE = ("Bahnschrift", 21, "bold")
    FONT_SUBTITLE = ("Consolas", 10)
    FONT_STATUS = ("Consolas", 16, "bold")
    FONT_COMPONENT = ("Consolas", 40, "bold")
    FONT_DESCRIPTION = ("Consolas", 28)

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(self.APP_TITLE)
        self.root.geometry("1366x768")
        self.root.configure(bg=self.APP_BG_COLOR)

        # --- Review state ---
        self.groups: List[PlacementGroup] = []
        self.sequence: List[Tuple[int, str]] = []
        self.sequence_segment_ids: List[int] = []
        self.current_index: int = -1
        self.running: bool = False
        self.paused: bool = False

        # --- Hotkey state ---
        self.hotkeys_installed: bool = False
        self.event_queue: queue.Queue[str] = queue.Queue()
        self.last_key_times: Dict[str, float] = {}
        self.left_alt_ready: bool = True
        self.z_hook = None
        self.n_press_hook = None
        self.n_release_hook = None
        self.a_hook = None
        self.w_hook = None

        self.fastcad = FastCadController(app_title=self.APP_TITLE)

        # Whether we've entered 'center mode' where the active row is kept
        # centered in the visible area while reviewing (until near the end).
        self._center_mode = False

        self._build_ui()
        self._install_visual_effects()
        self._install_hotkeys()
        self.root.after(self._get_event_loop_ms(), self._process_event_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_menu()
        self._build_header()
        self._build_config_bar()
        self._build_settings_panel()
        self._build_instructions()
        self._build_status_frame()
        self._build_text_panes()

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Show/Hide Settings", command=self._toggle_settings_panel)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menubar)

    def _toggle_settings_panel(self) -> None:
        if self.settings_frame.winfo_ismapped():
            self.settings_frame.pack_forget()
        else:
            self.settings_frame.pack(fill=tk.X, padx=12, pady=(4, 8), before=self.instructions_label)

    def _build_header(self) -> None:
        frame = tk.Frame(self.root, bg=self.APP_BG_COLOR)
        frame.pack(fill=tk.X, padx=12, pady=(10, 2))

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)

        tk.Label(
            frame,
            text="FastCAD Component Reviewer",
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
            font=self.FONT_TITLE,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            frame,
            text="v1.0",
            bg=self.APP_BG_COLOR,
            fg=self.MUTED_FG_COLOR,
            font=self.FONT_UI_BOLD,
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(12, 0), pady=(6, 0))

        tk.Frame(self.root, bg=self.USC_RED, height=4).pack(fill=tk.X, pady=(4, 4))

    def _build_config_bar(self) -> None:
        bar = tk.Frame(self.root, bg=self.PANEL_ALT_BG_COLOR, padx=12, pady=10)
        bar.pack(fill=tk.X, padx=12, pady=(4, 8))

        controls = tk.Frame(bar, bg=self.PANEL_ALT_BG_COLOR)
        controls.pack(side=tk.LEFT, anchor="w")

        self.start_btn = tk.Button(
            controls,
            text="Start",
            width=14,
            command=self.start_review,
            bg=self.ACCENT_COLOR,
            fg=self.START_BTN_TEXT_COLOR,
            activebackground=self.ACCENT_PRESS_COLOR,
            activeforeground=self.START_BTN_TEXT_COLOR,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=self.FONT_UI_BOLD,
        )
        self.start_btn.pack(side=tk.LEFT, pady=(2, 0))

        self.reset_btn = tk.Button(
            controls,
            text="Reset",
            width=10,
            command=self.reset_review,
            state=tk.DISABLED,
            bg=self.RESET_BTN_COLOR,
            fg=self.BTN_TEXT_COLOR,
            activebackground=self.RESET_BTN_PRESS_COLOR,
            activeforeground=self.BTN_TEXT_COLOR,
            disabledforeground=self.DISABLED_FG_COLOR,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=self.FONT_UI_BOLD,
        )
        self.reset_btn.pack(side=tk.LEFT, padx=(8, 0), pady=(2, 0))

        # Auto-enter checkbox moved to Settings panel (kept as var/widget attributes)

    def _build_settings_panel(self) -> None:
        self.settings_frame = tk.LabelFrame(
            self.root,
            text="Settings",
            padx=10,
            pady=8,
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.FG_COLOR,
            font=self.FONT_UI_BOLD,
            relief=tk.FLAT,
            bd=1,
        )

        tk.Label(self.settings_frame, text="FastCAD window title contains:", bg=self.PANEL_ALT_BG_COLOR, fg=self.FG_COLOR, font=self.FONT_UI).pack(side=tk.LEFT)
        self.window_hint_var = tk.StringVar(value=".FCW")
        self.window_entry = tk.Entry(
            self.settings_frame,
            textvariable=self.window_hint_var,
            width=24,
            bg=self.INPUT_BG_COLOR,
            fg=self.FG_COLOR,
            disabledbackground=self.INPUT_BG_COLOR,
            disabledforeground=self.FG_COLOR,
            readonlybackground=self.INPUT_BG_COLOR,
            insertbackground=self.FG_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.ACCENT_COLOR,
            font=self.FONT_UI,
        )
        self.window_entry.pack(side=tk.LEFT, padx=(8, 16), ipady=3)

        tk.Label(self.settings_frame, text="Event loop (ms):", bg=self.PANEL_ALT_BG_COLOR, fg=self.FG_COLOR, font=self.FONT_UI).pack(side=tk.LEFT)
        self.event_loop_ms_var = tk.StringVar(value=str(self.DEFAULT_EVENT_LOOP_MS))
        self.event_loop_entry = tk.Entry(
            self.settings_frame,
            textvariable=self.event_loop_ms_var,
            width=6,
            bg=self.INPUT_BG_COLOR,
            fg=self.FG_COLOR,
            insertbackground=self.FG_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.ACCENT_COLOR,
            font=self.FONT_UI,
        )
        self.event_loop_entry.pack(side=tk.LEFT, padx=(8, 16), ipady=3)

        tk.Label(self.settings_frame, text="Zoom value:", bg=self.PANEL_ALT_BG_COLOR, fg=self.FG_COLOR, font=self.FONT_UI).pack(side=tk.LEFT)
        self.zoom_value_var = tk.StringVar(value="3")
        self.zoom_entry = tk.Entry(
            self.settings_frame,
            textvariable=self.zoom_value_var,
            width=8,
            bg=self.INPUT_BG_COLOR,
            fg=self.FG_COLOR,
            insertbackground=self.FG_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.ACCENT_COLOR,
            font=self.FONT_UI,
        )
        self.zoom_entry.pack(side=tk.LEFT, padx=(8, 16), ipady=3)

        # Auto-enter multiple-match option (moved here from the top config bar)
        self.auto_enter_multiple_var = tk.BooleanVar(value=True)
        self.auto_enter_multiple_chk = tk.Checkbutton(
            self.settings_frame,
            text="Auto-Enter Multiple Match",
            variable=self.auto_enter_multiple_var,
            onvalue=True,
            offvalue=False,
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.FG_COLOR,
            activebackground=self.PANEL_ALT_BG_COLOR,
            activeforeground=self.FG_COLOR,
            selectcolor=self.INPUT_BG_COLOR,
            font=self.FONT_UI,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.auto_enter_multiple_chk.pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0), anchor="s")
        # Keep FastCAD auto-enter flag in sync live so the user may toggle it
        # while a review is running.
        try:
            # trace_add is available on modern Tkinter; fall back to trace for older.
            self.auto_enter_multiple_var.trace_add("write", lambda *a: self._on_auto_enter_changed())
        except Exception:
            try:
                self.auto_enter_multiple_var.trace("w", lambda *a: self._on_auto_enter_changed())
            except Exception:
                pass

    def _build_instructions(self) -> None:
        text = (
            "SPACE = next  |  A = previous  |  D = next after comma  |  "
            "S = next line  |  W = previous line  |  ESC = stop"
        )
        self.instructions_label = tk.Label(
            self.root,
            text=text,
            anchor="w",
            bg=self.APP_BG_COLOR,
            fg=self.MUTED_FG_COLOR,
            font=self.FONT_SUBTITLE,
        )
        self.instructions_label.pack(fill=tk.X, padx=12, pady=(0, 8))

    def _build_status_frame(self) -> None:
        frame = tk.LabelFrame(
            self.root,
            text="Review Status",
            padx=10,
            pady=8,
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
            font=self.FONT_UI_BOLD,
            relief=tk.FLAT,
            bd=1,
        )
        frame.pack(fill=tk.X, padx=12, pady=(4, 8))
        self.status_plain_bg = frame.cget("bg")

        # Progress row: Current and Total centered; show Expected QTY only on mismatch
        progress_row = tk.Frame(frame, bg=self.APP_BG_COLOR)
        progress_row.pack(fill=tk.X)

        # Center container approach: left and right columns are flexible,
        # center column holds the label group so it remains centered.
        progress_row.grid_columnconfigure(0, weight=1)
        progress_row.grid_columnconfigure(2, weight=1)

        center_group = tk.Frame(progress_row, bg=self.APP_BG_COLOR)
        center_group.grid(row=0, column=1)

        self.expected_qty_var = tk.StringVar(value="")
        self.expected_qty_label = tk.Label(
            center_group, textvariable=self.expected_qty_var,
            font=self.FONT_STATUS,
            justify=tk.CENTER,
            anchor="center",
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
        )

        self.current_var = tk.StringVar(value="")
        self.current_label = tk.Label(
            center_group, textvariable=self.current_var,
            font=self.FONT_STATUS,
            justify=tk.CENTER,
            anchor="center",
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
        )

        self.total_var = tk.StringVar(value="")
        self.total_label = tk.Label(
            center_group, textvariable=self.total_var,
            font=self.FONT_STATUS,
            justify=tk.CENTER,
            anchor="center",
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
        )

        # Default: only show Current and Total centered
        self.current_label.grid(row=0, column=0, padx=(0, 16))
        self.total_label.grid(row=0, column=1)

        self.component_var = tk.StringVar(value="")
        self.component_label = tk.Label(
            frame, textvariable=self.component_var,
            font=self.FONT_COMPONENT,
            justify=tk.CENTER,
            anchor="center",
            fg=self.BTN_TEXT_COLOR,
            background=self.status_plain_bg,
        )
        self.component_label.pack(fill=tk.X, pady=(6, 0))

        self.description_var = tk.StringVar(value="")
        self.description_label = tk.Label(
            frame, textvariable=self.description_var,
            font=self.FONT_DESCRIPTION,
            fg=self.FG_COLOR,
            wraplength=980,
            justify=tk.CENTER, anchor="center",
            background=self.status_plain_bg,
        )
        self.description_label.pack(fill=tk.X, pady=(4, 0))

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(
            frame,
            textvariable=self.status_var,
            fg=self.MUTED_FG_COLOR,
            bg=self.APP_BG_COLOR,
            font=self.FONT_UI,
        ).pack(anchor="w", pady=(6, 0))

    def _build_text_panes(self) -> None:
        container = tk.Frame(self.root, bg=self.APP_BG_COLOR)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        tk.Label(
            container,
            text="Copy QTY, DESCRIPTION, and USAGE columns from the PARTS LIST of the FastCAD drawing below:",
            anchor="w",
            bg=self.APP_BG_COLOR,
            fg=self.MUTED_FG_COLOR,
            font=self.FONT_SUBTITLE,
        ).pack(fill=tk.X, pady=(0, 6))

        # Inner frame uses grid so we can assign column weights (10% / 45% / 45%).
        panes = tk.Frame(container, bg=self.APP_BG_COLOR)
        panes.pack(fill=tk.BOTH, expand=True)
        # Keep a reference so the resize handler can update column min-sizes.
        self.panes = panes
        panes.columnconfigure(0, weight=2, minsize=0)  # QTY  — 10%
        panes.columnconfigure(1, weight=9, minsize=0)  # DESC — 45%
        panes.columnconfigure(2, weight=9, minsize=0)  # USAGE— 45%
        panes.rowconfigure(0, weight=1)
        # Enforce proportional column sizes at small widths by recalculating
        # and applying pixel min-sizes when the panes frame is resized.
        panes.bind("<Configure>", self._on_panes_resize)

        # --- QTY pane ---
        qty_frame = tk.Frame(panes, bg=self.PANEL_ALT_BG_COLOR)
        qty_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        qty_frame.rowconfigure(1, weight=1)
        qty_frame.columnconfigure(0, weight=1)
        tk.Label(
            qty_frame,
            text="QTY",
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.BTN_TEXT_COLOR,
            font=self.FONT_UI_BOLD,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.qty_text = tk.Text(
            qty_frame,
            wrap=tk.NONE,
            height=20,
            bg=self.INPUT_BG_COLOR,
            fg=self.FG_COLOR,
            insertbackground=self.FG_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.ACCENT_COLOR,
            selectbackground=self.SELECT_BG_COLOR,
            font=("Consolas", 11),
            padx=8,
            pady=8,
        )
        self.qty_text.grid(row=1, column=0, sticky="nsew")
        self.qty_text.tag_configure("current_line", background=self.ROW_HIGHLIGHT_COLOR)
        self.qty_text.tag_configure("qty_mismatch", background=self.MISMATCH_BG_COLOR, foreground=self.FG_COLOR)
        try:
            # Ensure mismatch background takes visual precedence over current line tag.
            self.qty_text.tag_raise("qty_mismatch", "current_line")
        except Exception:
            pass

        # --- DESCRIPTION pane ---
        desc_frame = tk.Frame(panes, bg=self.PANEL_ALT_BG_COLOR)
        desc_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        desc_frame.rowconfigure(1, weight=1)
        desc_frame.columnconfigure(0, weight=1)
        tk.Label(
            desc_frame,
            text="DESCRIPTION",
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.BTN_TEXT_COLOR,
            font=self.FONT_UI_BOLD,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.desc_text = tk.Text(
            desc_frame,
            wrap=tk.NONE,
            height=20,
            bg=self.INPUT_BG_COLOR,
            fg=self.FG_COLOR,
            insertbackground=self.FG_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.ACCENT_COLOR,
            selectbackground=self.SELECT_BG_COLOR,
            font=("Consolas", 11),
            padx=8,
            pady=8,
        )
        self.desc_text.grid(row=1, column=0, sticky="nsew")
        self.desc_text.tag_configure("current_line", background=self.ROW_HIGHLIGHT_COLOR)
        self.desc_text.tag_configure("qty_mismatch", background=self.MISMATCH_BG_COLOR, foreground=self.FG_COLOR)
        try:
            self.desc_text.tag_raise("qty_mismatch", "current_line")
        except Exception:
            pass

        # --- USAGE pane ---
        usage_frame = tk.Frame(panes, bg=self.PANEL_ALT_BG_COLOR)
        usage_frame.grid(row=0, column=2, sticky="nsew")
        usage_frame.rowconfigure(1, weight=1)
        usage_frame.columnconfigure(0, weight=1)
        tk.Label(
            usage_frame,
            text="USAGE",
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.BTN_TEXT_COLOR,
            font=self.FONT_UI_BOLD,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.place_text = tk.Text(
            usage_frame,
            wrap=tk.NONE,
            height=20,
            bg=self.INPUT_BG_COLOR,
            fg=self.FG_COLOR,
            insertbackground=self.FG_COLOR,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER_COLOR,
            highlightcolor=self.ACCENT_COLOR,
            selectbackground=self.SELECT_BG_COLOR,
            font=("Consolas", 11),
            padx=8,
            pady=8,
        )
        self.place_text.grid(row=1, column=0, sticky="nsew")
        self.place_text.tag_configure("current_line", background=self.ROW_HIGHLIGHT_COLOR)
        self.place_text.tag_configure("qty_mismatch", background=self.MISMATCH_BG_COLOR, foreground=self.FG_COLOR)
        try:
            self.place_text.tag_raise("qty_mismatch", "current_line")
        except Exception:
            pass

        bold_font = tkfont.Font(font=self.place_text.cget("font")).copy()
        bold_font.configure(weight="bold")
        self.place_text.tag_configure("current_component", font=bold_font)

        self._bind_synchronized_scroll()

    def _bind_synchronized_scroll(self) -> None:
        """Keep all three text panes scrolled to the same vertical position."""
        self.syncing_scroll = False
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.qty_text.bind(event, self._on_scroll)
            self.desc_text.bind(event, self._on_scroll)
            self.place_text.bind(event, self._on_scroll)

    def _on_panes_resize(self, event: tk.Event) -> None:
        """Recalculate and enforce column min-sizes so QTY stays ~10% at all widths.

        Tk grid weight allocation can cause very narrow columns at small overall
        sizes. To keep relative sizing stable we compute target pixel widths and
        set `minsize` on each column accordingly. This method is lightweight
        and safe to call frequently from the configure event.
        """
        try:
            total_w = max(event.width, 1)
            # Desired proportions: 2 / 9 / 9 => total 20
            total_weight = 2 + 9 + 9
            w0 = int(round(total_w * 2 / total_weight))
            w1 = int(round(total_w * 9 / total_weight))
            w2 = total_w - (w0 + w1)
            # Apply minimum sizes to preserve proportions when the window is small.
            self.panes.columnconfigure(0, minsize=w0)
            self.panes.columnconfigure(1, minsize=w1)
            self.panes.columnconfigure(2, minsize=w2)
        except Exception:
            # Guard against unexpected widget state during teardown.
            pass

    def _on_scroll(self, _event: tk.Event) -> None:
        """Sync all panes after any one scrolls."""
        if self.syncing_scroll:
            return
        self.syncing_scroll = True
        try:
            # Read the scroll position of whichever pane just moved
            # by asking the event widget indirectly via after().
            def _sync():
                pos = self.desc_text.yview()[0]
                self.qty_text.yview_moveto(pos)
                self.place_text.yview_moveto(pos)
            self.root.after(5, _sync)
        finally:
            self.syncing_scroll = False

    # ------------------------------------------------------------------
    # Hotkey management
    # ------------------------------------------------------------------

    def _install_hotkeys(self) -> None:
        """Install always-on global hotkeys (Space, Esc). Called once at startup."""
        if self.hotkeys_installed:
            return
        keyboard.on_press_key("space", lambda _: self._enqueue_key("space"), suppress=False)
        keyboard.on_press_key("esc", lambda _: self._enqueue_key("esc"), suppress=False)
        self.hotkeys_installed = True

    def _enable_running_hotkeys(self) -> None:
        """Install run-only hotkeys (A, D, S, W) with suppress=True to block FastCAD typing."""
        if self.z_hook is None:
            self.z_hook = keyboard.on_press_key("d", lambda _: self._enqueue_key("d"), suppress=True)
        if self.n_press_hook is None:
            self.n_press_hook = keyboard.on_press_key("s", self._on_n_press, suppress=True)
        if self.n_release_hook is None:
            self.n_release_hook = keyboard.on_release_key("s", self._on_n_release, suppress=True)
        if self.a_hook is None:
            self.a_hook = keyboard.on_press_key("a", lambda _: self._enqueue_key("a"), suppress=True)
        if self.w_hook is None:
            self.w_hook = keyboard.on_press_key("w", lambda _: self._enqueue_key("w"), suppress=True)

    def _disable_running_hotkeys(self) -> None:
        """Remove run-only hotkeys."""
        for attr in ("z_hook", "n_press_hook", "n_release_hook", "a_hook", "w_hook"):
            hook = getattr(self, attr)
            if hook is not None:
                self._safe_unhook(hook)
                setattr(self, attr, None)

    @staticmethod
    def _safe_unhook(hook) -> None:
        try:
            keyboard.unhook(hook)
        except (KeyError, ValueError):
            pass

    def _on_n_press(self, _: keyboard.KeyboardEvent) -> None:
        # One-shot debounce: fire once per physical N key press.
        if not self.left_alt_ready:
            return
        self.left_alt_ready = False
        self._enqueue_key("s")

    def _on_n_release(self, _: keyboard.KeyboardEvent) -> None:
        self.left_alt_ready = True

    def _enqueue_key(self, key_name: str) -> None:
        now = time.monotonic()
        if now - self.last_key_times.get(key_name, 0.0) < self.KEY_DEBOUNCE_S:
            return
        self.last_key_times[key_name] = now
        self.event_queue.put(key_name)

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def _process_event_queue(self) -> None:
        if self.running and self.fastcad.pending_zout:
            status = self.fastcad.try_complete_zout(self.window_hint_var.get().strip())
            if status:
                self.status_var.set(status)

        try:
            while True:
                self._handle_key_event(self.event_queue.get_nowait())
        except queue.Empty:
            pass

        self.root.after(self._get_event_loop_ms(), self._process_event_queue)

    def _handle_key_event(self, key_name: str) -> None:
        if key_name == "esc" and self.running:
            self.stop_review("Stopped by Esc")
            return

        if not self.running:
            return

        if self.fastcad.pending_zout:
            if key_name == "space" and self.fastcad.pending_zout_sent:
                skipped = self.fastcad.pending_component
                self.fastcad.clear_pending()
                self.status_var.set(f"Skipped {skipped} during warning check.")
                self._move_next()
                return
            if self.fastcad.pending_saw_dialog:
                self.status_var.set(
                    f"Waiting for manual selection of {self.fastcad.pending_component}. "
                    "ZOUT runs automatically after selection closes."
                )
            else:
                self.status_var.set(f"Resolving selection for {self.fastcad.pending_component}...")
            return

        if key_name == "space":
            self._move_next()
        elif key_name == "a":
            self._move_previous()
        elif key_name == "d":
            self._skip_to_next_designator_segment()
        elif key_name == "s":
            self._skip_to_next_group()
        elif key_name == "w":
            self._skip_to_previous_group()

    # ------------------------------------------------------------------
    # Review lifecycle
    # ------------------------------------------------------------------

    def start_review(self) -> None:
        """Toggle button handler: start a fresh review, resume, or stop."""
        if self.running:
            self.stop_review("Stopped by button")
            return

        if self.paused and self.sequence and 0 <= self.current_index < len(self.sequence):
            self.resume_review("Start button")
            return

        self._begin_fresh_review()

    def _begin_fresh_review(self) -> None:
        descriptions = parse_text_lines(self.desc_text.get("1.0", tk.END))
        placements = parse_text_lines(self.place_text.get("1.0", tk.END))

        if not descriptions:
            messagebox.showerror("Missing input", "Please paste at least one description line.")
            return
        if not placements:
            messagebox.showerror("Missing input", "Please paste at least one placement line.")
            return

        pair_count = min(len(descriptions), len(placements))
        if len(descriptions) != len(placements):
            self.status_var.set(
                f"Warning: {len(descriptions)} description lines vs {len(placements)} "
                f"placement lines. Using first {pair_count} pairs."
            )

        groups, group_segments = self._build_groups(descriptions, placements, pair_count)
        if not groups:
            messagebox.showerror("No components", "No valid components were found in placement lines.")
            return

        sequence, segment_ids = self._build_sequence(group_segments)
        if not sequence:
            messagebox.showerror("No components", "No reviewable components were generated.")
            return

        zoom_value = self._get_zoom_value()
        if zoom_value is None:
            return

        self.groups = groups
        self.sequence = sequence
        self.sequence_segment_ids = segment_ids
        self.current_index = 0
        self.paused = False
        self.fastcad.auto_enter_on_multiple_matches = bool(self.auto_enter_multiple_var.get())
        self._set_running(True)
        self._highlight_qty_mismatches()
        self._update_status_labels()
        self._send_to_fastcad(zoom_value)

    def stop_review(self, reason: str = "Stopped") -> None:
        """Stop and preserve position.  Start resumes; Reset clears."""
        self.paused = bool(self.sequence)
        self._set_running(False)
        suffix = ". Press Start to resume or Reset to start over." if self.sequence else ""
        self.status_var.set(f"{reason}{suffix}")

    def reset_review(self, reason: str = "Reset") -> None:
        """Fully clear all review state and return to idle."""
        self.groups = []
        self.sequence = []
        self.sequence_segment_ids = []
        self.current_index = -1
        self.paused = False
        self._set_running(False)
        self._clear_qty_mismatch_highlights()
        self._center_mode = False
        self._update_status_labels()
        self.status_var.set(reason)

    def resume_review(self, trigger: str) -> None:
        """Resume review from the preserved position."""
        if not self.paused or not self.sequence or self.current_index < 0:
            return
        self.paused = False
        self.fastcad.auto_enter_on_multiple_matches = bool(self.auto_enter_multiple_var.get())
        self._set_running(True)
        self.status_var.set(f"Resumed ({trigger}). Moving to next component.")
        self._move_next()

    def _set_running(self, running: bool) -> None:
        """Update running state, hotkeys, and buttons atomically."""
        self.running = running
        self.left_alt_ready = True
        self.fastcad.clear_pending()

        # Lock editable text panes while running, but keep settings editable
        # so the user may tweak behavior without stopping the review.
        text_state = tk.DISABLED if running else tk.NORMAL
        self.qty_text.config(state=text_state)
        self.desc_text.config(state=text_state)
        self.place_text.config(state=text_state)
        # Keep entries and the auto-enter checkbox enabled at all times.
        try:
            self.window_entry.config(state=tk.NORMAL)
            self.event_loop_entry.config(state=tk.NORMAL)
            self.zoom_entry.config(state=tk.NORMAL)
            self.auto_enter_multiple_chk.config(state=tk.NORMAL)
        except Exception:
            pass

        if running:
            self._enable_running_hotkeys()
            self.start_btn.config(text="Stop (Esc)")
            self.reset_btn.config(state=tk.NORMAL)
        else:
            self._disable_running_hotkeys()
            self.start_btn.config(text="Start")
            self.reset_btn.config(state=tk.NORMAL if self.sequence else tk.DISABLED)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _move_next(self) -> None:
        """Advance to the next component; reset if at the end."""
        if self.current_index + 1 >= len(self.sequence):
            self.fastcad.cancel_active_command(self.window_hint_var.get().strip())
            self.reset_review("Completed all components")
            return
        self.current_index += 1
        self._navigate_to_current()

    def _move_previous(self) -> None:
        """Move to the previous component in sequence."""
        if self.current_index <= 0:
            self.status_var.set("Already at the first component.")
            return
        self.current_index -= 1
        self._navigate_to_current()

    def _skip_to_next_group(self) -> None:
        """Jump to the first component of the next description row."""
        current_group = self.sequence[self.current_index][0]
        next_idx = next(
            (i for i in range(self.current_index + 1, len(self.sequence))
             if self.sequence[i][0] != current_group),
            None,
        )
        if next_idx is None:
            self.fastcad.cancel_active_command(self.window_hint_var.get().strip())
            self.reset_review("No additional component types. Review completed.")
            return
        self.current_index = next_idx
        self._navigate_to_current()

    def _skip_to_previous_group(self) -> None:
        """Jump to the first component of the previous description row."""
        current_group = self.sequence[self.current_index][0]
        prev_idx = next(
            (i for i in range(self.current_index - 1, -1, -1)
             if self.sequence[i][0] != current_group),
            None,
        )
        if prev_idx is None:
            self.status_var.set("No previous component line.")
            return
        prev_group = self.sequence[prev_idx][0]
        self.current_index = next(i for i, (g, _) in enumerate(self.sequence) if g == prev_group)
        self._navigate_to_current()

    def _skip_to_next_designator_segment(self) -> None:
        """Jump to the next comma-separated segment within the current row."""
        if len(self.sequence_segment_ids) != len(self.sequence):
            return
        current_group = self.sequence[self.current_index][0]
        current_seg = self.sequence_segment_ids[self.current_index]
        next_idx = next(
            (i for i in range(self.current_index + 1, len(self.sequence))
             if self.sequence[i][0] == current_group
             and self.sequence_segment_ids[i] > current_seg),
            None,
        )
        if next_idx is None:
            # Match SPACE behavior when already at the last segment on this line.
            self._move_next()
            return
        self.current_index = next_idx
        self._navigate_to_current()

    def _navigate_to_current(self) -> None:
        """Refresh UI labels and send the current component to FastCAD."""
        self._update_status_labels()
        zoom_value = self._get_zoom_value()
        if zoom_value is None:
            self.stop_review("Stopped: invalid zoom value")
            return
        self._send_to_fastcad(zoom_value)

    # ------------------------------------------------------------------
    # FastCAD interaction
    # ------------------------------------------------------------------

    def _send_to_fastcad(self, zoom_value: float) -> None:
        if not self.sequence or self.current_index < 0:
            return
        _, comp = self.sequence[self.current_index]
        hint = self.window_hint_var.get().strip()
        if self.fastcad.send_component(hint, comp, zoom_value):
            # Run one immediate check so dialog state can be latched right away.
            status = self.fastcad.try_complete_zout(hint)
            if status:
                self.status_var.set(status)
            else:
                self.status_var.set(f"Resolving selection for {comp}...")
        else:
            self.status_var.set(
                f"FastCAD window not found for hint '{hint}'. "
                "Press SPACE/N to continue once the window is available."
            )

    # ------------------------------------------------------------------
    # Sequence building (pure static helpers)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_groups(
        descriptions: List[str],
        placements: List[str],
        pair_count: int,
    ) -> Tuple[List[PlacementGroup], List[List[List[str]]]]:
        groups: List[PlacementGroup] = []
        group_segments: List[List[List[str]]] = []
        for i in range(pair_count):
            segments = expand_designator_segments(placements[i])
            components = [c for seg in segments for c in seg]
            if components:
                groups.append(PlacementGroup(descriptions[i], components, i))
                group_segments.append(segments)
        return groups, group_segments

    @staticmethod
    def _build_sequence(
        group_segments: List[List[List[str]]],
    ) -> Tuple[List[Tuple[int, str]], List[int]]:
        sequence: List[Tuple[int, str]] = []
        segment_ids: List[int] = []
        for group_idx, segments in enumerate(group_segments):
            for seg_idx, seg_comps in enumerate(segments):
                for comp in seg_comps:
                    sequence.append((group_idx, comp))
                    segment_ids.append(seg_idx)
        return sequence, segment_ids

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _install_visual_effects(self) -> None:
        """Apply subtle hover and press effects to interactive controls."""
        self._bind_button_hover(
            self.start_btn,
            base_bg=self.ACCENT_COLOR,
            hover_bg=self._mix_color(self.ACCENT_COLOR, "#ffffff", 0.1),
            press_bg=self.ACCENT_PRESS_COLOR,
        )
        self._bind_button_hover(
            self.reset_btn,
            base_bg=self.RESET_BTN_COLOR,
            hover_bg=self.RESET_BTN_HOVER_COLOR,
            press_bg=self.RESET_BTN_PRESS_COLOR,
        )

    def _clear_qty_mismatch_highlights(self) -> None:
        for text in (self.qty_text, self.desc_text, self.place_text):
            text.tag_remove("qty_mismatch", "1.0", tk.END)

    def _highlight_qty_mismatches(self) -> None:
        """Highlight rows red when typed QTY does not match computed row count."""
        self._clear_qty_mismatch_highlights()

        qty_lines = [line.strip() for line in self.qty_text.get("1.0", tk.END).splitlines()]
        # If QTY column is not pasted/provided, do not show mismatch errors.
        if not any(qty_lines):
            return

        for group in self.groups:
            row = group.source_line_index
            typed_qty = qty_lines[row] if row < len(qty_lines) else ""
            if not typed_qty:
                continue
            try:
                qty_value = int(typed_qty)
            except (TypeError, ValueError):
                continue

            expected_qty = len(group.components)
            if qty_value != expected_qty:
                line_no = row + 1
                start = f"{line_no}.0"
                end = f"{line_no}.end+1c"
                self.qty_text.tag_add("qty_mismatch", start, end)
                self.desc_text.tag_add("qty_mismatch", start, end)
                self.place_text.tag_add("qty_mismatch", start, end)

    @staticmethod
    def _is_widget_enabled(widget: tk.Widget) -> bool:
        return str(widget.cget("state")) != tk.DISABLED

    def _bind_button_hover(self, button: tk.Button, base_bg: str, hover_bg: str, press_bg: str) -> None:
        def on_enter(_: tk.Event) -> None:
            if self._is_widget_enabled(button):
                button.config(bg=hover_bg)

        def on_leave(_: tk.Event) -> None:
            button.config(bg=base_bg)

        def on_press(_: tk.Event) -> None:
            if self._is_widget_enabled(button):
                button.config(bg=press_bg)

        def on_release(event: tk.Event) -> None:
            if not self._is_widget_enabled(button):
                return
            inside = (0 <= event.x < button.winfo_width()) and (0 <= event.y < button.winfo_height())
            button.config(bg=hover_bg if inside else base_bg)

        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")
        button.bind("<ButtonPress-1>", on_press, add="+")
        button.bind("<ButtonRelease-1>", on_release, add="+")

    @staticmethod
    def _mix_color(color_a: str, color_b: str, t: float) -> str:
        """Linearly blend two #RRGGBB colors."""
        t = min(1.0, max(0.0, t))
        a_r = int(color_a[1:3], 16)
        a_g = int(color_a[3:5], 16)
        a_b = int(color_a[5:7], 16)
        b_r = int(color_b[1:3], 16)
        b_g = int(color_b[3:5], 16)
        b_b = int(color_b[5:7], 16)
        r = round(a_r + (b_r - a_r) * t)
        g = round(a_g + (b_g - a_g) * t)
        b = round(a_b + (b_b - a_b) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _on_auto_enter_changed(self, *args) -> None:
        """Callback when the Auto-Enter checkbox is toggled — update controller live."""
        try:
            self.fastcad.auto_enter_on_multiple_matches = bool(self.auto_enter_multiple_var.get())
        except Exception:
            pass

    def _get_zoom_value(self) -> Optional[float]:
        try:
            v = float(self.zoom_value_var.get().strip())
            if v <= 0:
                raise ValueError
            return v
        except ValueError:
            messagebox.showerror("Invalid zoom value", "Zoom value must be a positive number.")
            return None

    def _get_event_loop_ms(self) -> int:
        """Get event-loop interval from UI. Falls back to default on invalid input."""
        try:
            v = int(self.event_loop_ms_var.get().strip())
            if v <= 0:
                raise ValueError
            return v
        except (ValueError, AttributeError):
            return self.DEFAULT_EVENT_LOOP_MS

    def _update_status_labels(self) -> None:
        if not self.sequence or self.current_index < 0:
            self.expected_qty_var.set("")
            self.current_var.set("")
            self.total_var.set("")
            self.component_var.set("")
            self.description_var.set("")
            self._set_status_highlight(False)
            self._set_qty_mismatch_highlight(False)
            self._clear_highlights()
            return
        group_idx, comp = self.sequence[self.current_index]
        group = self.groups[group_idx]

        # Position within the current component group (1-based)
        within_pos = sum(1 for i in range(self.current_index + 1) if self.sequence[i][0] == group_idx)
        group_total = sum(1 for g, _ in self.sequence if g == group_idx)

        # Read the QTY value for this line from the QTY pane. If the entire
        # QTY column appears empty (user didn't paste it), hide the Expected
        # QTY indicator and do not mark mismatches.
        qty_lines_all = [line.strip() for line in self.qty_text.get("1.0", tk.END).splitlines()]
        qty_column_present = any(qty_lines_all)

        line_no = group.source_line_index + 1
        raw_qty = self.qty_text.get(f"{line_no}.0", f"{line_no}.end").strip()
        expected_qty: Optional[int] = None

        if not qty_column_present:
            # No QTY data pasted at all -> hide expected label and skip mismatch logic
            self.expected_qty_var.set("")
            mismatch = False
        else:
            try:
                expected_qty = int(raw_qty)
            except (ValueError, TypeError):
                expected_qty = None

            if expected_qty is not None:
                self.expected_qty_var.set(f"Expected QTY: {expected_qty}")
                mismatch = expected_qty != group_total
            else:
                self.expected_qty_var.set("Expected QTY: —")
                mismatch = False

        self.current_var.set(f"Current: {within_pos}/{group_total}")
        self.total_var.set(f"Total: {self.current_index + 1}/{len(self.sequence)}")
        self._set_status_highlight(True)
        self._set_qty_mismatch_highlight(mismatch)
        self.component_var.set(comp)
        self.description_var.set(_parse_description(group.description))
        self._highlight_line(group.source_line_index, comp)

    def _set_status_highlight(self, enabled: bool) -> None:
        bg = self.STATUS_HIGHLIGHT_COLOR if enabled else self.status_plain_bg
        self.component_label.config(background=bg)
        self.description_label.config(background=bg)

    MISMATCH_BG_COLOR = "#8B0000"  # dark red — visible but not garish

    def _set_qty_mismatch_highlight(self, mismatch: bool) -> None:
        """Red background on Expected QTY and Current labels when counts differ."""
        if mismatch:
            bg = self.MISMATCH_BG_COLOR
        else:
            bg = self.APP_BG_COLOR

        # When mismatch is True, show Expected QTY to the left of Current and
        # shift Current/Total right. When False, hide Expected and pack
        # Current/Total left again.
        try:
            if mismatch:
                # Place expected at column 0, move current->1, total->2
                self.expected_qty_label.grid(row=0, column=0, padx=(0, 16))
                self.current_label.grid_configure(row=0, column=1)
                self.total_label.grid_configure(row=0, column=2)
                self.expected_qty_label.config(background=bg)
                self.current_label.config(background=bg)
                # keep total with normal background
                self.total_label.config(background=self.APP_BG_COLOR)
            else:
                # Hide expected and collapse current/total to cols 0/1.
                self.expected_qty_label.grid_remove()
                self.current_label.grid_configure(row=0, column=0)
                self.total_label.grid_configure(row=0, column=1)
                self.current_label.config(background=self.APP_BG_COLOR)
                self.total_label.config(background=self.APP_BG_COLOR)
        except tk.TclError:
            # Defensive: ignore grid errors if widgets aren't mapped yet.
            pass

    def _clear_highlights(self) -> None:
        self.qty_text.tag_remove("current_line", "1.0", tk.END)
        self.desc_text.tag_remove("current_line", "1.0", tk.END)
        self.place_text.tag_remove("current_line", "1.0", tk.END)
        self.place_text.tag_remove("current_component", "1.0", tk.END)

    def _highlight_line(self, source_line_index: int, current_component: str) -> None:
        """Highlight the active row in both panes and bold the specific designator."""
        self._clear_highlights()
        line_no = source_line_index + 1
        start = f"{line_no}.0"
        end = f"{line_no}.end+1c"

        self.qty_text.tag_add("current_line", start, end)
        self.desc_text.tag_add("current_line", start, end)
        self.place_text.tag_add("current_line", start, end)

        line_text = self.place_text.get(start, f"{line_no}.end")
        match = re.search(
            rf"(?<![A-Z0-9]){re.escape(current_component)}(?![A-Z0-9])", line_text
        )
        if match:
            self.place_text.tag_add(
                "current_component",
                f"{line_no}.{match.start()}",
                f"{line_no}.{match.end()}",
            )
        else:
            # For expanded ranges like C1-C60, emphasize the hyphen while stepping through members.
            comp_match = re.fullmatch(r"([A-Z]+)(\d+)", current_component)
            if comp_match:
                comp_prefix = comp_match.group(1)
                comp_num = int(comp_match.group(2))
                for range_match in re.finditer(r"\b([A-Z]+)(\d+)\s*-\s*([A-Z]+)?(\d+)\b", line_text):
                    start_prefix = range_match.group(1)
                    start_num = int(range_match.group(2))
                    end_prefix = range_match.group(3) or start_prefix
                    end_num = int(range_match.group(4))

                    if comp_prefix != start_prefix or comp_prefix != end_prefix:
                        continue

                    lo, hi = sorted((start_num, end_num))
                    if lo <= comp_num <= hi:
                        dash_pos = line_text.find("-", range_match.start(), range_match.end())
                        if dash_pos != -1:
                            self.place_text.tag_add(
                                "current_component",
                                f"{line_no}.{dash_pos}",
                                f"{line_no}.{dash_pos + 1}",
                            )
                        break

        # Smart scrolling: compute desired top line and scroll the reference
        # pane by an integer number of lines, then sync other panes to the
        # reference fraction. Using integer scroll steps prevents small
        # fractional rounding drift that previously allowed the centered line
        # to slowly move out of view.
        try:
            ref = self.desc_text
            top_line = int(ref.index("@0,0").split(".")[0])
            line_h = tkfont.Font(font=ref.cget("font")).metrics("linespace") or 14
            visible_lines = max(1, int(max(1, ref.winfo_height()) / line_h))
            total_lines = int(ref.index("end-1c").split(".")[0])

            current_middle = top_line + visible_lines // 2
            if (not self._center_mode) and (line_no > current_middle):
                self._center_mode = True

            if self._center_mode and total_lines > visible_lines:
                desired_top = max(1, line_no - visible_lines // 2)
                max_top = max(1, total_lines - visible_lines + 1)
                desired_top = min(desired_top, max_top)

                # Scroll the reference widget by integer lines to reach desired_top.
                delta = int(desired_top - top_line)
                if delta != 0:
                    ref.yview_scroll(delta, "units")
                # Now get the canonical fraction and apply to all panes.
                frac = ref.yview()[0]
                for text in (self.qty_text, self.desc_text, self.place_text):
                    try:
                        text.yview_moveto(frac)
                    except Exception:
                        pass
            else:
                for text in (self.qty_text, self.desc_text, self.place_text):
                    try:
                        text.see(start)
                    except Exception:
                        pass
        except Exception:
            for text in (self.qty_text, self.desc_text, self.place_text):
                try:
                    text.see(start)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        try:
            self._disable_running_hotkeys()
            keyboard.unhook_all()
        except Exception:
            pass
        self.root.destroy()

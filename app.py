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

        self._build_ui()
        self._install_visual_effects()
        self._install_hotkeys()
        self.root.after(self._get_event_loop_ms(), self._process_event_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_header()
        self._build_config_bar()
        self._build_instructions()
        self._build_status_frame()
        self._build_text_panes()

    def _build_header(self) -> None:
        frame = tk.Frame(self.root, bg=self.APP_BG_COLOR)
        frame.pack(fill=tk.X, padx=12, pady=(10, 2))

        tk.Label(
            frame,
            text="FastCAD Component Reviewer",
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
            font=self.FONT_TITLE,
            anchor="w",
        ).pack(fill=tk.X)
        tk.Frame(self.root, bg=self.USC_RED, height=4).pack(fill=tk.X, pady=(4, 4))

    def _build_config_bar(self) -> None:
        bar = tk.Frame(self.root, bg=self.PANEL_ALT_BG_COLOR, padx=12, pady=10)
        bar.pack(fill=tk.X, padx=12, pady=(4, 8))

        tk.Label(bar, text="FastCAD window title contains:", bg=self.PANEL_ALT_BG_COLOR, fg=self.FG_COLOR, font=self.FONT_UI).pack(side=tk.LEFT)
        self.window_hint_var = tk.StringVar(value=".FCW")
        self.window_entry = tk.Entry(
            bar,
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

        tk.Label(bar, text="Event loop (ms):", bg=self.PANEL_ALT_BG_COLOR, fg=self.FG_COLOR, font=self.FONT_UI).pack(side=tk.LEFT)
        self.event_loop_ms_var = tk.StringVar(value=str(self.DEFAULT_EVENT_LOOP_MS))
        event_loop_entry = tk.Entry(
            bar,
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
        event_loop_entry.pack(side=tk.LEFT, padx=(8, 16), ipady=3)

        tk.Label(bar, text="Zoom value:", bg=self.PANEL_ALT_BG_COLOR, fg=self.FG_COLOR, font=self.FONT_UI).pack(side=tk.LEFT)
        self.zoom_value_var = tk.StringVar(value="2.5")
        zoom_entry = tk.Entry(
            bar,
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
        zoom_entry.pack(side=tk.LEFT, padx=(8, 16), ipady=3)

        self.start_btn = tk.Button(
            bar,
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
        self.start_btn.pack(side=tk.LEFT)

        self.reset_btn = tk.Button(
            bar,
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
        self.reset_btn.pack(side=tk.LEFT, padx=(8, 0))

    def _build_instructions(self) -> None:
        text = (
            "SPACE = next  |  A = previous  |  D = next after comma  |  "
            "S = next line  |  W = previous line  |  ESC = stop"
        )
        tk.Label(
            self.root,
            text=text,
            anchor="w",
            bg=self.APP_BG_COLOR,
            fg=self.MUTED_FG_COLOR,
            font=self.FONT_SUBTITLE,
        ).pack(
            fill=tk.X, padx=12, pady=(0, 8)
        )

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

        self.progress_var = tk.StringVar(value="")
        tk.Label(
            frame, textvariable=self.progress_var,
            font=self.FONT_STATUS,
            justify=tk.CENTER,
            anchor="center",
            bg=self.APP_BG_COLOR,
            fg=self.FG_COLOR,
        ).pack(fill=tk.X)

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
            text="Copy DESCRIPTION and USAGE columns from the PARTS LIST of the FastCAD drawing below:",
            anchor="w",
            bg=self.APP_BG_COLOR,
            fg=self.MUTED_FG_COLOR,
            font=self.FONT_SUBTITLE,
        ).pack(fill=tk.X, pady=(0, 6))

        left = tk.Frame(container, bg=self.PANEL_ALT_BG_COLOR)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        tk.Label(
            left,
            text="DESCRIPTION",
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.BTN_TEXT_COLOR,
            font=self.FONT_UI_BOLD,
        ).pack(anchor="w", padx=8, pady=(8, 4))
        self.desc_text = tk.Text(
            left,
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
        self.desc_text.pack(fill=tk.BOTH, expand=True)
        self.desc_text.tag_configure("current_line", background=self.ROW_HIGHLIGHT_COLOR)

        right = tk.Frame(container, bg=self.PANEL_ALT_BG_COLOR)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        tk.Label(
            right,
            text="USAGE",
            bg=self.PANEL_ALT_BG_COLOR,
            fg=self.BTN_TEXT_COLOR,
            font=self.FONT_UI_BOLD,
        ).pack(anchor="w", padx=8, pady=(8, 4))
        self.place_text = tk.Text(
            right,
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
        self.place_text.pack(fill=tk.BOTH, expand=True)
        self.place_text.tag_configure("current_line", background=self.ROW_HIGHLIGHT_COLOR)

        bold_font = tkfont.Font(font=self.place_text.cget("font")).copy()
        bold_font.configure(weight="bold")
        self.place_text.tag_configure("current_component", font=bold_font)

        self._bind_synchronized_scroll()

    def _bind_synchronized_scroll(self) -> None:
        """Keep both text panes scrolled to the same vertical position."""
        self.syncing_scroll = False
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.desc_text.bind(event, self._on_desc_scroll)
            self.place_text.bind(event, self._on_place_scroll)

    def _on_desc_scroll(self, _event: tk.Event) -> None:
        if not self.syncing_scroll:
            self.syncing_scroll = True
            try:
                self.root.after(5, lambda: self.place_text.yview_moveto(self.desc_text.yview()[0]))
            finally:
                self.syncing_scroll = False

    def _on_place_scroll(self, _event: tk.Event) -> None:
        if not self.syncing_scroll:
            self.syncing_scroll = True
            try:
                self.root.after(5, lambda: self.desc_text.yview_moveto(self.place_text.yview()[0]))
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
        self._set_running(True)
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
        self._update_status_labels()
        self.status_var.set(reason)

    def resume_review(self, trigger: str) -> None:
        """Resume review from the preserved position."""
        if not self.paused or not self.sequence or self.current_index < 0:
            return
        self.paused = False
        self._set_running(True)
        self.status_var.set(f"Resumed ({trigger}). Moving to next component.")
        self._move_next()

    def _set_running(self, running: bool) -> None:
        """Update running state, hotkeys, and buttons atomically."""
        self.running = running
        self.left_alt_ready = True
        self.fastcad.clear_pending()

        # Lock editable inputs while actively reviewing so parsed content and
        # window targeting cannot change mid-run.
        text_state = tk.DISABLED if running else tk.NORMAL
        entry_state = "readonly" if running else tk.NORMAL
        self.desc_text.config(state=text_state)
        self.place_text.config(state=text_state)
        self.window_entry.config(state=entry_state)

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
            self.progress_var.set("")
            self.component_var.set("")
            self.description_var.set("")
            self._set_status_highlight(False)
            self._clear_highlights()
            return
        group_idx, comp = self.sequence[self.current_index]
        group = self.groups[group_idx]

        # Position within the current component group (1-based)
        within_pos = sum(1 for i in range(self.current_index + 1) if self.sequence[i][0] == group_idx)
        group_total = sum(1 for g, _ in self.sequence if g == group_idx)

        self.progress_var.set(
            f"Current: {within_pos}/{group_total}     Total: {self.current_index + 1}/{len(self.sequence)}"
        )
        self._set_status_highlight(True)
        self.component_var.set(comp)
        self.description_var.set(_parse_description(group.description))
        self._highlight_line(group.source_line_index, comp)

    def _set_status_highlight(self, enabled: bool) -> None:
        bg = self.STATUS_HIGHLIGHT_COLOR if enabled else self.status_plain_bg
        self.component_label.config(background=bg)
        self.description_label.config(background=bg)

    def _clear_highlights(self) -> None:
        self.desc_text.tag_remove("current_line", "1.0", tk.END)
        self.place_text.tag_remove("current_line", "1.0", tk.END)
        self.place_text.tag_remove("current_component", "1.0", tk.END)

    def _highlight_line(self, source_line_index: int, current_component: str) -> None:
        """Highlight the active row in both panes and bold the specific designator."""
        self._clear_highlights()
        line_no = source_line_index + 1
        start = f"{line_no}.0"
        end = f"{line_no}.end+1c"

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

        self.desc_text.see(start)
        self.place_text.see(start)

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

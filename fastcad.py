"""fastcad.py — Automated interaction with the FastCAD application window.

FastCadController owns all state related to the current selection command
and the deferred ZOUT flow. It is deliberately decoupled from the Tkinter UI
so it can be exercised or replaced independently.
"""

import time
from typing import Optional

import pyautogui
import pygetwindow as gw


DEFAULT_SELECTION_COMMAND = "ztext"
ZOUT_VERIFY_S = 0.2        # seconds to watch for a post-ZOUT error dialog
ZOUT_RETRY_DELAY_S = 0.05  # pause before re-issuing the search after dismissal


class FastCadController:
    """Drive the FastCAD window via keyboard automation.

    Workflow for each component:
    1. ``send_component()`` — focus FastCAD, type selection command + designator.
    2. Poll ``try_complete_zout()`` each event-loop tick until it returns a
       non-None string, indicating ZOUT was sent (or an error occurred).

    State flags ``pending_zout`` / ``pending_component`` / ``pending_saw_dialog``
    are public so the UI can read them for status messages.
    """

    def __init__(
        self,
        app_title: str,
        command_delay: float = 0.0,
    ) -> None:
        self.app_title = app_title
        self.command_delay = command_delay

        # Reduce built-in post-action wait to improve command throughput.
        pyautogui.PAUSE = 0

        self.last_target_title: str = ""

        # Deferred ZOUT state — set by send_component(), cleared by clear_pending()
        self.pending_zout: bool = False
        self.pending_component: str = ""
        self.pending_zoom: float = 2.5
        self.pending_saw_dialog: bool = False
        self.pending_zout_sent: bool = False
        self.pending_zout_sent_at: float = 0.0
        self.pending_wait_for_retry_dialog: bool = False
        self.auto_enter_on_multiple_matches: bool = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_component(self, hint: str, comp: str, zoom_value: float) -> bool:
        """Focus FastCAD and issue the selection command + component command.

        Returns True if the FastCAD window was found and commands were sent.
        On True, ``pending_zout`` is set to True — call ``try_complete_zout``
        each event-loop tick until it reports completion.
        """
        if not self.focus_window(hint):
            return False

        pyautogui.press("esc")
        pyautogui.write(DEFAULT_SELECTION_COMMAND)
        pyautogui.press("enter")
        if self.command_delay > 0:
            time.sleep(self.command_delay)
        pyautogui.write(f"={comp}")
        pyautogui.press("enter")

        self.pending_zout = True
        self.pending_component = comp
        self.pending_zoom = zoom_value
        self.pending_saw_dialog = False
        return True

    def try_complete_zout(self, hint: str) -> Optional[str]:
        """Advance the deferred ZOUT flow one step.

        Call each event-loop tick while ``pending_zout`` is True.

        Returns:
            A status string when there is something worth reporting to the user,
            or ``None`` to leave the current status unchanged.
            Sets ``pending_zout = False`` once ZOUT is successfully sent.
        """
        if not self.pending_zout:
            return None

        # If ZOUT was already sent, watch for FastCAD's error dialog within
        # a short verify window before declaring success.
        if self.pending_zout_sent:
            if self._is_fastcad_warning_dialog_open():
                pyautogui.press("enter")
                time.sleep(ZOUT_RETRY_DELAY_S)
                comp = self.pending_component
                zoom = self.pending_zoom
                self.pending_zout_sent = False
                self.pending_wait_for_retry_dialog = True
                if not self.send_component(hint, comp, zoom):
                    self.clear_pending()
                    return f"FastCAD window not found for {comp} after ZOUT retry."
                return f"ZOUT rejected for {comp} — retrying selection."
            if time.monotonic() - self.pending_zout_sent_at >= ZOUT_VERIFY_S:
                component = self.pending_component
                zoom_text = f"{self.pending_zoom:g}"
                target = self.last_target_title
                self.clear_pending()
                suffix = f" (target: {target})" if target else ""
                return f"Sent ZOUT {zoom_text} for {component}{suffix}."
            return None  # Still inside verify window

        dialog_open = self._is_multiple_matches_dialog_open()

        if self.pending_wait_for_retry_dialog and not dialog_open:
            return None

        if not self.pending_saw_dialog and dialog_open:
            self.pending_wait_for_retry_dialog = False
            self.pending_saw_dialog = True
            # Move selection up once so the user starts from the previous row.
            pyautogui.press("up")
            if self.auto_enter_on_multiple_matches:
                pyautogui.press("enter")
            return (
                f"Multiple matches for {self.pending_component}. "
                "Select the correct item in FastCAD; ZOUT runs after the dialog closes."
            )

        if self.pending_saw_dialog and dialog_open:
            return None  # Still waiting for the user to close the dialog

        # No dialog present (or dialog just closed) — send ZOUT now
        if not self.focus_window(hint):
            return (
                f"Manual selection done for {self.pending_component}, "
                "but FastCAD could not be focused for ZOUT."
            )

        self._send_zout_sequence(self.pending_zoom)
        self.pending_zout_sent = True
        self.pending_zout_sent_at = time.monotonic()
        return None

    def clear_pending(self) -> None:
        """Reset all deferred ZOUT state (call on stop/reset)."""
        self.pending_zout = False
        self.pending_component = ""
        self.pending_saw_dialog = False
        self.pending_zout_sent = False
        self.pending_zout_sent_at = 0.0
        self.pending_wait_for_retry_dialog = False

    def cancel_active_command(self, hint: str) -> bool:
        """Best-effort ESC in FastCAD to clear any active command selection."""
        if not self.focus_window(hint):
            return False
        pyautogui.press("esc")
        return True

    def focus_window(self, hint: str) -> bool:
        """Find and activate the best-matching FastCAD window.

        Scores candidate windows by how well their title matches ``hint``:
          - Exact match:       +100
          - Starts with hint:  +60
          - Contains hint:     +30
          - Contains 'fastcad': +20 bonus

        Excludes the reviewer window itself and obvious Python host windows.
        Returns True if a window was activated.
        """
        hint_lower = hint.lower().strip() if hint else ""
        self.last_target_title = ""

        try:
            windows = [w for w in gw.getAllWindows() if w.title and w.title.strip()]
        except Exception:
            return False

        app_title_lower = self.app_title.lower()
        best_score = -10_000
        target = None

        for win in windows:
            title_lower = win.title.strip().lower()

            if title_lower == app_title_lower or "component reviewer" in title_lower:
                continue
            if "python" in title_lower and "fastcad" not in title_lower:
                continue

            score = 0
            if hint_lower:
                if hint_lower not in title_lower:
                    continue
                if title_lower == hint_lower:
                    score += 100
                elif title_lower.startswith(hint_lower):
                    score += 60
                else:
                    score += 30

            if "fastcad" in title_lower:
                score += 20

            if score > best_score:
                best_score = score
                target = win

        if target is None:
            return False

        try:
            if target.isMinimized:
                target.restore()
            target.activate()
            self.last_target_title = target.title.strip()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _send_zout_sequence(self, zoom_value: float) -> None:
        pyautogui.write("ZOUT")
        pyautogui.press("enter")
        pyautogui.write(f"{zoom_value:g}")
        pyautogui.press("enter")

    def _is_multiple_matches_dialog_open(self) -> bool:
        try:
            windows = [w for w in gw.getAllWindows() if w.title and w.title.strip()]
        except Exception:
            return False
        return any("multiple matches" in w.title.strip().lower() for w in windows)

    def _is_fastcad_warning_dialog_open(self) -> bool:
        """Return True if FastCAD's 'does not understand the command' error dialog is up."""
        try:
            windows = [w for w in gw.getAllWindows() if w.title and w.title.strip()]
        except Exception:
            return False
        return any(w.title.strip().lower() == "warning" for w in windows)

"""
SerialReader — background thread that continuously reads from a serial port
into a live ring buffer, enabling real-time log preview and non-blocking
pattern matching without pexpect.
"""

import re
import threading
from collections import deque
from typing import IO, Optional


class ConnectionDropError(Exception):
    """Raised when the serial port disappears mid-session."""


class SerialReader(threading.Thread):
    """
    Daemon thread that drains a serial.Serial port into two structures:

      _lines  — deque of complete text lines (for tail / get_log)
      _scan   — raw text accumulated since the last clear() call
                (used by wait_for to match prompts and patterns)

    A threading.Condition notifies all waiters whenever new data arrives,
    so wait_for() sleeps instead of busy-polling.

    A _dead event is set if the port raises an exception mid-read, allowing
    wait_for() to surface a ConnectionDropError immediately.
    """

    def __init__(
        self,
        ser,
        log_fh: Optional[IO] = None,
        maxlines: int = 5000,
    ):
        super().__init__(daemon=True, name="SerialReader")
        self._ser = ser
        self._log_fh = log_fh
        self._lines: deque = deque(maxlen=maxlines)
        self._scan = ""       # text since last clear(), searched by wait_for
        self._partial = ""    # incomplete line not yet flushed to _lines
        self._cond = threading.Condition(threading.Lock())
        self._dead = threading.Event()
        self._stop_event = threading.Event()

    # ── Thread loop ───────────────────────────────────────────────────────────

    def run(self):
        while not self._stop_event.is_set():
            try:
                chunk = self._ser.read(256)
            except Exception:
                with self._cond:
                    self._dead.set()
                    self._cond.notify_all()
                break

            if not chunk:
                continue

            text = chunk.decode("iso8859-1", errors="ignore").replace("\x00", "")

            if self._log_fh:
                try:
                    self._log_fh.write(text)
                    self._log_fh.flush()
                except Exception:
                    pass

            with self._cond:
                self._scan += text
                self._partial += text
                # Flush complete lines into the ring buffer
                while "\n" in self._partial:
                    line, self._partial = self._partial.split("\n", 1)
                    self._lines.append(line)
                self._cond.notify_all()

    # ── Control ───────────────────────────────────────────────────────────────

    def stop(self):
        """Signal the thread to exit on its next read iteration."""
        self._stop_event.set()

    def clear(self):
        """Reset the scan buffer. Call before sending a command so that
        wait_for() only sees output that arrives after the command is sent."""
        with self._cond:
            self._scan = ""

    # ── Read API ──────────────────────────────────────────────────────────────

    def get_scan(self) -> str:
        """Return all text accumulated since the last clear()."""
        with self._cond:
            return self._scan

    def wait_for(self, pattern: str, timeout: float) -> tuple[bool, str]:
        """
        Wait up to `timeout` seconds for `pattern` (treated as a regex) to
        appear in incoming data.

        Returns:
            (True,  text up to and including the match)  on success
            (False, all text captured so far)             on timeout

        Raises:
            ConnectionDropError  if the port disappears while waiting
        """
        import time

        deadline = time.monotonic() + timeout
        compiled = re.compile(pattern)

        while True:
            if self._dead.is_set():
                with self._cond:
                    partial = self._scan
                raise ConnectionDropError(
                    f"UART stream closed — board may have rebooted. "
                    f"Partial output: {partial!r}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._cond:
                    return False, self._scan

            with self._cond:
                m = compiled.search(self._scan)
                if m:
                    return True, self._scan[: m.end()]
                self._cond.wait(timeout=min(remaining, 0.1))

    def tail(self, n: int) -> list[str]:
        """Return the last n complete lines from the ring buffer."""
        with self._cond:
            lines = list(self._lines)
        return lines[-n:] if n < len(lines) else lines

    def is_dead(self) -> bool:
        return self._dead.is_set()

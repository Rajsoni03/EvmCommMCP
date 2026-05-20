"""
UartSession — owns one serial.Serial + one SerialReader thread.
Provides open / close / send / read / tail operations used by the MCP tools.
"""

import errno
import os
import re
import serial

from .reader import ConnectionDropError, SerialReader  # noqa: F401 (re-exported)

# ── ANSI strip ────────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ── Exceptions ────────────────────────────────────────────────────────────────

class EvmCommError(Exception):
    pass


class NoSessionError(EvmCommError):
    def __init__(self):
        super().__init__("No open UART session — call open_port first")


class PortBusyError(EvmCommError):
    def __init__(self, port: str):
        super().__init__(f"Port {port} is busy")


class PortPermissionError(EvmCommError):
    def __init__(self, port: str):
        super().__init__(f"Permission denied on {port}")


class PortNotFoundError(EvmCommError):
    def __init__(self, port: str, detail: str = ""):
        msg = f"Port {port} not found"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


# ── UartSession ───────────────────────────────────────────────────────────────

class UartSession:
    def __init__(self):
        self._ser: serial.Serial | None = None
        self._reader: SerialReader | None = None
        self._prompt: str = "=>"
        self._port: str | None = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(
        self,
        port: str,
        baud_rate: int = 115200,
        prompt: str = "=>",
        timeout: int = 10,
    ) -> bool:
        """
        Open the serial port and start the reader thread.
        Returns True if the prompt was seen within `timeout` seconds.
        """
        if self.is_open:
            raise EvmCommError("Already connected — call close_port first")

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                timeout=0.05,
            )
        except serial.SerialException as e:
            err_str = str(e).lower()
            err_no = getattr(e, "errno", None)
            if "permission" in err_str or err_no == errno.EACCES:
                raise PortPermissionError(port) from e
            if "busy" in err_str or err_no == errno.EBUSY:
                raise PortBusyError(port) from e
            raise PortNotFoundError(port, str(e)) from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise PortPermissionError(port) from e
            if e.errno == errno.EBUSY:
                raise PortBusyError(port) from e
            raise PortNotFoundError(port, str(e)) from e

        log_fh = None
        log_path = os.environ.get("EVM_LOG_FILE")
        if log_path:
            try:
                log_fh = open(log_path, "a")
            except OSError:
                pass

        maxlines = int(os.environ.get("EVM_LOG_BUFFER", "5000"))
        self._reader = SerialReader(self._ser, log_fh=log_fh, maxlines=maxlines)
        self._reader.start()
        self._prompt = prompt
        self._port = port

        # Probe: send an empty newline and wait for the prompt
        self._reader.clear()
        self._ser.write(b"\n")
        matched, _ = self._reader.wait_for(prompt, timeout)
        return matched

    def close(self) -> str | None:
        """Stop the reader thread and close the serial port."""
        port = self._port
        if self._reader:
            self._reader.stop()
            self._reader.join(timeout=2)
            self._reader = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._port = None
        return port

    def send(self, command: str, timeout: int = 30) -> tuple[bool, str]:
        """
        Write `command` to the board and wait for the session prompt.
        Returns (prompt_seen, response_text).
        """
        if not self.is_open:
            raise NoSessionError()
        self._reader.clear()
        self._ser.write((command + "\n").encode("utf-8", errors="replace"))
        matched, captured = self._reader.wait_for(self._prompt, timeout)
        return matched, _strip_ansi(captured)

    def read(self, duration: float) -> str:
        """
        Listen for `duration` seconds without sending anything.
        Returns all text received during that window.
        """
        if not self.is_open:
            raise NoSessionError()
        import time
        self._reader.clear()
        time.sleep(duration)
        return _strip_ansi(self._reader.get_scan())

    def tail(self, n: int) -> list[str]:
        """Return the last n lines from the live ring buffer."""
        if not self._reader:
            return []
        return [_strip_ansi(line) for line in self._reader.tail(n)]

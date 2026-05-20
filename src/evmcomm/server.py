"""
EvmCommMCP — FastMCP stdio server.
Exposes a simple serial terminal to a TI EVM board as MCP tools.
"""

import json
import os

import serial.tools.list_ports
from mcp.server.fastmcp import FastMCP

from .reader import ConnectionDropError
from .session import (
    EvmCommError,
    NoSessionError,
    PortBusyError,
    PortPermissionError,
    UartSession,
)

mcp = FastMCP("evmcomm")
_session = UartSession()


# ── Response helpers ──────────────────────────────────────────────────────────

def _ok(data: dict) -> str:
    return json.dumps(data)


def _err(message: str, suggestion: str = "", **extra) -> str:
    result: dict = {"error": message}
    if suggestion:
        result["suggestion"] = suggestion
    result.update(extra)
    return json.dumps(result)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_ports() -> str:
    """List all available serial ports on this machine."""
    try:
        ports = [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in serial.tools.list_ports.comports()
        ]
        return _ok({"ports": ports})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def open_port(
    port: str = "",
    baud_rate: int = 0,
    prompt: str = "",
    timeout: int = 10,
) -> str:
    """Open a serial connection to the EVM board.

    Args:
        port: Serial device, e.g. /dev/ttyACM0 or /dev/ttyUSB0.
              Falls back to EVM_PORT env var if not given.
        baud_rate: Baud rate. Defaults to 115200 (or EVM_BAUD env var).
        prompt: Prompt string to wait for after connecting.
                Use '=>' for U-Boot, '# ' for Linux root shell.
                Defaults to '=>' (or EVM_PROMPT env var).
        timeout: Seconds to wait for the prompt (default 10).
    """
    port = port or os.environ.get("EVM_PORT", "")
    baud_rate = baud_rate or int(os.environ.get("EVM_BAUD", "115200"))
    prompt = prompt or os.environ.get("EVM_PROMPT", "=>")

    if not port:
        return _err(
            "No port specified",
            "Pass port= argument or set EVM_PORT environment variable",
        )

    try:
        prompt_found = _session.open(port, baud_rate, prompt, timeout)
        return _ok({
            "status": "connected",
            "port": port,
            "baud_rate": baud_rate,
            "prompt": prompt,
            "prompt_found": prompt_found,
        })
    except PortBusyError as e:
        return _err(
            str(e),
            "Close any terminal emulators (minicom, screen, gtkterm) using this port",
        )
    except PortPermissionError as e:
        return _err(
            str(e),
            "Run: sudo usermod -aG dialout $USER  then log out and back in",
        )
    except EvmCommError as e:
        return _err(str(e))
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def close_port() -> str:
    """Close the active serial connection."""
    port = _session.close()
    return _ok({"status": "disconnected", "port": port or "none"})


@mcp.tool()
def send_command(command: str, timeout: int = 30) -> str:
    """Send a command to the EVM board and return the response.

    Args:
        command: Command string to send (newline appended automatically).
        timeout: Seconds to wait for the prompt in the response (default 30).
    """
    import time

    try:
        t0 = time.monotonic()
        matched, response = _session.send(command, timeout)
        elapsed = int((time.monotonic() - t0) * 1000)

        if not matched:
            return _err(
                f"Timeout: prompt not seen after {timeout}s",
                "Try a longer timeout or check the board is at a prompt",
                partial_output=response,
                elapsed_ms=elapsed,
            )

        return _ok({
            "command": command,
            "response": response,
            "elapsed_ms": elapsed,
        })
    except NoSessionError as e:
        return _err(str(e), "Call open_port first")
    except ConnectionDropError as e:
        return _err(str(e), "Call open_port again to reconnect")
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def read_output(duration_seconds: float = 5.0) -> str:
    """Read raw serial output for N seconds without sending a command.

    Useful for capturing boot logs or output triggered by a hardware reset.

    Args:
        duration_seconds: How long to listen (default 5.0 seconds).
    """
    try:
        output = _session.read(duration_seconds)
        return _ok({"output": output, "lines": len(output.splitlines())})
    except NoSessionError as e:
        return _err(str(e), "Call open_port first")
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def get_log(lines: int = 50) -> str:
    """Return the last N lines from the live serial buffer.

    The buffer is updated continuously by a background thread, so this is
    always instant and reflects the current board output.

    Args:
        lines: Number of recent lines to return (default 50).
    """
    try:
        log_lines = _session.tail(lines)
        return _ok({"lines": log_lines, "count": len(log_lines)})
    except Exception as e:
        return _err(str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

# EvmCommMCP

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives
Claude Code a serial terminal to a Texas Instruments EVM board. Open a UART connection,
send commands, and read output — directly from a Claude Code session.

Tested on **J784S4-EVM running QNX 8.0.0**.

---

## How it works

```
Claude Code  ──stdio──  EvmCommMCP server  ──serial──  EVM board
```

The server runs as a local stdio process. A background thread continuously reads the
serial port into a live ring buffer, so Claude can query board output at any time —
not just in response to a command.

---

## Requirements

- Python 3.10+
- `pyserial` and `mcp[cli]` (installed automatically)
- User must be in the `dialout` group to access serial ports:

  ```bash
  sudo usermod -aG dialout $USER
  # log out and back in for the change to take effect
  ```

---

## Installation

```bash
git clone https://github.com/Rajsoni03/EvmCommMCP.git
cd EvmCommMCP
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

---

## Claude Code Integration

There are two ways to register EvmCommMCP: **globally** (available in every
Claude Code session on your machine) or **per-project** (only active when Claude
Code is opened inside a specific directory).

### Global installation (recommended)

Register it once with the Claude Code CLI and it works everywhere:

```bash
claude mcp add evmcomm \
  --scope user \
  -e EVM_PORT=/dev/ttyUSB0 \
  -- /path/to/EvmCommMCP/venv/bin/python -m evmcomm
```

Replace `/path/to/EvmCommMCP` with the absolute path where you cloned the repo
(e.g. `/home/raj/adas/EvmCommMCP`).

This writes the entry to `~/.claude.json` under `mcpServers`, so it is loaded
for every project without any `.mcp.json` file needed.

**Optional env vars** — pass as many `-e KEY=VALUE` flags as needed:

```bash
claude mcp add evmcomm \
  --scope user \
  -e EVM_PORT=/dev/ttyUSB0 \
  -e EVM_BAUD=115200 \
  -e EVM_PROMPT="J784S4-EVM@QNX" \
  -e EVM_LOG_FILE=/tmp/evm.log \
  -- /path/to/EvmCommMCP/venv/bin/python -m evmcomm
```

To verify the entry was added:

```bash
claude mcp list
```

To remove it later:

```bash
claude mcp remove evmcomm --scope user
```

---

### Per-project installation

Useful when different projects need different boards or port settings. Copy the
example config into the project root and edit it:

```bash
cp .mcp.json.example .mcp.json
```

`.mcp.json`:

```json
{
  "mcpServers": {
    "evmcomm": {
      "type": "stdio",
      "command": "/path/to/EvmCommMCP/venv/bin/python",
      "args": ["-m", "evmcomm"],
      "cwd": "/path/to/EvmCommMCP",
      "env": {
        "EVM_PORT": "/dev/ttyUSB0"
      }
    }
  }
}
```

`.mcp.json` is git-ignored by default. Commit `.mcp.json.example` instead so
teammates can copy and adapt it for their own setup.

---

Restart Claude Code after any config change. The tools become available immediately.

---

## Tools

### `list_ports`

Lists all serial ports detected on the host machine.

```
Input:  none
Output: {"ports": [{"device": "/dev/ttyUSB0", "description": "...", "hwid": "..."}]}
```

### `open_port`

Opens a serial connection to the board and waits for a shell prompt.

```
Input:
  port        Serial device path          (default: EVM_PORT env var)
  baud_rate   Baud rate                   (default: 115200)
  prompt      Prompt string or regex      (default: '=>')
  timeout     Seconds to wait for prompt  (default: 10)

Output: {"status": "connected", "port": "...", "baud_rate": 115200, "prompt_found": true}
```

See [Prompt reference](#prompt-reference) for common prompt strings.

### `close_port`

Closes the active serial connection.

```
Input:  none
Output: {"status": "disconnected", "port": "..."}
```

### `send_command`

Sends a command to the board and returns the response.

```
Input:
  command    Command string (newline appended automatically)
  timeout    Seconds to wait for the prompt       (default: 30)
  wait_for   Regex/literal to wait for instead of
             the session prompt                   (default: session prompt)

Output: {"command": "...", "response": "...", "elapsed_ms": 312}
```

On timeout, returns `{"error": "...", "partial_output": "..."}` with whatever
the board printed before the deadline.

**`wait_for` — interactive demo tip:**  
Interactive apps (Vision Apps demos, RTOS menus) never return to the shell
prompt while running. Use `wait_for` to match the app's own prompt instead,
which lets you use short timeouts and get responses immediately:

```
# wrong — waits the full timeout every time
send_command("p", timeout=30)

# right — returns as soon as the menu reappears (~1-2s)
send_command("p", timeout=10, wait_for="Enter Choice:")
send_command("x", timeout=15)   # exit → shell prompt, no wait_for needed
```

### `read_output`

Listens on the serial port for N seconds without sending a command. Useful for
capturing boot logs or output triggered by a hardware reset.

```
Input:
  duration_seconds  How long to listen  (default: 5.0)

Output: {"output": "...", "lines": 42}
```

### `get_log`

Returns the last N lines from the live ring buffer. Always instant — the buffer is
updated continuously in the background regardless of pending tool calls.

```
Input:
  lines  Number of lines to return  (default: 50)

Output: {"lines": ["...", "..."], "count": 50}
```

---

## Prompt reference

| Shell / Environment | `prompt` value |
|---------------------|----------------|
| U-Boot              | `=>`           |
| Linux root shell    | `r"#\s"`       |
| QNX (TI default)    | `J784S4-EVM@QNX:/# ` or `r"#\s"` |
| Custom RTOS         | whatever the firmware prints |

The `prompt` field is passed directly to Python's `re.search()`, so both literal
strings and regex patterns are accepted.

---

## Environment variables

| Variable       | Purpose                                  | Default   |
|----------------|------------------------------------------|-----------|
| `EVM_PORT`     | Default serial port for `open_port`      | —         |
| `EVM_BAUD`     | Default baud rate                        | `115200`  |
| `EVM_PROMPT`   | Default prompt string                    | `=>`      |
| `EVM_LOG_FILE` | Mirror all serial output to a file       | —         |
| `EVM_LOG_BUFFER` | Max lines kept in the ring buffer      | `5000`    |

Arguments passed directly to a tool always override environment variables.

---

## Example session

Once the server is registered and Claude Code is running, you can talk to the board
naturally:

> *"List the serial ports available on this machine."*

> *"Open /dev/ttyUSB0 with prompt '# ' and baud rate 115200."*

> *"Run uname -a on the board."*

> *"Show me the last 30 lines from the board log."*

> *"Capture 10 seconds of output — I'm about to reset the board."*

---

## Troubleshooting

**Permission denied on the serial port**

```bash
sudo usermod -aG dialout $USER
# log out and back in
```

**Port busy**

Close any other terminal emulators (minicom, screen, gtkterm, CCS) connected to
the same port before opening it here.

**Prompt not found after connecting**

The board may not be at a shell prompt. Try:
- Increasing `timeout` in `open_port`
- Pressing Enter on the board's physical console first
- Checking the correct `prompt` string for your firmware

**Garbled output / wrong baud rate**

Verify the baud rate matches your board's UART configuration. Common alternatives
to 115200 are 57600 and 230400.

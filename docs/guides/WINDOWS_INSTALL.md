# Windows Installation & Configuration Guide

Step-by-step setup of **TradingViewMCPServer** on a Windows PC with Claude Desktop.

Everything below was verified against the code in this repository (`tradingview_mcp/server.py`,
`pyproject.toml`, `tradingview_mcp/api/alpha_vantage.py`).

---

## ⚠️ Read this first: pin the MCP SDK to 1.x

`pyproject.toml` declares `mcp[cli]>=1.12.0` with no upper bound. The MCP Python SDK has
since released **2.0.0**, which removed `mcp.server.fastmcp` — the exact module
`tradingview_mcp/server.py` imports on line 43. A plain `pip install -e .` today therefore
installs a broken combination and the server dies at startup with:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

The install steps below pin `mcp[cli]<2` to avoid this. (`uv.lock` in this repo pins
mcp 1.15.0, which is why the lockfile-based path works and the plain pip path does not.)

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | Install from [python.org](https://www.python.org/downloads/windows/). Tick **"Add python.exe to PATH"** during setup. Avoid the Microsoft Store build — its sandboxed paths confuse Claude Desktop. |
| **Git for Windows** *(optional)* | [git-scm.com](https://git-scm.com/download/win). If you skip it, download the repo ZIP from GitHub and extract it. |
| **Claude Desktop** | [claude.ai/download](https://claude.ai/download) |
| **Alpha Vantage API key** | Free key: [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key) |

Verify Python in PowerShell:

```powershell
python --version
```

You should see `Python 3.10.x` or newer. If you see 3.9 or a Store stub, fix that before continuing.

---

## 2. Choose an install location

Pick a path **without spaces** and **outside** `C:\Program Files` — the server creates a
`logs\` folder next to the package at startup (`server.py` lines 25-26) and will fail if the
directory isn't writable by your user account.

Recommended: `C:\Dev\TradingViewMCPServer`

```powershell
mkdir C:\Dev
cd C:\Dev
```

---

## 3. Get the code

```powershell
git clone https://github.com/lev-corrupted/TradingViewMCPServer.git
cd C:\Dev\TradingViewMCPServer
```

No Git? Download the ZIP from GitHub, extract to `C:\Dev\`, and rename the folder to
`TradingViewMCPServer`.

---

## 4. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script (`running scripts is disabled on this system`),
either allow scripts for your user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

…or use the Command Prompt equivalent instead:

```cmd
.\.venv\Scripts\activate.bat
```

Your prompt should now start with `(.venv)`.

---

## 5. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -e .
pip install "mcp[cli]>=1.12.0,<2"
```

The second `pip install` is the important part — it downgrades the SDK from the broken 2.x
back to a working 1.x release. Confirm:

```powershell
pip show mcp | Select-String "^Version"
python -c "from mcp.server.fastmcp import FastMCP; print('MCP SDK OK')"
```

Expect a `1.x` version and `MCP SDK OK`.

---

## 6. Create the `.env` file with your API key

The server reads `ALPHA_VANTAGE_API_KEY` from a `.env` file in the repository root
(`load_dotenv(PROJECT_ROOT / ".env")`, `server.py` line 46).

**Do not use `echo "..." > .env` in Windows PowerShell 5.1** — its `>` redirection writes
UTF-16 LE, which `python-dotenv` cannot parse, and your key will silently not load. Use:

```powershell
Set-Content -Path .env -Value "ALPHA_VANTAGE_API_KEY=YOUR_KEY_HERE" -Encoding ascii
```

Or copy the template and edit it in Notepad (save as ANSI or UTF-8):

```powershell
copy .env.example .env
notepad .env
```

The file must contain exactly one line:

```
ALPHA_VANTAGE_API_KEY=YOUR_KEY_HERE
```

`.env` is listed in `.gitignore`, so your key will not be committed.

---

## 7. Verify the server runs

```powershell
python tradingview_mcp\server.py stdio
```

Expected output on stderr:

```
... - tradingview_mcp.server - INFO - TradingView MCP Server initialized
... - tradingview_mcp.server - INFO - Starting server with transport: stdio
```

The process then waits silently for JSON-RPC on stdin — that is correct behavior. Press
`Ctrl+C` to stop.

If you see `ALPHA_VANTAGE_API_KEY not set`, step 6 didn't take (usually the UTF-16 encoding
problem).

Optionally run the test suite:

```powershell
pip install -r requirements-dev.txt
pytest
```

All 44 tests should pass.

---

## 8. Configure Claude Desktop

Open the config file (create it if missing):

```powershell
notepad $env:APPDATA\Claude\claude_desktop_config.json
```

The full path is typically
`C:\Users\<YourName>\AppData\Roaming\Claude\claude_desktop_config.json`.

Paste this, replacing the path with your actual install location. **Backslashes must be
doubled in JSON**:

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "C:\\Dev\\TradingViewMCPServer\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Dev\\TradingViewMCPServer\\tradingview_mcp\\server.py",
        "stdio"
      ]
    }
  }
}
```

Key points:

- `command` **must** be the Python inside `.venv\Scripts\`, not a bare `python` — Claude
  Desktop does not activate virtual environments.
- Use `python.exe`, not `pythonw.exe`.
- Forward slashes (`C:/Dev/...`) also work if you prefer them to escaped backslashes.

### Alternative: use the installed console script

`pip install -e .` also creates `tradingview-mcp.exe`, which is slightly tidier:

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "C:\\Dev\\TradingViewMCPServer\\.venv\\Scripts\\tradingview-mcp.exe",
      "args": ["stdio"]
    }
  }
}
```

### Alternative: pass the API key via config instead of `.env`

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "C:\\Dev\\TradingViewMCPServer\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Dev\\TradingViewMCPServer\\tradingview_mcp\\server.py", "stdio"],
      "env": {
        "ALPHA_VANTAGE_API_KEY": "YOUR_KEY_HERE"
      }
    }
  }
}
```

Note this stores the key in plaintext in a file that is not gitignored by anything — the
`.env` approach is safer.

---

## 9. Restart Claude Desktop

Fully quit Claude Desktop — closing the window is not enough. Right-click the system tray
icon and choose **Quit**, or:

```powershell
taskkill /IM Claude.exe /F
```

Then reopen it. The MCP tools icon should appear in the chat input area.

---

## 10. Confirm it works

In Claude Desktop, ask:

```
Check server health
```

You should get back `"status": "healthy"` with `"api_key_configured": true`, cache
statistics, and the total API call count. If it says `"degraded"`, the API key isn't
reaching the server.

Then try real queries:

```
What's the current price of AAPL?
Show me Bollinger Bands for TSLA on 1h timeframe
Validate this Pine Script code: [paste code]
```

**33 tools** are registered: market data, 25+ indicators, and 8 Pine Script tools.

---

## Troubleshooting

**Server shows as failed / doesn't appear in Claude Desktop**

Check the Claude Desktop log:

```powershell
Get-Content $env:APPDATA\Claude\logs\mcp-server-tradingview.log -Tail 50
```

And the server's own log:

```powershell
Get-Content C:\Dev\TradingViewMCPServer\logs\tradingview_mcp.log -Tail 50
```

**`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`**

MCP SDK 2.x is installed. Run `pip install "mcp[cli]>=1.12.0,<2"` inside the venv.

**`ModuleNotFoundError: No module named 'tradingview_mcp'`**

`command` points at the wrong Python. It must be `<repo>\.venv\Scripts\python.exe`.

**`"api_key_configured": false`**

`.env` is missing, in the wrong directory, or saved as UTF-16. Recreate it with
`Set-Content ... -Encoding ascii` (step 6). Test directly:

```powershell
.\.venv\Scripts\python.exe -c "from dotenv import load_dotenv; import os; load_dotenv('.env'); print(repr(os.getenv('ALPHA_VANTAGE_API_KEY')))"
```

**JSON parse errors in the config**

No trailing commas, no comments, all backslashes doubled. Validate with:

```powershell
Get-Content $env:APPDATA\Claude\claude_desktop_config.json | ConvertFrom-Json
```

**Rate limit errors**

Alpha Vantage's free tier allows 5 calls/minute and 25 calls/day
(`config.py`, `RATE_LIMIT_CALLS_PER_MINUTE` / `RATE_LIMIT_CALLS_PER_DAY`). Wait a minute
between requests or upgrade to a premium key. Responses are cached for 5 minutes (quotes)
and 15 minutes (historical data), which cuts repeat calls substantially.

**`Activate.ps1 cannot be loaded`**

Run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, or use
`activate.bat` from cmd.exe.

---

## Optional: Docker on Windows

`docker-compose.yml` exists, but note two things before using it:

1. The `Dockerfile` uses `FROM python:3.9-slim`, while `pyproject.toml` requires Python
   3.10+. Change the base image to `python:3.11-slim` first.
2. `docker-compose.yml` mounts `./TradingViewPineStrats`, a directory that isn't in this
   repository — create it or remove that volume line.

For Claude Desktop integration, the native install above is the simpler path: MCP stdio
transport through a container adds process-piping complexity for no benefit here.

---

## Updating later

```powershell
cd C:\Dev\TradingViewMCPServer
git pull
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install "mcp[cli]>=1.12.0,<2"
```

Restart Claude Desktop afterward.

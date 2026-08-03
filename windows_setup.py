#!/usr/bin/env python3
"""
Automated setup, diagnosis, and repair for TradingViewMCPServer + Claude Desktop.

Run from the repository root:

    python windows_setup.py

Checks and repairs, in order:
  1. Python version
  2. Virtual environment
  3. Dependencies (including the MCP SDK 2.x incompatibility)
  4. .env / API key
  5. Claude Desktop registration (merged, never overwritten)
  6. Live server health check over MCP stdio

Every problem it can fix, it fixes. Anything it cannot, it reports with the
exact command to run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import queue
from pathlib import Path

REPO = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
VENV = REPO / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
SERVER = REPO / "tradingview_mcp" / "server.py"
SERVER_KEY = "tradingview"

_fixed: list[str] = []
_failed: list[tuple[str, str]] = []


def ok(msg: str) -> None:
    print(f"  [OK]    {msg}")


def fix(msg: str) -> None:
    print(f"  [FIXED] {msg}")
    _fixed.append(msg)


def fail(msg: str, command: str = "") -> None:
    print(f"  [FAIL]  {msg}")
    if command:
        print(f"          run: {command}")
    _failed.append((msg, command))


TOTAL_STEPS = 8


def step(n: int, title: str) -> None:
    print(f"\n[{n}/{TOTAL_STEPS}] {title}")


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, **kw)


def claude_config_path() -> Path:
    """Location of claude_desktop_config.json for the current platform."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    # Linux / test environments
    return Path(os.environ.get("APPDATA", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def write_text_no_bom(path: Path, text: str) -> None:
    """Write UTF-8 without a BOM. A BOM breaks Claude Desktop's JSON parser."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------- steps


def check_python() -> bool:
    step(1, "Python version")
    if sys.version_info < (3, 10):
        fail(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is too old; 3.10+ required",
            "install Python 3.10+ from https://www.python.org/downloads/windows/",
        )
        return False
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return True


def check_venv() -> bool:
    step(2, "Virtual environment")
    if VENV_PY.exists():
        ok(f"venv present at {VENV}")
        return True
    print("        creating .venv ...")
    r = run([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0 or not VENV_PY.exists():
        fail("could not create virtual environment", f"{sys.executable} -m venv .venv")
        print(r.stderr.strip()[:500])
        return False
    fix(f"created virtual environment at {VENV}")
    return True


def check_dependencies() -> bool:
    step(3, "Dependencies")
    probe = [str(VENV_PY), "-c", "from mcp.server.fastmcp import FastMCP; print('ok')"]

    if run(probe).returncode == 0:
        ver = run([str(VENV_PY), "-c", "import importlib.metadata as m; print(m.version('mcp'))"])
        ok(f"mcp {ver.stdout.strip()} - mcp.server.fastmcp imports cleanly")
        return True

    print("        installing dependencies ...")
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    r = run([str(VENV_PY), "-m", "pip", "install", "-e", str(REPO), "-q"])
    if r.returncode != 0:
        fail("pip install -e . failed", ".venv\\Scripts\\python.exe -m pip install -e .")
        print(r.stderr.strip()[:800])
        return False

    if run(probe).returncode == 0:
        fix("installed dependencies")
        return True

    # Landed on MCP SDK 2.x, which removed mcp.server.fastmcp.
    print("        MCP SDK 2.x detected (incompatible) - pinning to 1.x ...")
    r = run([str(VENV_PY), "-m", "pip", "install", "-q", "mcp[cli]>=1.12.0,<2"])
    if r.returncode == 0 and run(probe).returncode == 0:
        ver = run([str(VENV_PY), "-c", "import importlib.metadata as m; print(m.version('mcp'))"])
        fix(f"downgraded MCP SDK to {ver.stdout.strip()} (2.x removed mcp.server.fastmcp)")
        return True

    fail(
        "mcp.server.fastmcp still unimportable",
        '.venv\\Scripts\\python.exe -m pip install "mcp[cli]>=1.12.0,<2"',
    )
    return False


def check_env(api_key: str | None) -> bool:
    step(4, "API key (.env)")
    env_file = REPO / ".env"

    current = None
    if env_file.exists():
        # Tolerate the UTF-16 file PowerShell's `>` redirection produces.
        raw = env_file.read_bytes()
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("ALPHA_VANTAGE_API_KEY="):
                current = line.split("=", 1)[1].strip().strip('"').strip("'")
        if current and raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            write_text_no_bom(env_file, f"ALPHA_VANTAGE_API_KEY={current}\n")
            fix(".env was UTF-16 (unreadable by python-dotenv); rewrote as UTF-8")
            return True

    if current and current != "your_api_key_here":
        ok(f".env has a key ({current[:4]}{'*' * max(0, len(current) - 4)})")
        return True

    key = api_key
    if not key:
        if not sys.stdin.isatty():
            fail(
                ".env missing or placeholder",
                "python windows_setup.py --api-key YOUR_KEY",
            )
            return False
        print("        Get a free key at https://www.alphavantage.co/support/#api-key")
        key = input("        Alpha Vantage API key: ").strip()

    if not key:
        fail("no API key provided", "python windows_setup.py --api-key YOUR_KEY")
        return False

    write_text_no_bom(env_file, f"ALPHA_VANTAGE_API_KEY={key}\n")
    fix(f"wrote {env_file}")
    return True


def check_registration() -> bool:
    step(5, "Claude Desktop registration")
    cfg = claude_config_path()
    desired = {"command": str(VENV_PY), "args": [str(SERVER), "stdio"]}

    data: dict = {}
    had_bom = False
    if cfg.exists():
        raw = cfg.read_bytes()
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            # Unparseable: preserve the original and rebuild from scratch rather
            # than making the user hand-edit JSON.
            broken = cfg.with_suffix(".json.broken")
            shutil.copy2(cfg, broken)
            data = {}
            print(f"        config was not valid JSON ({exc})")
            print(f"        original preserved as {broken.name}; rebuilding it")
    else:
        print(f"        no config yet; creating {cfg}")

    if not isinstance(data.get("mcpServers"), dict):
        data["mcpServers"] = {}
    servers = data["mcpServers"]

    existing = servers.get(SERVER_KEY)
    others = [k for k in servers if k != SERVER_KEY]

    # A BOM alone is reason to rewrite: Claude Desktop's JSON parser rejects it.
    if existing == desired and cfg.exists() and not had_bom:
        ok(f"'{SERVER_KEY}' already registered correctly")
        if others:
            ok(f"preserved other servers: {', '.join(others)}")
        return True

    if cfg.exists():
        shutil.copy2(cfg, cfg.with_suffix(".json.bak"))

    servers[SERVER_KEY] = desired
    write_text_no_bom(cfg, json.dumps(data, indent=2) + "\n")

    if had_bom and existing == desired:
        fix("stripped UTF-8 BOM from config (Claude Desktop cannot parse it)")
    elif existing is None:
        fix(f"registered '{SERVER_KEY}' in {cfg}")
    else:
        fix(f"corrected '{SERVER_KEY}' entry in {cfg} (backup: {cfg.name}.bak)")
    if others:
        ok(f"preserved other servers: {', '.join(others)}")
    return True


def check_health() -> bool:
    """Start the server over MCP stdio and call health_check for real."""
    step(6, "Live server health check")

    proc = subprocess.Popen(
        [str(VENV_PY), str(SERVER), "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(REPO),
    )

    lines: queue.Queue = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(l) for l in proc.stdout], daemon=True
    ).start()

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv(timeout: float = 20.0):
        try:
            return json.loads(lines.get(timeout=timeout))
        except queue.Empty:
            return None

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "windows_setup", "version": "1"},
            },
        })
        if recv() is None:
            err = proc.stderr.read(1500) if proc.poll() is not None else "(server did not respond)"
            fail("server did not complete the MCP handshake")
            print("        --- server stderr ---")
            for line in err.strip().splitlines()[-12:]:
                print(f"        {line}")
            return False
        ok("MCP handshake completed")

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = recv()
        count = len(tools["result"]["tools"]) if tools else 0
        if count:
            ok(f"{count} tools exposed")
        else:
            fail("server exposed no tools")
            return False

        send({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        })
        resp = recv()
        if not resp:
            fail("health_check produced no response")
            return False

        health = json.loads(resp["result"]["content"][0]["text"])
        print(f"        status ............. {health['status']}")
        print(f"        version ............ {health['version']}")
        print(f"        api_key_configured . {health['api_key_configured']}")
        print(f"        cache .............. {health['cache']['size']}/{health['cache']['max_size']}")

        if health["status"] != "healthy":
            fail(
                "server reports 'degraded' - the API key is not reaching it",
                "python windows_setup.py --api-key YOUR_KEY",
            )
            return False
        ok("server reports healthy")
        return True
    finally:
        proc.kill()


def find_claude_desktop() -> Path | None:
    """Locate the Claude Desktop executable on Windows."""
    if not IS_WINDOWS:
        return None
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "AnthropicClaude",
        Path(os.environ.get("PROGRAMFILES", "")) / "Claude",
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        direct = root / "Claude.exe"
        if direct.exists():
            candidates.append(direct)
        # Squirrel-style installs keep versions in app-<version>\ folders.
        candidates.extend(sorted(root.glob("app-*/Claude.exe")))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def restart_claude(do_restart: bool) -> bool:
    step(7, "Restart Claude Desktop")

    if not IS_WINDOWS:
        ok("not Windows - skipping (restart Claude Desktop yourself)")
        return True

    if not do_restart:
        print("        skipped (pass --restart to do this automatically)")
        print("        Claude Desktop must restart before it picks up the config.")
        print('        cmd:  taskkill /F /IM claude.exe  &&  start "" "%LOCALAPPDATA%\\AnthropicClaude\\Claude.exe"')
        return True

    exe = find_claude_desktop()
    subprocess.run(
        ["taskkill", "/F", "/IM", "claude.exe"],
        capture_output=True, text=True,
    )
    print("        stopped Claude Desktop")

    if exe is None:
        fail(
            "could not find Claude.exe to relaunch",
            'start "" "%LOCALAPPDATA%\\AnthropicClaude\\Claude.exe"',
        )
        return False

    subprocess.Popen([str(exe)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    fix(f"restarted Claude Desktop ({exe.name})")
    return True


def check_claude_launched_server(wait_seconds: int = 25) -> bool:
    """
    Confirm Claude Desktop actually spawned the server.

    Claude Desktop writes a per-server log; a fresh one after restart is the
    programmatic equivalent of seeing the server under
    Developer -> Local MCP Servers.
    """
    step(8, "Confirm Claude Desktop launched the server")

    log = claude_config_path().parent / "logs" / f"mcp-server-{SERVER_KEY}.log"
    print(f"        watching {log}")

    if not IS_WINDOWS:
        ok("not Windows - skipping (check Developer -> Local MCP Servers)")
        return True

    import time

    started = time.time()
    baseline = log.stat().st_mtime if log.exists() else 0.0
    while time.time() - started < wait_seconds:
        if log.exists() and log.stat().st_mtime > baseline:
            break
        time.sleep(2)
    else:
        fail(
            "Claude Desktop has not started the server yet",
            "open Claude Desktop, then Settings -> Developer -> Local MCP Servers",
        )
        print("        (if Claude Desktop is still starting up, re-run this script)")
        return False

    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    errors = [l for l in tail if "error" in l.lower() or "failed" in l.lower()]
    if errors:
        fail("Claude Desktop reported errors starting the server")
        for line in errors[-6:]:
            print(f"        {line.strip()[:160]}")
        return False

    ok("Claude Desktop started the server")
    print("        it should now be listed under Settings -> Developer -> Local MCP Servers")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", help="Alpha Vantage API key (skips the prompt)")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="stop and relaunch Claude Desktop so it picks up the config",
    )
    args = parser.parse_args()

    print("=" * 62)
    print(" TradingViewMCPServer - setup, diagnosis and repair")
    print("=" * 62)
    print(f" repo   : {REPO}")
    print(f" config : {claude_config_path()}")

    steps = [check_python, check_venv, check_dependencies]
    for fn in steps:
        if not fn():
            break
    else:
        if check_env(args.api_key) and check_registration() and check_health():
            if restart_claude(args.restart) and args.restart:
                check_claude_launched_server()

    print("\n" + "=" * 62)
    if _fixed:
        print(f" Fixed {len(_fixed)} issue(s):")
        for m in _fixed:
            print(f"   - {m}")
    if _failed:
        print(f" {len(_failed)} issue(s) need attention:")
        for m, c in _failed:
            print(f"   - {m}")
            if c:
                print(f"     run: {c}")
        print("=" * 62)
        return 1

    print(" All checks passed.")
    print("")
    print(" In Claude Desktop, confirm under:")
    print("   Settings -> Developer -> Local MCP Servers  ->  'tradingview'")
    print(" then ask it: 'Check server health'")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())

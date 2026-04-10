#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
from plistlib import dumps as plist_dumps
from pathlib import Path
from platform import system


REPO = Path(__file__).resolve().parents[1]
SERVICE_PATH = Path.home() / ".config" / "systemd" / "user" / "claude-oracle-sync.service"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.claude.oracle.sync.plist"
SERVICE_TEXT = f"""[Unit]
Description=Claude Oracle Auto Sync
After=default.target

[Service]
Type=simple
ExecStart={sys.executable} {REPO / 'scripts' / 'watch_sync.py'}
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
"""


def run(cmd: list[str], check: bool = True) -> None:
    subprocess.run(cmd, check=check)


def install_systemd_service() -> None:
    SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_PATH.write_text(SERVICE_TEXT, encoding="utf-8")

    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "claude-oracle-sync.service"])
    run(["systemctl", "--user", "restart", "claude-oracle-sync.service"])

    print(f"Installed user service: {SERVICE_PATH}")
    print("Claude oracle sync is enabled and running.")


def install_launch_agent() -> None:
    LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.claude.oracle.sync",
        "ProgramArguments": [sys.executable, str(REPO / "scripts" / "watch_sync.py")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(REPO),
        "StandardOutPath": str(Path.home() / ".claude" / "debug" / "claude-oracle-sync.out.log"),
        "StandardErrorPath": str(Path.home() / ".claude" / "debug" / "claude-oracle-sync.err.log"),
    }
    (Path.home() / ".claude" / "debug").mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENT_PATH.write_bytes(plist_dumps(plist))

    run(["launchctl", "unload", str(LAUNCH_AGENT_PATH)], check=False)
    run(["launchctl", "load", "-w", str(LAUNCH_AGENT_PATH)])

    print(f"Installed LaunchAgent: {LAUNCH_AGENT_PATH}")
    print("Claude oracle sync is enabled and running.")


def install_rtk() -> None:
    """Ensure the rtk binary is installed. Uses cargo if available, else curl installer."""
    if shutil.which("rtk"):
        print("RTK: already installed.")
        return
    print("RTK: not found, installing...")
    if shutil.which("cargo"):
        run(["cargo", "install", "--git", "https://github.com/rtk-ai/rtk"])
    elif shutil.which("curl"):
        run(["bash", "-c", "curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh"])
    else:
        print("RTK: WARNING — neither cargo nor curl found. Install rtk manually: https://github.com/rtk-ai/rtk#installation", file=sys.stderr)
        return
    if shutil.which("rtk"):
        print("RTK: installed successfully.")
    else:
        print("RTK: WARNING — installation completed but rtk not found in PATH. You may need to restart your shell.", file=sys.stderr)


def main() -> None:
    install_rtk()
    run([sys.executable, str(REPO / "scripts" / "sync.py")])
    run([sys.executable, str(REPO / "scripts" / "verify.py")], check=False)
    if system() == "Darwin":
        install_launch_agent()
    else:
        install_systemd_service()


if __name__ == "__main__":
    main()

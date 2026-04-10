#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "source"
SYNC = REPO / "scripts" / "sync.py"
VERIFY = REPO / "scripts" / "verify.py"
POLL_SECONDS = 2.0
DEBOUNCE_SECONDS = 1.0


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        stat = path.stat()
        h.update(rel.encode("utf-8"))
        h.update(str(stat.st_mtime_ns).encode("utf-8"))
        h.update(str(stat.st_size).encode("utf-8"))
    return h.hexdigest()


def run_sync() -> int:
    sync_proc = subprocess.run([sys.executable, str(SYNC)], check=False)
    if sync_proc.returncode != 0:
        return sync_proc.returncode
    verify_proc = subprocess.run([sys.executable, str(VERIFY)], check=False)
    return verify_proc.returncode


def main() -> int:
    last = fingerprint(ROOT)
    pending_since: float | None = None

    while True:
        current = fingerprint(ROOT)
        if current != last:
            if pending_since is None:
                pending_since = time.monotonic()
            elif time.monotonic() - pending_since >= DEBOUNCE_SECONDS:
                code = run_sync()
                if code == 0:
                    last = fingerprint(ROOT)
                    pending_since = None
                else:
                    time.sleep(5)
                    last = fingerprint(ROOT)
                    pending_since = None
        else:
            pending_since = None

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

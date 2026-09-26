#!/usr/bin/env python3
"""Download Hub: resumable HTTP downloads followed by optional GoldHEN FTP upload."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable
from pathlib import Path

BLOCK = 4 * 1024 * 1024
STATE = Path.home() / ".pulsehost-downloads.json"


def load(path: Path) -> dict:
    try: return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError): return {}


def save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(BLOCK), b""): h.update(b)
    return h.hexdigest()


def download(url: str, out: Path, state: dict, retries: int = 8,
             should_pause: Callable[[], bool] | None = None,
             should_cancel: Callable[[], bool] | None = None,
             on_progress: Callable[[int, int], None] | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    key = url
    entry = state.setdefault(key, {})
    for attempt in range(1, retries + 1):
        current = out.stat().st_size if out.exists() else 0
        headers = {"User-Agent": "PulseHost-DownloadHub/1.0"}
        if current: headers["Range"] = f"bytes={current}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                total = int(r.headers.get("Content-Length") or 0)
                resumed = current and r.status == 206
                if current and not resumed:
                    current = 0
                mode = "ab" if resumed else "wb"
                if not resumed: total = int(r.headers.get("Content-Length") or 0)
                done = current
                with out.open(mode) as f:
                    while True:
                        while should_pause and should_pause():
                            entry["status"] = "paused"; save(STATE, state); time.sleep(.25)
                        if should_cancel and should_cancel():
                            entry["status"] = "cancelled"; save(STATE, state)
                            return out
                        block = r.read(BLOCK)
                        if not block: break
                        f.write(block); done += len(block)
                        entry.update({"file": str(out), "bytes": done, "total": total, "status": "downloading"})
                        save(STATE, state)
                        if on_progress: on_progress(done, total)
                        pct = done / total * 100 if total else 0
                        print(f"\r{pct:6.2f}% {done}/{total or '?'} bytes", end="", flush=True)
                print()
            entry.update({"file": str(out), "bytes": done, "total": total, "status": "complete"})
            save(STATE, state)
            return out
        except (OSError, urllib.error.URLError) as exc:
            entry["status"] = "interrupted"; entry["error"] = str(exc); save(STATE, state)
            print(f"\n[retry {attempt}/{retries}] {exc}")
            time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"download failed: {url}")


def main() -> int:
    p = argparse.ArgumentParser(description="PulseHost Download Hub: resumable URL downloads")
    p.add_argument("url")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sha256")
    p.add_argument("--retries", type=int, default=8)
    p.add_argument("--ftp-host")
    p.add_argument("--remote")
    p.add_argument("--ftp-port", type=int, default=2121)
    args = p.parse_args()
    global STATE
    state = load(STATE)
    path = download(args.url, args.output, state, args.retries)
    if args.sha256:
        actual = sha256(path)
        if actual.lower() != args.sha256.lower(): raise SystemExit(f"sha256 mismatch: {actual}")
        print(f"sha256 OK: {actual}")
    if args.ftp_host:
        if not args.remote: p.error("--remote is required with --ftp-host")
        import importlib.util
        spec = importlib.util.spec_from_file_location("transfer", Path(__file__).with_name("pulse-transfer.py"))
        mod = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod)
        cfg = type("Args", (), {"host": args.ftp_host, "port": args.ftp_port, "user": "anonymous", "password": "", "timeout": 15.0, "retries": args.retries, "state": Path.home()/".pulsehost-transfers.json", "block_size": BLOCK, "hash": False})()
        mod.transfer_one(cfg, path, args.remote, mod.load_state(cfg.state))
    return 0


if __name__ == "__main__": raise SystemExit(main())

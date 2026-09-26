#!/usr/bin/env python3
"""PulseHost resumable FTP transfer helper for GoldHEN.

Uploads files to a PS4 FTP server (normally port 2121), keeping a local state
file so interrupted transfers can continue with REST + STOR when supported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import posixpath
import sys
import time
from ftplib import FTP, all_errors
from pathlib import Path

DEFAULT_BLOCK = 4 * 1024 * 1024
DEFAULT_STATE = Path.home() / ".pulsehost-transfers.json"


def sha256_file(path: Path, block_size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"jobs": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def remote_size(ftp: FTP, remote: str) -> int:
    try:
        value = ftp.size(remote)
        return int(value or 0)
    except all_errors:
        return 0


def supports_rest(ftp: FTP) -> bool:
    try:
        response = ftp.sendcmd("REST 0")
        return response.startswith("3") or "restart" in response.lower()
    except all_errors:
        return False


def connect(host: str, port: int, user: str, password: str, timeout: float) -> FTP:
    ftp = FTP()
    ftp.connect(host, port, timeout=timeout)
    ftp.login(user, password)
    ftp.voidcmd("TYPE I")
    if ftp.sock is not None:
        ftp.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        ftp.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return ftp


def transfer_one(args, local: Path, remote: str, state: dict) -> None:
    if not local.is_file():
        raise FileNotFoundError(local)
    total = local.stat().st_size
    key = f"{local.resolve()}::{remote}"
    entry = state.setdefault("jobs", {}).setdefault(key, {})
    digest = entry.get("sha256")
    if not digest or entry.get("size") != total:
        if args.hash:
            print(f"[hash] {local.name}")
            digest = sha256_file(local, args.block_size)
        else:
            digest = "deferred"
        entry.update({"size": total, "sha256": digest, "remote": remote, "completed": 0})
        save_state(args.state, state)

    last_error = None
    for attempt in range(1, args.retries + 1):
        ftp = None
        try:
            ftp = connect(args.host, args.port, args.user, args.password, args.timeout)
            size = remote_size(ftp, remote)
            if size > total:
                print(f"[reset] remoto mayor que local ({size} > {total}); reiniciando")
                size = 0
            if size == total:
                entry["completed"] = total
                entry["verified_size"] = total
                save_state(args.state, state)
                print(f"[skip] {remote} ya está completo ({total} bytes)")
                return
            resume = size > 0 and supports_rest(ftp)
            offset = size if resume else 0
            if not resume and size:
                print("[restart] el FTP no anuncia REST; se reinicia desde cero")
            mode = "resume" if offset else "start"
            print(f"[{mode}] {local.name}: {offset}/{total} bytes (intento {attempt})")
            with local.open("rb") as fh:
                if offset:
                    fh.seek(offset)
                sent = offset

                def callback(block: bytes) -> None:
                    nonlocal sent
                    sent += len(block)
                    entry["completed"] = sent
                    if sent == total or sent % (64 * args.block_size) < len(block):
                        save_state(args.state, state)
                    pct = (sent / total * 100) if total else 100
                    print(f"\r{pct:6.2f}% {sent}/{total} bytes", end="", flush=True)

                ftp.storbinary(
                    f"STOR {remote}",
                    fh,
                    blocksize=args.block_size,
                    callback=callback,
                    rest=offset or None,
                )
            print()
            final = remote_size(ftp, remote)
            if final != total:
                raise RuntimeError(f"tamaño remoto {final}, esperado {total}")
            entry.update({"completed": total, "verified_size": total, "status": "complete"})
            save_state(args.state, state)
            print(f"[done] {remote} sha256-local={digest[:16]}...")
            return
        except all_errors + (OSError, RuntimeError) as exc:
            last_error = exc
            print(f"\n[retry] {exc}")
            entry["status"] = "interrupted"
            save_state(args.state, state)
            time.sleep(min(2 ** (attempt - 1), 8))
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except all_errors:
                    ftp.close()
    raise RuntimeError(f"transfer failed after {args.retries} attempts: {last_error}")


def read_jobs(manifest: Path) -> list[tuple[Path, str]]:
    jobs = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"manifest line must be: LOCAL REMOTE: {line}")
        jobs.append((Path(parts[0]).expanduser(), parts[1]))
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable PC -> GoldHEN FTP transfer")
    parser.add_argument("--host", required=True, help="PS4 IP address")
    parser.add_argument("--port", type=int, default=2121)
    parser.add_argument("--user", default="anonymous")
    parser.add_argument("--password", default="")
    parser.add_argument("--file", type=Path, help="local file to upload")
    parser.add_argument("--remote", help="remote path, e.g. /data/pkg/game.pkg")
    parser.add_argument("--manifest", type=Path, help="text file with LOCAL REMOTE pairs")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument(
        "--block-size",
        type=int,
        default=DEFAULT_BLOCK,
        help="FTP read block in bytes (default: 4194304)",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help="hash the local file before transfer; slower startup, stronger precheck",
    )
    args = parser.parse_args()
    if bool(args.file) == bool(args.manifest):
        parser.error("use exactly one of --file/--manifest")
    if args.file and not args.remote:
        parser.error("--remote is required with --file")
    jobs = [(args.file.expanduser(), args.remote)] if args.file else read_jobs(args.manifest.expanduser())
    state = load_state(args.state.expanduser())
    for local, remote in jobs:
        transfer_one(args, local, posixpath.normpath(remote), state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        print("\ninterrupted; state saved for resume", file=sys.stderr)
        raise SystemExit(130)

#!/usr/bin/env python3
"""Local PulseHost PC panel for GoldHEN FTP transfers.

Binds to localhost by default. Use --host 0.0.0.0 only on a trusted LAN.
"""
from __future__ import annotations

import importlib.util
import os
import posixpath
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from ftplib import FTP, all_errors
from pathlib import Path

from flask import Flask, jsonify, request, send_file

ROOT = Path(__file__).resolve().parent
TRANSFER_PATH = ROOT / "pulse-transfer.py"
spec = importlib.util.spec_from_file_location("pulse_transfer", TRANSFER_PATH)
transfer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(transfer)
download_spec = importlib.util.spec_from_file_location("pulse_download", ROOT / "pulse-download.py")
download = importlib.util.module_from_spec(download_spec)
assert download_spec.loader is not None
download_spec.loader.exec_module(download)

app = Flask(__name__)
JOBS: list[dict] = []
JOB_QUEUE: queue.Queue[dict] = queue.Queue()
LOCK = threading.Lock()


def category(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {".pkg": "PKG", ".zip": "ZIP", ".7z": "7Z", ".json": "JSON", ".bin": "PAYLOAD", ".sav": "SAVE"}.get(ext, "OTHER")


def safe_extract_zip(source: Path, target: Path) -> None:
    target = target.resolve(); target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            dest = (target / member.filename).resolve()
            if dest != target and target not in dest.parents:
                raise ValueError("unsafe archive path")
        archive.extractall(target)


def ftp_from(data: dict) -> FTP:
    ftp = transfer.connect(
        data["host"], int(data.get("port", 2121)), data.get("user", "anonymous"),
        data.get("password", ""), float(data.get("timeout", 15)),
    )
    return ftp


def worker() -> None:
    while True:
        job = JOB_QUEUE.get()
        try:
            with LOCK:
                job["status"] = "running"
            args = type("Args", (), {
                "host": job["host"], "port": job["port"], "user": job["user"],
                "password": job["password"], "timeout": 15.0, "retries": 8,
                "state": Path.home() / ".pulsehost-transfers.json",
                "block_size": 4 * 1024 * 1024, "hash": job.get("hash", False),
            })()
            transfer.transfer_one(args, Path(job["local"]), job["remote"], transfer.load_state(args.state))
            with LOCK:
                job["status"] = "complete"
        except Exception as exc:
            with LOCK:
                job["status"] = "error"
                job["error"] = str(exc)
        finally:
            JOB_QUEUE.task_done()


threading.Thread(target=worker, daemon=True, name="pulse-transfer-worker").start()


@app.get("/")
def index():
    return send_file(ROOT / "pulse-panel.html")


@app.get("/api/health")
def health():
    with LOCK:
        return jsonify({"ok": True, "jobs": JOBS[-30:]})


@app.post("/api/connect")
def connect_api():
    data = request.get_json(force=True)
    ftp = None
    try:
        ftp = ftp_from(data)
        return jsonify({"ok": True, "welcome": ftp.getwelcome()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        if ftp:
            try: ftp.quit()
            except all_errors: ftp.close()


@app.get("/api/files")
def files_api():
    data = {"host": request.args["host"], "port": request.args.get("port", 2121),
            "user": request.args.get("user", "anonymous"), "password": request.args.get("password", "")}
    path = posixpath.normpath(request.args.get("path", "/data/pkg"))
    ftp = None
    try:
        ftp = ftp_from(data)
        rows = []
        try:
            for name, facts in ftp.mlsd(path):
                rows.append({"name": name, "type": facts.get("type", ""), "size": int(facts.get("size", 0) or 0)})
        except all_errors:
            for name in ftp.nlst(path):
                rows.append({"name": name, "type": "unknown", "size": 0})
        return jsonify({"ok": True, "path": path, "files": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        if ftp:
            try: ftp.quit()
            except all_errors: ftp.close()


@app.post("/api/queue")
def queue_api():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "missing file"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"ok": False, "error": "empty filename"}), 400
    host = request.form.get("host", "")
    if not host:
        return jsonify({"ok": False, "error": "PS4 host required"}), 400
    remote = request.form.get("remote", "/data/pkg/" + Path(file.filename).name)
    safe_name = Path(file.filename).name
    folder = Path(tempfile.gettempdir()) / "pulsehost-queue"
    folder.mkdir(exist_ok=True)
    local = folder / (str(int(time.time() * 1000)) + "-" + safe_name)
    file.save(local)
    job = {"id": str(time.time_ns()), "name": safe_name, "local": str(local), "remote": remote,
           "host": host, "port": int(request.form.get("port", 2121)),
           "user": request.form.get("user", "anonymous"), "password": request.form.get("password", ""),
           "hash": request.form.get("hash") == "1", "category": category(safe_name), "status": "queued"}
    with LOCK: JOBS.append(job)
    JOB_QUEUE.put(job)
    public = {k: v for k, v in job.items() if k not in {"password", "local"}}
    return jsonify({"ok": True, "job": public})


@app.post("/api/download")
def download_api():
    data = request.get_json(force=True)
    url = str(data.get("url", "")).strip()
    output = str(data.get("output", "")).strip()
    if not url or not output:
        return jsonify({"ok": False, "error": "url and output required"}), 400
    job = {"id": str(time.time_ns()), "name": Path(output).name, "local": output,
           "remote": data.get("remote", ""), "category": category(output), "status": "downloading", "kind": "http"}
    with LOCK: JOBS.append(job)

    def run() -> None:
        try:
            def progress(done, total):
                with LOCK:
                    job.update(bytes=done, total=total, progress=(done / total * 100 if total else 0))
            path = download.download(
                url, Path(output).expanduser(), download.load(download.STATE),
                int(data.get("retries", 8)),
                should_pause=lambda: job.get("status") == "paused",
                should_cancel=lambda: job.get("status") == "cancelled",
                on_progress=progress,
            )
            if job.get("status") == "cancelled":
                return
            if data.get("ftp_host") and data.get("remote"):
                cfg = type("Args", (), {"host": data["ftp_host"], "port": int(data.get("ftp_port", 2121)), "user": "anonymous", "password": "", "timeout": 15.0, "retries": 8, "state": Path.home()/".pulsehost-transfers.json", "block_size": 4*1024*1024, "hash": False})()
                transfer.transfer_one(cfg, path, data["remote"], transfer.load_state(cfg.state))
            with LOCK: job["status"] = "complete"
        except Exception as exc:
            with LOCK: job.update(status="error", error=str(exc))
    threading.Thread(target=run, daemon=True, name="pulse-http-download").start()
    return jsonify({"ok": True, "job": {k: v for k, v in job.items() if k != "local"}})


@app.post("/api/jobs/<job_id>/<action>")
def job_action(job_id: str, action: str):
    if action not in {"pause", "resume", "cancel"}:
        return jsonify({"ok": False, "error": "unknown action"}), 400
    with LOCK:
        job = next((x for x in JOBS if x["id"] == job_id), None)
        if not job:
            return jsonify({"ok": False, "error": "job not found"}), 404
        if action == "pause" and job.get("kind") == "http": job["status"] = "paused"
        elif action == "resume" and job.get("kind") == "http": job["status"] = "running"
        elif action == "cancel": job["status"] = "cancelled"
        else: return jsonify({"ok": False, "error": "only HTTP jobs support pause/resume"}), 400
        return jsonify({"ok": True, "status": job["status"]})


@app.post("/api/local")
def local_action():
    data = request.get_json(force=True)
    path = Path(str(data.get("path", ""))).expanduser().resolve()
    action = data.get("action")
    if not path.exists(): return jsonify({"ok": False, "error": "file not found"}), 404
    try:
        if action == "delete":
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
        elif action == "move":
            dest = Path(str(data.get("destination", ""))).expanduser().resolve()
            dest.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(path), str(dest))
        elif action == "extract":
            dest = Path(str(data.get("destination", path.with_suffix("_extracted")))).expanduser().resolve()
            if path.suffix.lower() == ".zip": safe_extract_zip(path, dest)
            elif path.suffix.lower() == ".7z": subprocess.run(["7z", "x", "-y", f"-o{dest}", str(path)], check=True, timeout=900)
            else: return jsonify({"ok": False, "error": "only zip/7z can be extracted"}), 400
        else: return jsonify({"ok": False, "error": "unknown local action"}), 400
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PulseHost local PC panel")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use 0.0.0.0 for trusted LAN")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)

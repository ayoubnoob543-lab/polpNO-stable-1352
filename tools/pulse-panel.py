#!/usr/bin/env python3
"""Local PulseHost PC panel for GoldHEN FTP transfers.

Binds to localhost by default. Use --host 0.0.0.0 only on a trusted LAN.
"""
from __future__ import annotations

import importlib.util
import os
import posixpath
import queue
import tempfile
import threading
import time
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
           "hash": request.form.get("hash") == "1", "status": "queued"}
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
           "remote": data.get("remote", ""), "status": "downloading", "kind": "http"}
    with LOCK: JOBS.append(job)

    def run() -> None:
        try:
            path = download.download(url, Path(output).expanduser(), download.load(download.STATE), int(data.get("retries", 8)))
            if data.get("ftp_host") and data.get("remote"):
                cfg = type("Args", (), {"host": data["ftp_host"], "port": int(data.get("ftp_port", 2121)), "user": "anonymous", "password": "", "timeout": 15.0, "retries": 8, "state": Path.home()/".pulsehost-transfers.json", "block_size": 4*1024*1024, "hash": False})()
                transfer.transfer_one(cfg, path, data["remote"], transfer.load_state(cfg.state))
            with LOCK: job["status"] = "complete"
        except Exception as exc:
            with LOCK: job.update(status="error", error=str(exc))
    threading.Thread(target=run, daemon=True, name="pulse-http-download").start()
    return jsonify({"ok": True, "job": {k: v for k, v in job.items() if k != "local"}})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PulseHost local PC panel")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use 0.0.0.0 for trusted LAN")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)

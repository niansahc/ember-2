"""
scripts/watchdog.py

Lightweight watchdog process that manages the Ember-2 API lifecycle.

Runs alongside the API process. Polls for signal files and acts on them:
  - ember_restart.signal → kill the API, delete the signal, restart it
  - ember_stop.signal → kill the API, delete the signal, exit watchdog

Signal files are written by the API itself (POST /v1/service/api/restart
and /stop) so the watchdog is the external process that can actually
kill and relaunch uvicorn — something the API cannot do from within
its own request handler.

Cross-platform: uses subprocess and os.kill with platform-appropriate
signal handling. Works on Windows, Linux, and macOS.

Usage (not called directly — launched by launch_ember.bat/sh):
    python scripts/watchdog.py --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNAL_DIR = REPO_ROOT
RESTART_SIGNAL = SIGNAL_DIR / "ember_restart.signal"
STOP_SIGNAL = SIGNAL_DIR / "ember_stop.signal"
POLL_INTERVAL = 1.0  # seconds between signal file checks


def _venv_python() -> str:
    """Return the path to the venv Python executable."""
    if platform.system() == "Windows":
        candidate = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO_ROOT / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def start_api(host: str, port: int) -> subprocess.Popen:
    """Start the uvicorn API process and return the Popen handle."""
    python = _venv_python()
    cmd = [
        python, "-m", "uvicorn",
        "src.api.main:app",
        "--host", host,
        "--port", str(port),
    ]
    print(f"[WATCHDOG] Starting API: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    return proc


def kill_api(proc: subprocess.Popen) -> None:
    """Kill the API process. Cross-platform."""
    if proc.poll() is not None:
        return  # already exited

    print(f"[WATCHDOG] Killing API (PID {proc.pid})...")
    try:
        if platform.system() == "Windows":
            # On Windows, terminate() sends CTRL_BREAK_EVENT if possible,
            # falls back to TerminateProcess. Use kill() for certainty.
            proc.kill()
        else:
            # On Unix, send SIGTERM for graceful shutdown.
            os.kill(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if SIGTERM didn't work within 5s.
                os.kill(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError) as exc:
        print(f"[WATCHDOG] Kill failed (process may have already exited): {exc}")

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("[WATCHDOG] WARNING: API process did not exit after kill.")


def clear_signal(signal_path: Path) -> None:
    """Delete a signal file if it exists."""
    try:
        if signal_path.exists():
            signal_path.unlink()
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Ember-2 API watchdog")
    parser.add_argument("--host", default="127.0.0.1", help="API bind host")
    parser.add_argument("--port", type=int, default=8000, help="API bind port")
    args = parser.parse_args()

    # Clean up any stale signals from a previous run
    clear_signal(RESTART_SIGNAL)
    clear_signal(STOP_SIGNAL)

    print(f"[WATCHDOG] Starting on {platform.system()} — watching for signals in {SIGNAL_DIR}")
    api_proc = start_api(args.host, args.port)

    try:
        while True:
            # Check if API crashed on its own
            if api_proc.poll() is not None:
                exit_code = api_proc.returncode
                print(f"[WATCHDOG] API exited unexpectedly (code {exit_code}). Restarting...")
                time.sleep(2)
                api_proc = start_api(args.host, args.port)

            # Check for restart signal
            if RESTART_SIGNAL.exists():
                print("[WATCHDOG] Restart signal detected.")
                clear_signal(RESTART_SIGNAL)
                kill_api(api_proc)
                time.sleep(1)
                api_proc = start_api(args.host, args.port)

            # Check for stop signal
            if STOP_SIGNAL.exists():
                print("[WATCHDOG] Stop signal detected. Shutting down.")
                clear_signal(STOP_SIGNAL)
                kill_api(api_proc)
                print("[WATCHDOG] Exiting.")
                sys.exit(0)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[WATCHDOG] Interrupted. Shutting down API...")
        kill_api(api_proc)
        print("[WATCHDOG] Exiting.")
        sys.exit(0)


if __name__ == "__main__":
    main()

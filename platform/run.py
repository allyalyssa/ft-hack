"""Run the collections loop on this machine: the dashboard plus one full cycle every two minutes.

python3 platform/run.py                # dashboard on :8080 and a cycle every 120 s; Ctrl-C stops both
python3 platform/run.py --once         # one cycle, then exit (for cron or a systemd timer)

A cycle is: triage, disputes, draft the next few invoices, post new packages if a channel is configured.
"""
import argparse
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def step(args, quiet=False, timeout=900):
    started = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  {args[0]}: timed out after {timeout}s")
        return False
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    for line in lines[:1] if quiet and proc.returncode == 0 else lines:
        print(f"  {line}")
    if proc.returncode:
        print(f"  {args[0]} exited {proc.returncode} after {time.perf_counter() - started:.1f}s")
    return proc.returncode == 0


def cycle(limit):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} cycle")
    if not step(["triage/triage.py"], quiet=True):
        return False
    step(["disputes/disputes.py"])
    drafted = step(["agent/actions.py", "--limit", str(limit)])
    step(["channel/post.py"])
    return drafted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=120, help="seconds between cycle starts")
    ap.add_argument("--limit", type=int, default=5, help="invoices drafted per cycle")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    if args.once:
        return 0 if cycle(args.limit) else 1

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    dashboard = subprocess.Popen([sys.executable, "channel/dashboard.py", "--port", str(args.port)], cwd=ROOT)
    try:
        while True:
            started = time.monotonic()
            cycle(args.limit)
            time.sleep(max(0, args.every - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.terminate()
        dashboard.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

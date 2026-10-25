"""Check this machine can run the demo with no network: tests, the local model, one full cycle and the dashboard.

python3 platform/preflight.py               # everything, including one real draft with the local model
python3 platform/preflight.py --no-model    # skip the model checks

Every Python process it starts runs under platform/offline_guard, which refuses any connection that leaves this
machine. The last check fails if anything tried.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import store  # noqa: E402

GUARD = ROOT / "platform" / "offline_guard"
results = []


def report(status, name, detail=""):
    results.append(status)
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""))


def run(args, env, timeout=900):
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr, time.perf_counter() - started


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.read().decode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true")
    args = ap.parse_args()

    log = Path(tempfile.gettempdir()) / f"offline-guard-{os.getpid()}.log"
    env = dict(os.environ, OFFLINE_GUARD_LOG=str(log), PYTHONIOENCODING="utf-8",
               PYTHONPATH=os.pathsep.join(filter(None, [str(GUARD), os.environ.get("PYTHONPATH")])))

    ok = sys.version_info >= (3, 10)
    report("PASS" if ok else "FAIL", "python version", sys.version.split()[0])

    code, out, _ = run(["-c", "import urllib.request; urllib.request.urlopen('http://example.com', timeout=5)"], env)
    report("PASS" if code and "offline guard" in out else "FAIL", "offline guard blocks outside connections")
    log.unlink(missing_ok=True)

    for suite in ("triage/test_triage.py", "triage/test_revalidate.py", "disputes/test_disputes.py", "disputes/test_cases.py",
                  "agent/test_agent.py"):
        code, out, _ = run([suite], env)
        passed = re.search(r"\d+ passed", out)
        report("PASS" if code == 0 and passed else "FAIL", f"tests {suite}", passed.group(0) if code == 0 and passed else out[-1500:])

    inbox = [p.name for p in (ROOT / "data" / "inbox").iterdir() if not p.name.startswith(".")]
    report("WARN" if inbox else "PASS", "data/inbox", f"holds {', '.join(inbox)}; triage reads it instead of data/exports"
           if inbox else "empty, triage uses data/exports")

    base_url, model = store.model_config()
    host = urlparse(base_url).hostname
    report("PASS" if host in ("127.0.0.1", "localhost", "::1") else "FAIL", "model endpoint is on this machine", base_url)

    if not args.no_model:
        try:
            names = [m["id"] for m in json.loads(get(base_url.rstrip("/") + "/models")[1])["data"]]
            report("PASS" if model in names else "FAIL", f"model {model} is served", f"available: {', '.join(names)}")
        except OSError as e:
            report("FAIL", "model endpoint answers", f"{e}. Is Ollama running?")

    cycle = ["triage/triage.py"] if args.no_model else ["platform/run.py", "--once", "--limit", "1"]
    code, out, secs = run(cycle, env)
    triage_line = next((line for line in out.splitlines() if "queued" in line and "invoices" in line), "")
    match = re.search(r"([\d.]+)s$", triage_line.strip())
    report("PASS" if match and float(match.group(1)) < 2 else "FAIL", "triage under 2 seconds", triage_line.strip())
    if not args.no_model:
        report("PASS" if "disputes:" in out else "FAIL", "disputes step ran",
               next((line.strip() for line in out.splitlines() if "disputes:" in line), ""))
        drafted = re.search(r"drafted (INV-\S+) .* in ([\d.]+)s(.*)", out)
        if drafted:
            seconds = float(drafted.group(2))
            report("PASS" if seconds < 15 else "WARN", "one real draft with the local model",
                   f"{drafted.group(1)} in {seconds:.1f}s{drafted.group(3)} (target under 15s on the GB10)")
        elif "still to draft" in out and "drafted 0, failed 0" in out:
            report("WARN", "one real draft with the local model", "nothing left to draft")
        else:
            report("FAIL", "one real draft with the local model", out[-1500:])
        report("PASS" if code == 0 else "FAIL", "full cycle (platform/run.py --once)", f"exit {code}, {secs:.1f}s")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, "channel/dashboard.py", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                get(base + "/crm")
                break
            except OSError:
                time.sleep(0.2)
        queue = store.read_json(store.QUEUE, {"items": []})
        sample = {b: next((i["invoice_id"] for i in queue["items"] if i["bucket"] == b), None)
                  for b in ("agent", "writeoff", "escalate")}
        paths = ["/", "/?tab=agent", "/?tab=writeoff", "/?tab=escalate", "/crm"]
        paths += [f"/invoice/{iid}" for iid in sample.values() if iid]
        failed = []
        for path in paths:
            try:
                status, _ = get(base + path)
            except OSError as e:
                status = str(e)
            if status != 200:
                failed.append(f"{path} -> {status}")
        report("FAIL" if failed else "PASS", "dashboard pages", "; ".join(failed) or f"{len(paths)} pages answered 200")
    finally:
        server.terminate()
        server.wait()

    blocked = log.read_text(encoding="utf-8").split() if log.exists() else []
    report("FAIL" if blocked else "PASS", "nothing tried to leave this machine",
           f"blocked: {', '.join(sorted(set(blocked)))}" if blocked else "no outside connection attempted")
    log.unlink(missing_ok=True)

    print(f"\n{results.count('PASS')} passed, {results.count('WARN')} warnings, {results.count('FAIL')} failed")
    return 1 if "FAIL" in results else 0


if __name__ == "__main__":
    raise SystemExit(main())

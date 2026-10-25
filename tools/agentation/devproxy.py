"""Dev-only: put the Agentation toolbar on any of our pages without touching their code.

    cd tools/agentation && npm install && npm run build      # once, needs internet
    python tools/agentation/devproxy.py --target 8080 --port 9080   # approval dashboard
    python tools/agentation/devproxy.py --target 8090 --port 9090   # dispute desk

Open http://127.0.0.1:9080 instead of :8080. Click the toolbar (bottom right), click an element, write what to
change; with `npx -y agentation-mcp server` registered in Claude Code the note reaches the agent directly.
Never run this on the box for the demo: it is a laptop tool.
"""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent / "dist" / "agentation.js"
TAG = b'<script src="/__agentation.js"></script>'
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding",
       "content-security-policy", "upgrade"}


def make_handler(target_host, target_port):
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def forward(self):
            if self.path == "/__agentation.js":
                body = BUNDLE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {"host", "accept-encoding"}}
            conn = http.client.HTTPConnection(target_host, target_port, timeout=60)
            conn.request(self.command, self.path, body=data, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            if "text/html" in (resp.getheader("Content-Type") or ""):
                body = body.replace(b"</body>", TAG + b"</body>", 1) if b"</body>" in body else body + TAG
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = forward

    return Proxy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, required=True, help="port of the page to wrap, e.g. 8080")
    ap.add_argument("--target-host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    if not BUNDLE.exists():
        raise SystemExit(f"{BUNDLE} missing: run `npm install && npm run build` in tools/agentation first")
    print(f"http://127.0.0.1:{args.port} -> {args.target_host}:{args.target} with the Agentation toolbar")
    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.target_host, args.target)).serve_forever()


if __name__ == "__main__":
    main()

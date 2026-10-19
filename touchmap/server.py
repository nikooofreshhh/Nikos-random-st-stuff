#!/usr/bin/env python3
"""Tiny local server: serves index.html, accepts recorded touch sessions, renders them.

    python server.py              # then open http://127.0.0.1:8765 in Chrome on the phone
    python server.py --port 9000
    python server.py --host 0.0.0.0   # if you want to open the page from another device

Saved sessions land in ./sessions/<timestamp>.{json,png,txt}.
Re-render an old session with:  python render.py sessions/<timestamp>.json
"""
import argparse
import datetime
import http.server
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "sessions")
sys.path.insert(0, HERE)
from render import render_session  # noqa: E402


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, fmt, *args):
        if "/save" in fmt % args or "code 4" in fmt % args or "code 5" in fmt % args:
            super().log_message(fmt, *args)

    def send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/save":
            return self.send_json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("events"), list):
                return self.send_json(400, {"error": "bad payload"})
            os.makedirs(SESSIONS, exist_ok=True)
            name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = os.path.join(SESSIONS, name)
            with open(base + ".json", "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            png, txt = render_session(data, base)
            print(f"saved {base}.json\n  -> {png}\n  -> {txt}", flush=True)
            self.send_json(200, {"name": name, "json": f"sessions/{name}.json",
                                 "png": f"sessions/{name}.png", "txt": f"sessions/{name}.txt"})
        except Exception as exc:  # report to the page instead of dying
            import traceback
            traceback.print_exc()
            self.send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    os.makedirs(SESSIONS, exist_ok=True)
    srv = http.server.ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"touchmap: open http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port} in Chrome  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

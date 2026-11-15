#!/usr/bin/env python3
"""Local UI for aaf2drt. Binds to 127.0.0.1 only; every request needs the session token.
Nothing leaves this computer: the conversion runs locally in a child process."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))   # index.html
MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
TOKEN = secrets.token_urlsafe(16)
STATE = {"proc": None, "log": [], "done": True, "report": None, "mode": None, "returncode": None,
         "progress": None, "last_ping": time.time()}
LOCK = threading.Lock()


def osa(script):
    p = subprocess.run(["osascript", "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8").strip()


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def pick(kind, prompt, start=None):
    loc = ""
    if start and os.path.isdir(start):
        loc = ' default location (POSIX file "%s")' % esc(start)
    if kind == "file":
        script = 'POSIX path of (choose file with prompt "%s"%s)' % (esc(prompt), loc)
    else:
        script = 'POSIX path of (choose folder with prompt "%s"%s)' % (esc(prompt), loc)
    r = osa("tell me to activate\n" + script)
    if r and kind == "folder":
        r = r.rstrip("/")
    return r


def ffmpeg_path():
    """ffmpeg shipped inside the app (imageio-ffmpeg), else one on the PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def env_check():
    return {"ffmpeg": ffmpeg_path(), "python": sys.version.split()[0]}


def run_engine(params):
    args = ["--engine", "--aaf", params["aaf"], "--out", params["out"],
            "--handles", str(int(params.get("handles", 10)))]
    cmd = [sys.executable] + args if FROZEN else [sys.executable, "-u", "-B", MAIN] + args
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    ff = ffmpeg_path()
    if ff:
        env["FFMPEG"] = ff
    with LOCK:
        STATE.update(log=["$ aaf2drt " + os.path.basename(params["aaf"])], done=False, report=None, mode="build",
                     returncode=None, progress=None)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    STATE["proc"] = proc
    n = 0
    for line in proc.stdout:
        line = line.rstrip("\n")
        with LOCK:
            STATE["log"].append(line)
            if line.startswith("  ") and line.strip().endswith("s") and ".wav" in line:
                n += 1
                STATE["progress"] = {"i": n, "n": 0, "file": line.strip()}
    proc.wait()
    rep = None
    rp = os.path.join(params["out"], "aaf2drt_report.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as fh:
            rep = json.load(fh)
    drts = [f for f in os.listdir(params["out"]) if f.endswith(".drt")] if os.path.isdir(params["out"]) else []
    with LOCK:
        STATE.update(done=True, returncode=proc.returncode, report={"rows": rep or [], "drt": drts}, proc=None)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        return self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            if ("t=" + TOKEN) not in self.path:
                return self._send(403, b"forbidden", "text/plain")
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                return self._send(200, fh.read().replace(b"__TOKEN__", TOKEN.encode()), "text/html; charset=utf-8")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self._auth():
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        p = self.path
        STATE["last_ping"] = time.time()
        if p == "/env":
            return self._send(200, env_check())
        if p == "/pick":
            path = pick(body.get("kind", "folder"), body.get("prompt", "Kies"), body.get("start"))
            return self._send(200, {"path": path})
        if p == "/run":
            if not STATE["done"]:
                return self._send(409, {"error": "er loopt al iets"})
            if not body.get("aaf") or not os.path.exists(body["aaf"]):
                return self._send(400, {"error": "Kies eerst een AAF."})
            if not body.get("out"):
                return self._send(400, {"error": "Kies een uitvoermap."})
            os.makedirs(body["out"], exist_ok=True)
            threading.Thread(target=run_engine, args=(body,), daemon=True).start()
            return self._send(200, {"ok": True})
        if p == "/status":
            since = int(body.get("since", 0))
            with LOCK:
                return self._send(200, {"log": STATE["log"][since:], "n": len(STATE["log"]),
                                        "done": STATE["done"], "returncode": STATE["returncode"],
                                        "report": STATE["report"] if STATE["done"] else None,
                                        "mode": STATE["mode"], "progress": STATE["progress"]})
        if p == "/stop":
            pr = STATE.get("proc")
            if pr:
                pr.terminate()
            return self._send(200, {"ok": True})
        if p == "/reveal":
            path = body.get("path")
            if path and os.path.exists(path):
                subprocess.run(["open", "-R", path] if os.path.isfile(path) else ["open", path])
            return self._send(200, {"ok": True})
        if p == "/ping":
            return self._send(200, {"ok": True})
        if p == "/quit":
            self._send(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        self._send(404, {"error": "unknown"})


def watchdog():
    # quit when the browser tab has been gone for a while and nothing is running
    while True:
        time.sleep(10)
        if STATE["done"] and time.time() - STATE["last_ping"] > 90:
            os._exit(0)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    url = "http://127.0.0.1:%d/?t=%s" % (srv.server_address[1], TOKEN)
    threading.Thread(target=watchdog, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    print(url, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

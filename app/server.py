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


def app_version():
    try:
        import plistlib
        exe = os.path.realpath(sys.executable)
        plist = os.path.join(os.path.dirname(os.path.dirname(exe)), "Info.plist")
        with open(plist, "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString", "?")
    except Exception:
        return "dev"


def bundle_report(out):
    """Zip the log, the report and system info (no media, no AAF) for a bug report."""
    import platform
    import zipfile
    dst = os.path.join(out, "aaf2drt_rapport.zip")
    info = {"app": app_version(), "macos": platform.mac_ver()[0], "machine": platform.machine(),
            "python": sys.version.split()[0], "ffmpeg": bool(ffmpeg_path())}
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("systeem.json", json.dumps(info, indent=2))
        for name in ("aaf2drt_log.txt", "aaf2drt_report.json"):
            p = os.path.join(out, name)
            if os.path.exists(p):
                z.write(p, name)
    return dst


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
    try:   # keep the log next to the output, for bug reports
        with open(os.path.join(params["out"], "aaf2drt_log.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(STATE["log"]) + "\n")
    except OSError:
        pass
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
        if p == "/bundle":
            out = body.get("out")
            if not out or not os.path.isdir(out):
                return self._send(400, {"error": "geen uitvoermap"})
            dst = bundle_report(out)
            subprocess.run(["open", "-R", dst])
            return self._send(200, {"ok": True, "path": dst})
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


def open_window(url, srv):
    """Show the UI in the app's own window (macOS WebKit via pywebview). Returns False if not available."""
    try:
        import webview
    except Exception:
        return False
    win = webview.create_window("AAF naar Resolve", url, width=900, height=820, min_size=(640, 560))

    def closing():
        p = STATE.get("proc")
        if p and p.poll() is None:      # stop a running conversion with the window
            p.terminate()

    win.events.closing += closing
    webview.start()                      # blocks on the main thread until the window is closed
    srv.shutdown()
    return True


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    url = "http://127.0.0.1:%d/?t=%s" % (srv.server_address[1], TOKEN)
    print(url, flush=True)
    if "--no-browser" in sys.argv:
        srv.serve_forever()
        return
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    if "--browser" not in sys.argv and open_window(url, srv):
        return
    # fallback: the default browser; the watchdog quits once the page is gone
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    t.join()


if __name__ == "__main__":
    main()

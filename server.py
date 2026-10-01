#!/usr/bin/env python3
import base64
import datetime
import hashlib
import json
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import threading
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from timelapse import Timelapse

HOST = "10.42.0.2"
PORT = 8093
APP = Path("/home/copycatbrain/universal-nodes")
HOME = Path("/home/copycatbrain")
MPREMOTE = HOME / "scanner-venv/bin/mpremote"
CAPTURES = APP / "captures"
CAPTURES.mkdir(parents=True, exist_ok=True)
CAMERA_LOCK = threading.Lock()
CAMERA_SERVICE = "http://127.0.0.1:8090"
TRASH = APP / "capture-trash"
TRASH.mkdir(parents=True, exist_ok=True)
PHOTO_SIZES = {"instant": (1280,720), "1920x1080": (1920,1080), "3840x2160": (3840,2160), "9248x6944": (9248,6944)}

def capture_path(name, trash=False):
    if not isinstance(name, str) or not re.fullmatch(r"capture_[0-9_]+\.jpg", name):
        raise ValueError("Invalid photo filename")
    folder = TRASH if trash else CAPTURES
    path = folder / name
    if path.is_symlink():
        raise ValueError("Invalid photo path")
    return path

def camera_request(path, post=False, payload=None):
    """Use the existing camera owner instead of opening the hardware twice."""
    request = Request(CAMERA_SERVICE + path, data=json.dumps(payload or {}).encode() if post else None,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=55) as response:
            return response.read()
    except HTTPError as exc:
        message = exc.read().decode("utf-8", "replace")
        try: message = json.loads(message).get("error", message)
        except ValueError: pass
        raise RuntimeError(message[:1500]) from exc
    except URLError as exc:
        raise RuntimeError("Camera control service on port 8090 is unavailable") from exc

def check_camera(camera):
    if camera not in (0, 1):
        raise ValueError("Select Camera 1 or Camera 2")

def timelapse_capture(camera, config, count, target):
    focus = config['focus']
    mode = 'off' if focus == 'off' else 'hold'
    if focus == 'every' or (focus == 'once' and count == 0):
        camera_request('/api/camera/focus', post=True, payload={'camera': camera, 'focus_mode': 'hold', 'refocus': True})
    result = json.loads(camera_request('/api/camera/capture', post=True,
                        payload={'camera': camera, 'resolution': config['resolution'], 'focus_mode': mode}))
    name = result.get('filename', '')
    if not re.fullmatch(r'capture_[0-9_]+\.jpg', name):
        raise ValueError('Invalid camera photo filename')
    source = HOME / 'pi-control/captures' / name
    if source.is_symlink() or not source.is_file():
        raise ValueError('Camera photo unavailable')
    source.replace(target)  # this session's new frame, never an existing gallery photo

TIMELAPSE = Timelapse(APP / 'timelapses', timelapse_capture,
                      lambda: json.loads(camera_request('/api/camera/status')), CAMERA_LOCK)

VALID_GP = set(range(29))

def jdump(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")

def valid_gp(value):
    gp = int(value)
    if gp not in VALID_GP:
        raise ValueError("GPIO must be GP0 through GP28")
    return gp

def pico_exec(code, timeout=8):
    if not MPREMOTE.exists():
        raise RuntimeError(f"mpremote not found at {MPREMOTE}")
    p = subprocess.run(
        [str(MPREMOTE), "connect", "/dev/ttyACM0", "exec", code],
        capture_output=True, text=True, timeout=timeout
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "Pico command failed").strip())
    lines = [x.strip() for x in p.stdout.splitlines() if x.strip() and x.strip() != "None"]
    return lines[-1] if lines else ""

def safe_user_path(raw):
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = HOME / p
    p = p.resolve(strict=False)
    home = HOME.resolve()
    if p != home and home not in p.parents:
        raise PermissionError("File editor is limited to /home/copycatbrain")
    return p

def ws_accept(key):
    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    return base64.b64encode(hashlib.sha1((key + guid).encode()).digest()).decode()

def recv_exact(sock, n):
    b = bytearray()
    while len(b) < n:
        chunk = sock.recv(n-len(b))
        if not chunk:
            return None
        b.extend(chunk)
    return bytes(b)

def recv_ws_frame(sock):
    h = recv_exact(sock, 2)
    if not h:
        return None, None
    b1, b2 = h
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        x = recv_exact(sock, 2)
        if not x: return None, None
        length = struct.unpack("!H", x)[0]
    elif length == 127:
        x = recv_exact(sock, 8)
        if not x: return None, None
        length = struct.unpack("!Q", x)[0]
    mask = recv_exact(sock, 4) if masked else None
    data = recv_exact(sock, length) if length else b""
    if data is None:
        return None, None
    if masked:
        data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
    return opcode, data

def send_ws_frame(sock, payload=b"", opcode=1):
    if isinstance(payload, str):
        payload = payload.encode("utf-8", "replace")
    n = len(payload)
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", n))
    sock.sendall(bytes(header) + payload)

class Handler(BaseHTTPRequestHandler):
    server_version = "BookScannerV6/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.client_address[0], fmt % args))

    def send_bytes(self, data, status=200, ctype="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        self.send_bytes(jdump(obj), status, "application/json; charset=utf-8")

    def body_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/ws/terminal" and self.headers.get("Upgrade", "").lower() == "websocket":
            return self.handle_terminal_ws()

        try:
            if path == "/":
                return self.send_bytes((APP/"index.html").read_bytes(), 200, "text/html; charset=utf-8")

            if path in ("/studio.js", "/studio.css", "/timelapse-ui.js"):
                ctype = "text/javascript" if path.endswith(".js") else "text/css"
                return self.send_bytes((APP/path[1:]).read_bytes(), 200, ctype+"; charset=utf-8")

            if path == "/api/status":
                return self.send_json({
                    "hostname": socket.gethostname(),
                    "pico_connected": Path("/dev/ttyACM0").exists(),
                    "mpremote": MPREMOTE.exists(),
                    "version": "V6-self-contained"
                })

            if path == "/api/camera/status":
                return self.send_bytes(camera_request("/api/camera/status"),200,"application/json")

            if path == '/api/timelapse/status':
                return self.send_json(TIMELAPSE.status())

            if re.fullmatch(r'/timelapses/[a-f0-9]{32}/camera-[01]/video\.mp4', path):
                video = APP / path.lstrip('/')
                if not video.is_file() or video.is_symlink():
                    return self.send_json({'error': 'Video not found'}, 404)
                size = video.stat().st_size
                start, end, status = 0, size-1, 200
                value = self.headers.get('Range')
                if value:
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', value)
                    if not match:
                        return self.send_json({'error': 'Unsupported byte range'}, 416)
                    start = int(match[1]); end = min(int(match[2]) if match[2] else size-1, size-1)
                    if start > end:
                        return self.send_json({'error': 'Range outside video'}, 416)
                    status = 206
                self.send_response(status)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end-start+1))
                if status == 206:self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                with video.open('rb') as stream:
                    stream.seek(start)
                    remaining = end-start+1
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:break
                        self.wfile.write(chunk); remaining -= len(chunk)
                return

            if path == "/api/sensor/ds18b20":
                gp = valid_gp(qs.get("gp", ["26"])[0])
                code = (
                    "from machine import Pin;"
                    "import onewire,ds18x20,time;"
                    f"d=ds18x20.DS18X20(onewire.OneWire(Pin({gp})));"
                    "r=d.scan();assert r,'NO_SENSOR';"
                    "d.convert_temp();time.sleep_ms(750);"
                    "c=d.read_temp(r[0]);print(str(c)+','+str(c*9/5+32))"
                )
                out = pico_exec(code)
                c, f = map(float, out.split(","))
                return self.send_json({"gp": gp, "celsius": c, "fahrenheit": f})

            if path == "/api/gpio/read":
                gp = valid_gp(qs["gp"][0])
                pull = qs.get("pull", ["NONE"])[0].upper()
                pull_code = {"NONE":"None","UP":"Pin.PULL_UP","DOWN":"Pin.PULL_DOWN"}[pull]
                out = pico_exec(
                    f"from machine import Pin;"
                    f"p=Pin({gp},Pin.IN,pull={pull_code});print(p.value())"
                )
                return self.send_json({"gp":gp,"value":int(out)})

            if path == "/api/file/read":
                p = safe_user_path(qs["path"][0])
                return self.send_json({"path":str(p),"content":p.read_text()})

            if path.startswith(("/captures/", "/trash-captures/")):
                p = capture_path(unquote(path.rsplit("/",1)[-1]), path.startswith("/trash-captures/"))
                if not p.exists():
                    return self.send_json({"error":"Capture not found"},404)
                return self.send_bytes(p.read_bytes(),200,"image/jpeg")

            if path == "/api/captures":
                captures = []
                trash = qs.get("trash",["0"])[0] == "1"
                for p in sorted((TRASH if trash else CAPTURES).glob("capture_*.jpg"), key=lambda x: x.stat().st_mtime, reverse=True):
                    if p.is_symlink():continue
                    captures.append({"name": p.name, "url": ("/trash-captures/" if trash else "/captures/") + p.name, "bytes": p.stat().st_size, "taken": p.stat().st_mtime})
                return self.send_json({"captures": captures})

            if path == "/camera/frame.jpg":
                cam = int(qs.get("camera", ["0"])[0])
                check_camera(cam)
                return self.send_bytes(camera_request(f"/camera/frame.jpg?camera={cam}"), 200, "image/jpeg")

            return self.send_json({"error":"Not found"},404)

        except Exception as e:
            return self.send_json({"error":str(e)},500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            data = self.body_json()

            if path.startswith('/api/timelapse/'):
                try:
                    if path.endswith('/start'):return self.send_json(TIMELAPSE.start(data))
                    if path.endswith('/stop'):return self.send_json(TIMELAPSE.stop())
                    if path.endswith('/rebuild'):return self.send_json(TIMELAPSE.rebuild(data.get('id')))
                except ValueError as exc:
                    return self.send_json({'error': str(exc)}, 409)

            if path in ('/api/camera/focus', '/api/camera/restart', '/api/camera/action') and TIMELAPSE.active:
                return self.send_json({'error':'Timelapse is recording or processing. Use Stop & build video before manual camera actions.'},409)

            if path in ("/api/captures/delete", "/api/captures/restore"):
                restoring = path.endswith("restore")
                source = capture_path(data.get("name"), restoring)
                target = capture_path(data.get("name"), not restoring)
                if not source.is_file():return self.send_json({"error":"Photo not found"},404)
                if target.exists():return self.send_json({"error":"A photo with that name already exists"},409)
                source.rename(target)
                return self.send_json({"ok":True,"recoverable":True})

            if path == "/api/gpio/write":
                gp = valid_gp(data["gp"])
                value = 1 if int(data.get("value",0)) else 0
                pico_exec(
                    f"from machine import Pin;"
                    f"p=Pin({gp},Pin.OUT);p.value({value});print(p.value())"
                )
                return self.send_json({"gp":gp,"value":value})

            if path == "/api/gpio/pwm":
                gp = valid_gp(data["gp"])
                duty = max(0.0,min(100.0,float(data.get("duty",0))))
                freq = max(1,int(data.get("freq",1000)))
                u16 = round(duty/100.0*65535)
                pico_exec(
                    f"from machine import Pin,PWM;"
                    f"p=PWM(Pin({gp}));p.freq({freq});p.duty_u16({u16});print('OK')"
                )
                return self.send_json({"gp":gp,"duty":duty,"freq":freq})

            if path == "/api/camera/focus":
                return self.send_json(json.loads(camera_request("/api/camera/focus",post=True,payload=data)))

            if path == "/api/camera/restart":
                if CAMERA_LOCK.locked():
                    return self.send_json({"error":"Wait for the current photo to finish"},409)
                return self.send_json(json.loads(camera_request("/api/camera/restart",post=True)))

            if path == "/api/camera/action":
                cam = int(data.get("camera",0))
                check_camera(cam)
                mode = data.get("mode","Take Picture")
                if mode == "Live Feed":
                    return self.send_json({"url":f"/camera/frame.jpg?camera={cam}"})
                resolution = data.get("resolution", "9248x6944")
                if resolution not in PHOTO_SIZES:
                    return self.send_json({"error":"Unsupported resolution"},400)
                started = time.monotonic()
                filename = "capture_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jpg"
                dest = CAPTURES / filename
                if not CAMERA_LOCK.acquire(blocking=False):
                    return self.send_json({"error":"Camera is already taking a photo. Try again when it finishes."},409)
                try:
                    result = {}
                    if resolution == "instant":
                        photo = camera_request(f"/camera/frame.jpg?camera={cam}")
                    else:
                        result = json.loads(camera_request("/api/camera/capture", post=True, payload={"camera":cam,"resolution":resolution,"focus_mode":data.get("focus_mode","hold")}))
                        url = result.get("url", "")
                        if not url.startswith("/captures/") or ".." in url:
                            raise RuntimeError("Camera service returned an invalid capture path")
                        photo = camera_request(url)
                    if not photo.startswith(b"\xff\xd8"):
                        raise RuntimeError("Camera service did not return a JPEG")
                    temporary = dest.with_suffix(".part")
                    try:
                        temporary.write_bytes(photo)
                        temporary.replace(dest)
                    finally:
                        if temporary.exists():
                            temporary.unlink()  # only this request's incomplete gallery copy
                finally:CAMERA_LOCK.release()
                width,height=PHOTO_SIZES[resolution]
                return self.send_json({"url":"/captures/"+filename,"filename":filename,"width":width,"height":height,"resolution":resolution,"elapsed_ms":round((time.monotonic()-started)*1000),"focus":result.get("focus"),"shutter_ms":result.get("shutter_ms"),"save_ms":result.get("save_ms")})

            if path == "/api/file/write":
                p = safe_user_path(data["path"])
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(data.get("content",""))
                return self.send_json({"ok":True,"path":str(p)})

            return self.send_json({"error":"Not found"},404)

        except Exception as e:
            return self.send_json({"error":str(e)},500)

    def handle_terminal_ws(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self.send_json({"error":"Missing WebSocket key"},400)

        self.send_response(101,"Switching Protocols")
        self.send_header("Upgrade","websocket")
        self.send_header("Connection","Upgrade")
        self.send_header("Sec-WebSocket-Accept",ws_accept(key))
        self.end_headers()
        self.wfile.flush()

        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(str(HOME))
            env = os.environ.copy()
            env.update({
                "HOME":str(HOME),
                "USER":"copycatbrain",
                "LOGNAME":"copycatbrain",
                "SHELL":"/bin/bash",
                "TERM":"xterm"
            })
            os.execve("/bin/bash",["/bin/bash","-l"],env)

        conn = self.connection
        conn.setblocking(True)

        try:
            while True:
                ready,_,_ = select.select([conn,fd],[],[],0.25)

                if fd in ready:
                    try:
                        chunk = os.read(fd,4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    send_ws_frame(conn,chunk.decode("utf-8","replace"),1)

                if conn in ready:
                    opcode,payload = recv_ws_frame(conn)
                    if opcode is None or opcode == 8:
                        break
                    if opcode == 9:
                        send_ws_frame(conn,payload,10)
                        continue
                    if opcode in (1,2):
                        os.write(fd,payload)

        except Exception:
            pass
        finally:
            try: os.close(fd)
            except Exception: pass
            try: os.kill(pid,signal.SIGHUP)
            except Exception: pass
            try: os.waitpid(pid,os.WNOHANG)
            except Exception: pass
        self.close_connection = True

def main():
    if not APP.exists():
        raise SystemExit(f"Application directory not found: {APP}")
    httpd = ThreadingHTTPServer((HOST,PORT),Handler)
    print(f"Book Scanner V6 listening at http://{HOST}:{PORT}")
    httpd.serve_forever()

if __name__ == "__main__":
    main()

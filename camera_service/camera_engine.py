#!/usr/bin/python3
"""Persistent full-sensor camera owner. Runs with the Pi's system Picamera2.

The Flask service talks over a user-only Unix socket. No public extra port,
camera mode switches, or per-photo camera process startups are needed.
"""
import datetime
import io
import json
import os
import shutil
from pathlib import Path
import socketserver
import threading
import time
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

BASE = Path(__file__).resolve().parent
SOCKET = BASE / 'camera-engine.sock'
SIZES = {'1920x1080': (1920, 1080), '3840x2160': (3840, 2160), '9248x6944': (9248, 6944)}


class FrameGate:
    """Hand off only a focused frame exposed after the shutter request.

    Buffer references must be released by the consumer, including on timeout.
    Shared by the camera callback and HTTP capture handler.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.after = None
        self.request = None

    def arm(self, timestamp, require_focus=True):
        with self.lock:
            self.event.clear()
            self.after = timestamp
            self.require_focus = require_focus

    def offer(self, request, metadata):
        with self.lock:
            if (self.after is not None and metadata.get('SensorTimestamp', 0) >= self.after
                    and (not self.require_focus or metadata.get('AfState') == 2)):
                request.acquire()
                self.request = request
                self.after = None
                self.event.set()

    def take(self, timeout):
        self.event.wait(timeout)
        with self.lock:
            self.after = None
            request, self.request = self.request, None
        if request is None:
            raise TimeoutError('Autofocus has not locked. Keep the book still, add light, and try again.')
        return request


class CameraEngine:
    def __init__(self, camera_id=0):
        from picamera2 import Picamera2
        from libcamera import controls
        self.controls = controls
        self.lock = threading.Lock()
        self.capture_lock = threading.Lock()
        self.gate = FrameGate()
        self.jpeg = None
        self.frame_at = 0
        self.metadata = {}
        self.phase = 'starting'
        self.focus_mode = 'hold'
        self.scan_pending = True
        self.scan_started = False
        self.last_metadata_at = time.monotonic()
        self.camera = Picamera2(camera_id)
        if 'AfMode' not in self.camera.camera_controls:
            raise RuntimeError('This camera does not expose autofocus controls')
        # Three YUV buffers keep the sensor running while one full-size image
        # is JPEG-encoded. No raw buffer or full-size RGB copy is required.
        config = self.camera.create_still_configuration(
            main={'size': (9248, 6944), 'format': 'YUV420'},
            lores={'size': (1280, 960), 'format': 'YUV420'},
            raw=None, buffer_count=3, queue=False,
            controls={'AfMode': controls.AfModeEnum.Auto,
                      'AfRange': controls.AfRangeEnum.Full,
                      'AfSpeed': controls.AfSpeedEnum.Fast,
                      'FrameDurationLimits': (100, 250000),
                      'NoiseReductionMode': controls.draft.NoiseReductionModeEnum.Fast})
        self.camera.configure(config)
        actual = tuple(self.camera.camera_configuration()['main']['size'])
        if actual != (9248, 6944):
            raise RuntimeError(f'Full-resolution configuration changed unexpectedly: {actual}')
        self.camera.options['quality'] = 90
        self.camera.pre_callback = self.on_frame
        print('Ready-camera configuration:', self.camera.camera_configuration(), flush=True)
        self.camera.start()
        self.camera.set_controls({'AfTrigger': controls.AfTriggerEnum.Start})
        self.phase = 'ready'

    def on_frame(self, request):
        metadata = request.get_metadata()
        with self.lock:
            self.metadata = metadata
            self.last_metadata_at = time.monotonic()
            if self.scan_pending:
                if metadata.get('AfState') == 1:
                    self.scan_started = True
                elif self.scan_started and metadata.get('AfState') in (2, 3):
                    self.scan_pending = False
            if not self.scan_pending:
                self.gate.offer(request, metadata)
        # Low-resolution encoding never touches the 64 MP main buffer.
        now = time.monotonic()
        if now - self.frame_at < .12:
            return
        import simplejpeg
        array = request.make_array('lores')
        y = array[:960, :1280]
        uv = array[960:, :1280].reshape(2, 480, 640)
        # Center crop the Y/U/V planes to the existing 1280x720 API.
        jpeg = simplejpeg.encode_jpeg_yuv_planes(y[120:840], uv[0, 60:420], uv[1, 60:420], 80)
        with self.lock:
            self.jpeg, self.frame_at = jpeg, now

    def status(self):
        with self.lock:
            af = 1 if self.scan_pending else self.metadata.get('AfState')
            return {'streaming': self.jpeg is not None and time.monotonic() - self.frame_at < 3,
                    'focus': {0: 'idle', 1: 'focusing', 2: 'focused', 3: 'not-focused'}.get(af, 'starting'),
                    'lens_position': self.metadata.get('LensPosition'),
                    'focus_mode': self.focus_mode,
                    'phase': self.phase, 'resolution': '9248x6944',
                    'frame_duration_us': self.metadata.get('FrameDuration'), 'error': ''}

    def _set_focus(self, mode, refocus=False):
        """Caller holds capture_lock; metadata callbacks use only self.lock."""
        if mode not in ('hold', 'continuous', 'off'):
            raise ValueError('Unsupported focus mode')
        with self.lock:
            if mode == self.focus_mode and not refocus:
                return
            if mode == 'off':
                self.camera.set_controls({'AfMode': self.controls.AfModeEnum.Manual,
                                          'LensPosition': self.metadata.get('LensPosition', 1.0)})
                self.scan_pending = False
                self.focus_mode = mode
                return
            # Do not accept a stale 'focused' frame before the new scan starts.
            self.scan_pending = True
            self.scan_started = False
            af_mode = self.controls.AfModeEnum.Auto if mode == 'hold' else self.controls.AfModeEnum.Continuous
            values = {'AfMode': af_mode}
            if mode == 'hold':
                values['AfTrigger'] = self.controls.AfTriggerEnum.Start
            elif refocus:
                # Continuous mode's explicit resume requests a new search.
                values['AfPause'] = self.controls.AfPauseEnum.Resume
            self.camera.set_controls(values)
            self.focus_mode = mode

    def focus(self, mode='hold', refocus=False):
        if not self.capture_lock.acquire(blocking=False):
            raise RuntimeError('Finish the current photo before changing focus')
        try:
            self._set_focus(mode, refocus)
            return self.status()
        finally:
            self.capture_lock.release()

    def capture(self, resolution, focus_mode='hold'):
        if resolution not in SIZES:
            raise ValueError('Unsupported photo resolution')
        if not self.capture_lock.acquire(blocking=False):
            raise RuntimeError('Camera is already taking a photo')
        started = time.monotonic()
        request = None
        temporary = None
        try:
            if shutil.disk_usage(BASE / 'captures').free < 256 * 1024 * 1024:
                raise RuntimeError('Storage nearly full. Free space before taking more photos.')
            self._set_focus(focus_mode)
            with self.lock:
                af = 1 if self.scan_pending else self.metadata.get('AfState')
                self.phase = 'focusing' if af != 2 else 'capturing'
            # Auto mode holds its result indefinitely: never restart autofocus
            # between same-height book pages. Retry only idle/failed searches.
            if focus_mode != 'off' and af in (0, 3):
                self._set_focus('hold', refocus=True)
            if focus_mode == 'off':
                self.gate.arm(time.monotonic_ns(), require_focus=False)
            else:
                self.gate.arm(time.monotonic_ns())
            request = self.gate.take(40)
            acquired = time.monotonic()
            metadata = request.get_metadata()
            with self.lock:
                self.phase = 'saving'
            filename = 'capture_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.jpg'
            path = BASE / 'captures' / filename
            temporary = path.with_suffix('.part')
            if resolution == '9248x6944':
                request.save('main', str(temporary), format='jpeg')
            else:
                # Derive smaller options from the same focused sensor exposure.
                from PIL import Image, ImageOps
                encoded = io.BytesIO()
                request.save('main', encoded, format='jpeg')
                encoded.seek(0)
                with Image.open(encoded) as image:
                    image = ImageOps.fit(image, SIZES[resolution], method=Image.Resampling.LANCZOS)
                    image.save(temporary, format='JPEG', quality=90)
            os.replace(temporary, path)
            result = {'filename': filename, 'url': '/captures/' + filename,
                      'focus': 'unchecked' if focus_mode == 'off' else 'focused', 'focus_mode': self.focus_mode, 'lens_position': metadata.get('LensPosition'),
                      'shutter_ms': round((acquired - started) * 1000),
                      'save_ms': round((time.monotonic() - acquired) * 1000),
                      'elapsed_ms': round((time.monotonic() - started) * 1000)}
            print('Ready-camera capture:', json.dumps(result), flush=True)
            return result
        finally:
            try:
                if request is not None:
                    request.release()
            finally:
                try:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()  # only this request's incomplete output
                finally:
                    with self.lock:
                        self.phase = 'ready'
                    self.capture_lock.release()


class CameraBank:
    """One owner per detected camera; a missing camera never aliases camera 0."""
    def __init__(self):
        from picamera2 import Picamera2
        self.engines = {}
        self.info = Picamera2.global_camera_info()
        self.errors = {}
        for info in self.info:
            index = info['Num']
            try:
                self.engines[index] = CameraEngine(index)
            except Exception as exc:
                self.errors[index] = str(exc)
        threading.Thread(target=self.watchdog, daemon=True).start()

    def get(self, index):
        index = int(index)
        if index not in self.engines:
            raise ValueError(self.errors.get(index, f'Camera {index + 1} not detected. Power off before checking its ribbon cable.'))
        return self.engines[index]

    def status(self):
        cameras = []
        for info in self.info:
            index = info['Num']
            status = self.engines[index].status() if index in self.engines else {'streaming': False, 'error': self.errors[index]}
            cameras.append(dict(status, camera=index, model=info['Model'], detected=True, device=info.get('Id', '')))
        legacy = dict(cameras[0]) if cameras else {'streaming': False, 'error': 'No cameras detected. Power off before checking cables.'}
        return dict(legacy, cameras=cameras)

    def watchdog(self):
        while True:
            time.sleep(5)
            if any(time.monotonic() - engine.last_metadata_at > 20 for engine in self.engines.values()):
                print('Camera frames stalled; restarting camera owner', flush=True)
                os._exit(1)  # supervisor restarts; kernel releases all camera handles


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, status=200, content_type='application/json'):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/status':
            return self.reply(self.server.bank.status())
        try:
            engine = self.server.bank.get(parse_qs(parsed.query).get('camera', ['0'])[0])
        except ValueError as exc:
            return self.reply({'error': str(exc)}, 404)
        if parsed.path == '/frame':
            with engine.lock:
                jpeg = engine.jpeg if time.monotonic() - engine.frame_at < 3 else None
            return self.reply(jpeg, content_type='image/jpeg') if jpeg else self.reply({'error': 'Camera warming up'}, 503)
        self.reply({'error': 'Not found'}, 404)

    def do_POST(self):
        if self.path not in ('/capture', '/focus'):
            return self.reply({'error': 'Not found'}, 404)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size < 1024:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(size))
            engine = self.server.bank.get(data.get('camera', 0))
            if self.path == '/focus':
                return self.reply(engine.focus(data.get('focus_mode', 'hold'), bool(data.get('refocus'))))
            self.reply(engine.capture(data.get('resolution', '9248x6944'), data.get('focus_mode', 'hold')))
        except ValueError as exc:
            self.reply({'error': str(exc)}, 400)
        except Exception as exc:
            self.reply({'error': str(exc)}, 503)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


if __name__ == '__main__':
    (BASE / 'captures').mkdir(exist_ok=True)
    bank = CameraBank()
    if SOCKET.exists():
        SOCKET.unlink()  # stale endpoint for this engine, never a capture
    with Server(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o600)
        server.bank = bank
        server.serve_forever()

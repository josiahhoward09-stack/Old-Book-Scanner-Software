"""Pi-owned, browser-independent timelapse jobs with retained source frames."""
import concurrent.futures
import copy
import json
import math
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

SIZES = {'1920x1080', '3840x2160', '9248x6944'}

def settings(data):
    result = {'cameras': data.get('cameras', [0]), 'resolution': data.get('resolution', '1920x1080'),
              'focus': data.get('focus', 'once')}
    if result['cameras'] not in ([0], [1], [0, 1]):
        raise ValueError('Choose Camera 1, Camera 2, or both')
    if result['resolution'] not in SIZES or result['focus'] not in ('once', 'every', 'off'):
        raise ValueError('Invalid resolution or focus setting')
    for key, low, high, default in [('interval', 1, 3600, 10), ('duration', 1, 86400, 3600), ('fps', 1, 60, 30)]:
        value = float(data.get(key, default))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} must be between {low} and {high}')
        result[key] = value
    if math.ceil(result['duration'] / result['interval']) * len(result['cameras']) > 20000:
        raise ValueError('Maximum 20,000 photos per session; increase the interval')
    return result


class Timelapse:
    def __init__(self, root, capture, camera_status, reservation):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.capture = capture
        self.camera_status = camera_status
        self.reservation = reservation
        self.lock = threading.RLock()
        self.jobs = {}
        self.active = None
        self.stop_event = threading.Event()
        for path in self.root.glob('*/job.json'):
            try:
                job = json.loads(path.read_text())
                if not re.fullmatch('[a-f0-9]{32}', job['id']):
                    continue
                if job['state'] in ('recording', 'stopping', 'processing'):
                    job.update(state='interrupted', error='Pi/service restarted. Source frames retained; use Build videos.')
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError):
                continue

    def save(self, job):
        with self.lock:
            folder = self.root / job['id']
            folder.mkdir(exist_ok=True)
            temp = folder / 'job.json.part'
            temp.write_text(json.dumps(job))
            temp.replace(folder / 'job.json')

    def status(self):
        with self.lock:
            return {'active': self.active, 'jobs': copy.deepcopy(sorted(self.jobs.values(), key=lambda j: j['started'], reverse=True))}

    def start(self, data):
        config = settings(data)
        if not shutil.which('ffmpeg'):
            raise ValueError('FFmpeg is not installed on the Pi')
        detected = {c['camera']: c for c in self.camera_status().get('cameras', [])}
        for camera in config['cameras']:
            if not detected.get(camera, {}).get('streaming'):
                raise ValueError(f'Camera {camera+1} is not streaming. Fix its connection before recording.')
        with self.lock:
            if self.active or not self.reservation.acquire(blocking=False):
                raise ValueError('A photo or timelapse is already running')
            try:
                job = dict(id=uuid.uuid4().hex, settings=config, started=time.time(), state='recording',
                           counts={str(c): 0 for c in config['cameras']}, missed=0, errors={}, videos=[], error='')
                self.save(job)
                self.jobs[job['id']] = job
                self.active = job['id']
                self.stop_event.clear()
                threading.Thread(target=self.record, args=(job,), daemon=True).start()
                return copy.deepcopy(job)
            except Exception:
                self.active = None
                self.reservation.release()
                raise

    def stop(self):
        with self.lock:
            if self.active and self.jobs[self.active]['state'] == 'recording':
                self.jobs[self.active]['state'] = 'stopping'
                self.stop_event.set()
            return {'message': 'Finishing current exposures, then building videos'}

    def shoot(self, job, camera):
        key = str(camera)
        folder = self.root / job['id'] / f'camera-{camera}'
        folder.mkdir(exist_ok=True)
        count = job['counts'][key]
        target = folder / f'{count:06d}.jpg'
        config = job['settings']
        self.capture(camera, config, count, target)
        with self.lock:
            job['counts'][key] += 1
            job['errors'].pop(key, None)

    def record(self, job):
        config = job['settings']
        began = time.monotonic()
        deadline = began + config['duration']
        due = began
        failures = {c: 0 for c in config['cameras']}
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    if self.stop_event.wait(max(0, min(due, deadline) - time.monotonic())) or time.monotonic() >= deadline:
                        break
                    if shutil.disk_usage(self.root).free < 512 * 1024**2:
                        job['error'] = 'Low storage: recording stopped early'
                        break
                    cameras = [c for c, failures_count in failures.items() if failures_count < 3]
                    if not cameras:
                        job['error'] = 'Recording stopped after three consecutive failures per camera'
                        break
                    futures = {pool.submit(self.shoot, job, c): c for c in cameras}
                    for future, camera in futures.items():
                        try:
                            future.result()
                            failures[camera] = 0
                        except Exception as exc:
                            failures[camera] += 1
                            job['errors'][str(camera)] = str(exc)
                    self.save(job)
                    due += config['interval']
                    now = time.monotonic()
                    if due < now:
                        skipped = math.ceil((now - due) / config['interval'])
                        job['missed'] += skipped
                        due += skipped * config['interval']
            job['recorded_seconds'] = round(time.monotonic() - began, 1)
            self.render(job)
        except Exception as exc:
            job.update(state='error', error=str(exc))
        finally:
            try:
                self.save(job)
            finally:
                with self.lock:
                    self.active = None
                    self.reservation.release()

    def rebuild(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('Unknown timelapse')
            if self.active or not self.reservation.acquire(blocking=False):
                raise ValueError('Wait for the current recording or photo')
            job = self.jobs[job_id]
            self.active = job_id
            def work():
                try:
                    self.render(job)
                except Exception as exc:
                    job.update(state='error', error=str(exc))
                finally:
                    try:
                        self.save(job)
                    finally:
                        with self.lock:
                            self.active = None
                            self.reservation.release()
            threading.Thread(target=work, daemon=True).start()
            return {'message': 'Building videos from retained photos'}

    def render(self, job):
        job['state'] = 'processing'
        self.save(job)
        videos = []
        for camera in job['settings']['cameras']:
            folder = self.root / job['id'] / f'camera-{camera}'
            count = len(list(folder.glob('[0-9][0-9][0-9][0-9][0-9][0-9].jpg')))
            job['counts'][str(camera)] = count
            if not count:
                continue
            # H.264 video <=4K; full-size original photos remain untouched.
            width, height = (1920, 1080) if job['settings']['resolution'] == '1920x1080' else (3840, 2160)
            video = folder / 'video.mp4'
            temp = folder / 'video.part.mp4'
            cmd = ['ffmpeg', '-nostdin', '-y', '-loglevel', 'error', '-threads', '2', '-filter_threads', '1',
                   '-framerate', str(job['settings']['fps']), '-i', str(folder / '%06d.jpg'),
                   '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
                   '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(temp)]
            with (folder / 'encode.log').open('w') as log:
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log, timeout=3600)
            if result.returncode:
                raise RuntimeError(f'Camera {camera+1}: video encoding failed; original photos retained')
            temp.replace(video)
            videos.append({'camera': camera, 'url': f'/timelapses/{job["id"]}/camera-{camera}/video.mp4',
                           'frames': count, 'seconds': round(count / job['settings']['fps'], 2), 'width': width, 'height': height})
            job['videos'] = list(videos)
            self.save(job)
        job['videos'] = videos
        job['state'] = 'complete' if videos else 'error'
        if not videos:
            job['error'] = job['error'] or 'No photos captured; check focus, lighting, and connections'

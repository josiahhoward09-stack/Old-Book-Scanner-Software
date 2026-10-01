"""No hardware required: python3 -m unittest discover -s tests."""
import importlib.util
import inspect
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'camera_service/camera_engine.py'
spec = importlib.util.spec_from_file_location('camera_engine', SOURCE)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class Frame:
    def __init__(self):
        self.refs = 0

    def acquire(self):
        self.refs += 1

    def release(self):
        self.refs -= 1


class CameraEngineTests(unittest.TestCase):
    def test_autofocus_off_accepts_unfocused_but_fresh_frame(self):
        gate = engine.FrameGate()
        frame = Frame()
        gate.arm(100, require_focus=False)
        gate.offer(frame, {'AfState': 0, 'SensorTimestamp': 99})
        self.assertEqual(frame.refs, 0)
        gate.offer(frame, {'AfState': 0, 'SensorTimestamp': 101})
        self.assertIs(gate.take(0), frame)
        frame.release()

    def test_camera_constructor_accepts_selected_index(self):
        inspect.signature(engine.CameraEngine).bind(1)

    def test_camera_bank_never_falls_back_to_other_camera(self):
        bank = engine.CameraBank.__new__(engine.CameraBank)
        first = types.SimpleNamespace(status=lambda: {'streaming': True})
        bank.engines = {0: first}
        bank.errors = {}
        bank.info = [{'Num': 0, 'Model': 'ov64a40', 'Id': 'physical-path'}]
        self.assertIs(bank.get(0), first)
        with self.assertRaisesRegex(ValueError, 'Camera 2 not detected'):
            bank.get(1)
        status = bank.status()
        self.assertTrue(status['streaming'])
        self.assertEqual(status['cameras'][0]['device'], 'physical-path')

    def test_camera_bank_reports_independent_errors(self):
        bank = engine.CameraBank.__new__(engine.CameraBank)
        bank.engines = {}
        bank.errors = {1: 'Camera busy'}
        bank.info = [{'Num': 1, 'Model': 'ov64a40'}]
        self.assertEqual(bank.status()['cameras'][0]['error'], 'Camera busy')
        with self.assertRaisesRegex(ValueError, 'Camera busy'):
            bank.get(1)

    def test_only_focused_post_shutter_frame_is_accepted(self):
        gate = engine.FrameGate()
        frame = Frame()
        gate.arm(100)
        gate.offer(frame, {'AfState': 2, 'SensorTimestamp': 99})
        gate.offer(frame, {'AfState': 1, 'SensorTimestamp': 101})
        gate.offer(frame, {'AfState': 3, 'SensorTimestamp': 102})
        self.assertEqual(frame.refs, 0)
        gate.offer(frame, {'AfState': 2, 'SensorTimestamp': 103})
        gate.offer(frame, {'AfState': 2, 'SensorTimestamp': 104})
        self.assertIs(gate.take(0), frame)
        self.assertEqual(frame.refs, 1)
        frame.release()

    def test_timed_out_capture_does_not_leak_future_frames(self):
        gate = engine.FrameGate()
        frame = Frame()
        gate.arm(100)
        with self.assertRaises(TimeoutError):
            gate.take(0)
        gate.offer(frame, {'AfState': 2, 'SensorTimestamp': 101})
        self.assertEqual(frame.refs, 0)
        gate.arm(200)
        gate.offer(frame, {'AfState': 2, 'SensorTimestamp': 201})
        self.assertIs(gate.take(0), frame)
        frame.release()

    def test_scan_timeout_releases_lock_without_restarting_focus(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        camera.lock = threading.Lock()
        camera.capture_lock = threading.Lock()
        camera.metadata = {'AfState': 1}
        camera.scan_pending = False
        camera.focus_mode = 'hold'
        camera.phase = 'ready'
        controls_set = []
        camera.camera = types.SimpleNamespace(set_controls=controls_set.append)
        camera.controls = types.SimpleNamespace(AfModeEnum=types.SimpleNamespace(Continuous=2))
        def timeout(_):
            raise TimeoutError('test')
        camera.gate = types.SimpleNamespace(arm=lambda _: None, take=timeout)
        with patch.object(engine.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10**10)):
            with self.assertRaises(TimeoutError):
                camera.capture('9248x6944')
        self.assertFalse(camera.capture_lock.locked())
        self.assertEqual(camera.phase, 'ready')
        self.assertEqual(controls_set, [])

    def test_repeated_hold_requests_do_not_retrigger_autofocus(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        camera.lock = threading.Lock()
        camera.focus_mode = 'hold'
        calls = []
        camera.camera = types.SimpleNamespace(set_controls=calls.append)
        for _ in range(100):
            camera._set_focus('hold')
        self.assertEqual(calls, [])

    def test_refocus_rejects_stale_focused_frames_until_scan_completes(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        camera.lock = threading.Lock()
        camera.focus_mode = 'hold'
        camera.scan_pending = False
        camera.scan_started = False
        camera.frame_at = engine.time.monotonic() + 10
        calls = []
        camera.camera = types.SimpleNamespace(set_controls=calls.append)
        camera.controls = types.SimpleNamespace(AfModeEnum=types.SimpleNamespace(Auto=1), AfTriggerEnum=types.SimpleNamespace(Start=0))
        camera.gate = engine.FrameGate()
        camera._set_focus('hold', refocus=True)
        camera.gate.arm(0)
        frame = Frame()
        frame.get_metadata = lambda: {'AfState': 2, 'SensorTimestamp': 1}
        camera.on_frame(frame)
        self.assertEqual(frame.refs, 0)
        frame.get_metadata = lambda: {'AfState': 1, 'SensorTimestamp': 2}
        camera.on_frame(frame)
        frame.get_metadata = lambda: {'AfState': 2, 'SensorTimestamp': 3}
        camera.on_frame(frame)
        self.assertIs(camera.gate.take(0), frame)
        self.assertEqual(calls, [{'AfMode': 1, 'AfTrigger': 0}])
        frame.release()

    def test_invalid_resolution_rejected_before_touching_camera(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        with self.assertRaises(ValueError):
            camera.capture('../../other-file')

    def test_overlapping_capture_is_rejected_without_queueing(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        camera.capture_lock = threading.Lock()
        camera.capture_lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'already taking'):
                camera.capture('9248x6944')
        finally:
            camera.capture_lock.release()

    def test_save_failure_releases_sensor_buffer_and_capture_lock(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        camera.lock = threading.Lock()
        camera.capture_lock = threading.Lock()
        camera.metadata = {'AfState': 2}
        camera.scan_pending = False
        camera.focus_mode = 'hold'
        camera.phase = 'ready'
        frame = Frame()
        frame.acquire()
        frame.get_metadata = lambda: {'AfState': 2, 'LensPosition': 4}
        def failed_save(*args, **kwargs):
            raise OSError('test disk failure')
        frame.save = failed_save
        camera.gate = types.SimpleNamespace(arm=lambda _: None, take=lambda _: frame)
        with patch.object(engine.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=10**10)):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                camera.capture('9248x6944')
        self.assertEqual(frame.refs, 0)
        self.assertFalse(camera.capture_lock.locked())
        self.assertEqual(camera.phase, 'ready')

    def test_low_disk_space_fails_without_starting_a_capture(self):
        camera = engine.CameraEngine.__new__(engine.CameraEngine)
        camera.lock = threading.Lock()
        camera.capture_lock = threading.Lock()
        with patch.object(engine.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0)):
            with self.assertRaisesRegex(RuntimeError, 'Storage nearly full'):
                camera.capture('9248x6944')
        self.assertFalse(camera.capture_lock.locked())


if __name__ == '__main__':
    unittest.main()

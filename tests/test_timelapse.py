import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from timelapse import Timelapse, settings
from camera_service.enable_second_camera import updated_config

class TimelapseTests(unittest.TestCase):
    def setUp(self):
        patcher=patch('timelapse.shutil.which',return_value='/usr/bin/ffmpeg')
        patcher.start();self.addCleanup(patcher.stop)

    def test_validation(self):
        for data in ({'interval':0},{'fps':float('nan')},{'duration':-1},{'cameras':[2]},{'resolution':'evil'},{'focus':'x'},{'duration':86400,'interval':1}):
            with self.assertRaises(ValueError):settings(data)
        self.assertEqual(settings({'cameras':[0,1]})['cameras'],[0,1])

    def test_boot_config_idempotence_and_guard(self):
        original='camera_auto_detect=0\n[all]\ndtoverlay=ov64a40,cam0,link-frequency=360000000\n'
        result=updated_config(original)
        self.assertEqual(updated_config(result),result)
        self.assertIn('dtoverlay=ov64a40,link-frequency=360000000',result)
        with self.assertRaises(ValueError):updated_config('camera_auto_detect=1\n')

    def test_both_shoot_concurrently_stop_and_release(self):
        with tempfile.TemporaryDirectory() as folder:
            barrier=threading.Barrier(2); seen=[]; reservation=threading.Lock()
            def capture(camera,config,count,target):
                barrier.wait(timeout=2);seen.append(camera);target.write_bytes(b'jpeg')
            manager=Timelapse(folder,capture,lambda:{'cameras':[{'camera':c,'streaming':True} for c in [0,1]]},reservation)
            with patch.object(manager,'render',side_effect=lambda job:job.update(state='complete')):
                job=manager.start({'cameras':[0,1],'duration':1,'interval':1})
                for _ in range(100):
                    if not manager.active:break
                    time.sleep(.02)
            self.assertCountEqual(seen,[0,1]);self.assertFalse(reservation.locked())
            self.assertEqual(manager.jobs[job['id']]['counts'],{'0':1,'1':1})

    def test_missing_camera_fails_before_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=Timelapse(folder,None,lambda:{'cameras':[]},threading.Lock())
            with self.assertRaisesRegex(ValueError,'Camera 2'):manager.start({'cameras':[1]})
            self.assertIsNone(manager.active)

    def test_stop_interrupts_wait_and_builds_video(self):
        with tempfile.TemporaryDirectory() as folder:
            shot=threading.Event();reservation=threading.Lock()
            def capture(camera,config,count,target):target.write_bytes(b'jpeg');shot.set()
            manager=Timelapse(folder,capture,lambda:{'cameras':[{'camera':0,'streaming':True}]},reservation)
            with patch.object(manager,'render',side_effect=lambda job:job.update(state='complete')):
                job=manager.start({'duration':100,'interval':60})
                self.assertTrue(shot.wait(2));manager.stop()
                for _ in range(100):
                    if not manager.active:break
                    time.sleep(.01)
            self.assertIsNone(manager.active);self.assertFalse(reservation.locked())
            self.assertEqual(manager.jobs[job['id']]['state'],'complete')

    def test_encoder_failure_retains_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=Timelapse(folder,None,None,threading.Lock())
            job={'id':'b'*32,'settings':settings({}),'counts':{'0':1},'videos':[],'error':''}
            target=Path(folder)/job['id']/'camera-0';target.mkdir(parents=True);frame=target/'000000.jpg';frame.write_bytes(b'jpeg')
            with patch('timelapse.subprocess.run',return_value=type('Result',(),{'returncode':1})()):
                with self.assertRaisesRegex(RuntimeError,'original photos retained'):manager.render(job)
            self.assertTrue(frame.is_file())

    def test_restart_marks_interrupted_keeps_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/('a'*32);path.mkdir();(path/'job.json').write_text(json.dumps({'id':'a'*32,'state':'recording','started':0}))
            manager=Timelapse(folder,None,None,threading.Lock())
            self.assertEqual(manager.status()['jobs'][0]['state'],'interrupted')

if __name__=='__main__':unittest.main()

#!/usr/bin/python3
"""Run once with sudo on this Pi, after confirming two OV64A40 cameras."""
import datetime
import os
from pathlib import Path
import shutil

def updated_config(text):
    lines = [line.strip() for line in text.splitlines() if not line.lstrip().startswith('#')]
    cameras = [line for line in lines if line.startswith('dtoverlay=ov64a40')]
    desired = 'dtoverlay=ov64a40,link-frequency=360000000'
    if desired in cameras:
        return text
    if cameras != ['dtoverlay=ov64a40,cam0,link-frequency=360000000'] or 'camera_auto_detect=0' not in lines:
        raise ValueError('Camera configuration has changed; stop and have it reviewed before applying this setup.')
    return text.rstrip() + '\n\n[all]\n# Second OwlSight on CAM/DISP1; CAM/DISP0 configuration retained.\n' + desired + '\n'

if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run this script with sudo on the Pi.')
    path = Path('/boot/firmware/config.txt')
    original = path.read_text()
    updated = updated_config(original)
    if updated == original:
        print('Both connector overlays are already configured.')
    else:
        backup = path.with_name('config.txt.before-dual-camera-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(path, backup)
        temp = path.with_name('config.txt.dual-camera.tmp')
        temp.write_text(updated)
        temp.replace(path)
        print('Saved camera configuration. Backup:', backup)
    print('Reboot the Pi to activate the second camera connector. Do not unplug camera cables while powered.')

BOOK SCANNER UNIVERSAL NODE CONTROLLER V6

WHY V6
------
V5 failed because the external LiteGraph URL returned HTTP 404.
V6 removes that failure mode completely.

V6 IS SELF-CONTAINED
--------------------
- No LiteGraph CDN
- No xterm CDN
- No browser JavaScript/CSS dependencies
- No new pip packages
- Uses Python's standard library for the web server and WebSocket terminal
- Binds only to the dedicated Lenovo-to-Pi Ethernet address: 10.42.0.2

CURRENT HARDWARE DEFAULT
------------------------
DS18B20 thermometer DATA = GP26

NODE EDITOR
-----------
- Shift+A opens Add Node
- Inputs are on the left
- Outputs are on the right
- Click an output, then an input, to connect them
- Drag node headers to move nodes
- GPIO numbers are editable in the nodes
- Duplicate GPIO assignments are flagged
- Save Graph / Load Graph use the Lenovo browser's local storage

NODES INCLUDED
--------------
Flow:
- Start Sequence
- Delay

Values:
- Number
- Boolean

Sensors / GPIO:
- DS18B20 Temperature
- GPIO Digital Input
- GPIO Digital Output
- GPIO PWM Output

Logic:
- If / Compare
- AND
- OR
- NOT

Camera / Pi:
- OwlSight Camera
- Pi API Action

PI TERMINAL
-----------
The Pi Terminal button opens a Bash shell on the Pi as copycatbrain.
It is intentionally exposed only on 10.42.0.2.

The built-in terminal is meant for normal shell commands. Because V6 has no
external terminal-emulator JavaScript library, full-screen applications such
as nano are not ideal inside the browser terminal. Use the built-in Pi Files
editor for editing files, or normal SSH when a full terminal emulator is needed.

PI FILES
--------
The Pi Files button provides a text editor for files under:
/home/copycatbrain

This makes it easy to load, edit, and save index.html, server.py, scripts, etc.
The editor cannot write outside /home/copycatbrain.

INSTALL
-------
From the Lenovo, unzip and copy the folder to the Pi:

  cd ~/Downloads
  unzip bookscanner_universal_nodes_v6.zip
  scp -r bookscanner_universal_nodes_v6 copycatbrain@10.42.0.2:/home/copycatbrain/

Then SSH to the Pi:

  ssh copycatbrain@10.42.0.2

Then:

  cd ~/bookscanner_universal_nodes_v6
  chmod +x install.sh
  sudo ./install.sh

Open on Lenovo:
  http://10.42.0.2:8093

CAMERA NOTE
-----------
The OwlSight live frame endpoint is still intentionally disabled until the Pi
OS reliably detects the OV64A40 camera. Still capture will work only after the
camera is detected by rpicam.

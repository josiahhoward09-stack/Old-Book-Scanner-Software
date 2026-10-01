#!/usr/bin/env python3
from flask import Flask, jsonify, send_from_directory, request, Response
import subprocess, os, time, socket, json, urllib.request, shutil, datetime, threading

APP="/home/copycatbrain/pi-litegraph"
CAP=os.path.join(APP,"captures")
os.makedirs(CAP,exist_ok=True)
app=Flask(__name__)
latest_frame=None
camera_process=None
camera_error=""
camera_lock=threading.Lock()

def run(cmd,timeout=10):
    return subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)

def get_temp():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/temperature",timeout=3) as r:
            return json.loads(r.read().decode())
    except:
        return {"celsius":None,"fahrenheit":None}

# Camera ownership belongs to bookscanner-pi-control.service on port 8090.

@app.route("/")
def home(): return send_from_directory(APP,"index.html")

@app.route("/api/status")
def status(): return jsonify(hostname=socket.gethostname(),pi=True)

@app.route("/api/temperature")
def temperature(): return jsonify(get_temp())

@app.route("/camera/frame.jpg")
def frame():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/camera/frame.jpg",timeout=5) as r:
            return Response(r.read(),mimetype="image/jpeg",headers={"Cache-Control":"no-store"})
    except Exception:
        return ("Camera preview is temporarily unavailable",503,{"Content-Type":"text/plain"})

@app.route("/api/camera/capture",methods=["POST"])
def capture():
    global camera_process
    fn="capture_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")+".jpg"
    path=os.path.join(CAP,fn)
    try:
        req=urllib.request.Request("http://127.0.0.1:8090/api/camera/capture",data=b"{}",headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=55) as r:result=json.loads(r.read())
        photo_url=result["url"]
        if not photo_url.startswith("/captures/") or ".." in photo_url:raise ValueError("Invalid capture URL")
        with urllib.request.urlopen("http://127.0.0.1:8090"+photo_url,timeout=15) as r:photo=r.read()
        with open(path,"wb") as f:f.write(photo)
        return jsonify(filename=fn,url="/captures/"+fn)
    except Exception as e:
        return jsonify(error=str(e)),500

@app.route("/captures/<path:name>")
def capture_file(name): return send_from_directory(CAP,name)

@app.route("/api/service/<path:name>/restart",methods=["POST"])
def restart_service(name):
    if name not in {"bookscanner-temperature.service","ssh.service"}:
        return jsonify(error="Service not allowlisted"),403
    p=run(["sudo","systemctl","restart",name],10)
    if p.returncode!=0: return jsonify(error=p.stderr or "restart failed"),500
    return jsonify(message="Restarted "+name)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=8092,threaded=True)

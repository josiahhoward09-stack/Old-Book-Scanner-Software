#!/usr/bin/env python3
from flask import Flask,jsonify,send_from_directory,Response,request
import subprocess,os,time,socket,shutil,re,json,urllib.request,threading,http.client
app=Flask(__name__)
BASE="/home/copycatbrain/pi-control"
CAP=os.path.join(BASE,"captures");os.makedirs(CAP,exist_ok=True)
FLOW=os.path.join(BASE,"flow.json")
flow={"running":False,"last_message":"Stopped"}
prev={}
camera_error="Camera warming up"
camera_proc=None
camera_lock=threading.Lock()
ENGINE_SOCKET=os.path.join(BASE,"camera-engine.sock")

def run(c,t=8):return subprocess.run(c,capture_output=True,text=True,timeout=t)
def ip4(i):
    m=re.search(r"(\d+\.\d+\.\d+\.\d+/\d+)",run(["ip","-4","-br","addr","show",i]).stdout);return m.group(1) if m else None
def up(i):return " UP " in (" "+run(["ip","-br","link","show",i]).stdout+" ")
def cpu_temp():
    try:return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000
    except:return None
def cpu_percent():
    def s():
        v=list(map(int,open("/proc/stat").readline().split()[1:]));return v[3]+v[4],sum(v)
    a,b=s();time.sleep(.1);c,d=s();return 100*(1-(c-a)/max(1,d-b))
def mem():
    x={}
    for l in open("/proc/meminfo"):
        k,v=l.split(":",1);x[k]=int(v.strip().split()[0])
    return {"percent":100*(x["MemTotal"]-x["MemAvailable"])/x["MemTotal"]}
def uptime():
    sec=int(float(open("/proc/uptime").read().split()[0]));d,sec=divmod(sec,86400);h,sec=divmod(sec,3600);m,_=divmod(sec,60);return f"{d}d {h}h" if d else f"{h}h {m}m"
def temperature():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/temperature",timeout=3) as r:
            x=json.loads(r.read());return {"celsius":float(x["celsius"]),"fahrenheit":float(x["fahrenheit"])}
    except:return None

def camera_diagnostics():
    return "Independent persistent OwlSight cameras; focus hold and automatic stalled-stream recovery"

class EngineConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(ENGINE_SOCKET)

def engine_request(path,payload=None):
    connection=EngineConnection("localhost",timeout=50 if payload is not None else 3)
    try:
        connection.request("POST" if payload is not None else "GET",path,
                           body=json.dumps(payload) if payload is not None else None,
                           headers={"Content-Type":"application/json"})
        response=connection.getresponse();body=response.read()
        if response.status!=200:
            raise RuntimeError(json.loads(body).get("error","Camera request failed"))
        return body
    finally:connection.close()

def camera_worker():
    global camera_error,camera_proc
    while True:
        try:
            with camera_lock:
                camera_error="Camera warming up"
                camera_proc=subprocess.Popen(["/usr/bin/python3","-u",os.path.join(BASE,"camera_engine.py")])
                proc=camera_proc
            proc.wait()
            camera_error="Camera engine restarting; see camera service journal for details"
        except Exception as e:camera_error=str(e)
        time.sleep(2)

def restart_camera_worker():
    global camera_proc
    with camera_lock:
        if camera_proc and camera_proc.poll() is None:camera_proc.terminate()

threading.Thread(target=camera_worker,daemon=True).start()

def capture(resolution="9248x6944"):
    return json.loads(engine_request("/capture",{"resolution":resolution}))["filename"]

@app.get("/")
def home():return send_from_directory(BASE,"index.html")
@app.get("/captures/<path:name>")
def capfile(name):return send_from_directory(CAP,name)
@app.get("/api/status")
def status():return jsonify(hostname=socket.gethostname(),uptime_human=uptime(),cpu={"percent":cpu_percent(),"temp_c":cpu_temp()},memory=mem(),network={"eth0":{"up":up("eth0"),"ip":ip4("eth0")},"wlan0":{"up":up("wlan0"),"ip":ip4("wlan0")}},pico={"present":os.path.exists("/dev/ttyACM0")},temperature=temperature())
@app.get("/api/logs/temperature")
def logs():return jsonify(text=run(["journalctl","-u","bookscanner-temperature.service","-n","80","--no-pager"]).stdout.strip())
@app.post("/api/service/<path:name>/restart")
def restart(name):
    if name not in {"bookscanner-temperature.service","ssh.service"}:return jsonify(error="Not allowlisted"),403
    p=run(["sudo","systemctl","restart",name],10)
    return jsonify(message="Restarted "+name) if p.returncode==0 else (jsonify(error=p.stderr or "restart failed"),500)
@app.post("/api/pico/reset")
def pico():
    if not os.path.exists("/dev/ttyACM0"):return jsonify(error="Pico not connected"),503
    try:
        run(["sudo","systemctl","stop","bookscanner-temperature.service"],8)
        p=run(["/home/copycatbrain/scanner-venv/bin/mpremote","connect","/dev/ttyACM0","reset"],8);time.sleep(2)
        run(["sudo","systemctl","start","bookscanner-temperature.service"],8)
        return jsonify(message="Pico reset") if p.returncode==0 else (jsonify(error=p.stderr or "reset failed"),500)
    except Exception as e:return jsonify(error=str(e)),500
@app.post("/api/system/reboot")
def reboot():subprocess.Popen(["sudo","systemctl","reboot"]);return jsonify(message="Reboot requested")
@app.post("/api/system/shutdown")
def shutdown():subprocess.Popen(["sudo","systemctl","poweroff"]);return jsonify(message="Shutdown requested")

@app.get("/api/camera/status")
def camstatus():
    try:return jsonify(**json.loads(engine_request("/status")),diagnostics=camera_diagnostics())
    except Exception as e:return jsonify(streaming=False,focus="starting",phase="starting",error=str(e)),503
@app.get("/camera/frame.jpg")
def frame():
    try:return Response(engine_request("/frame?camera="+str(int(request.args.get("camera",0)))),mimetype="image/jpeg",headers={"Cache-Control":"no-store"})
    except Exception as e:return (str(e),503,{"Content-Type":"text/plain"})
@app.post("/api/camera/restart")
def camrestart():restart_camera_worker();return jsonify(message="Camera worker restarting")
@app.post("/api/camera/capture")
def camcapture():
    try:
        data=request.get_json(silent=True) or {}
        return jsonify(**json.loads(engine_request("/capture",{"camera":int(data.get("camera",0)),"resolution":data.get("resolution","9248x6944"),"focus_mode":data.get("focus_mode","hold")})))
    except Exception as e:return jsonify(error=str(e)),500

@app.post("/api/camera/focus")
def camfocus():
    try:
        data=request.get_json(silent=True) or {}
        return jsonify(**json.loads(engine_request("/focus",{"camera":int(data.get("camera",0)),"focus_mode":data.get("focus_mode","hold"),"refocus":bool(data.get("refocus"))})))
    except Exception as e:return jsonify(error=str(e)),409

@app.post("/api/flow/save")
def flowsave():
    data=request.get_json(force=True)
    with open(FLOW,"w") as f:json.dump(data,f,indent=2)
    return jsonify(message="Flow saved")
@app.get("/api/flow/load")
def flowload():
    if not os.path.exists(FLOW):return jsonify(nodes=[],links=[])
    return jsonify(json.load(open(FLOW)))
@app.get("/api/flow/status")
def flowstatus():return jsonify(**flow)
def comp(a,b,o):return {">=":a>=b,">":a>b,"<=":a<=b,"<":a<b,"==":a==b,"!=":a!=b}.get(o,False)
def getin(n,s,nodes,links,cache,stack):
    l=next((x for x in links if x.get("to",{}).get("node")==n["id"] and x.get("to",{}).get("socket")==s),None)
    if not l:return None
    return evalout(nodes[l["from"]["node"]],l["from"]["socket"],nodes,links,cache,stack)
def evalout(n,s,nodes,links,cache,stack):
    k=n["id"]+":"+s
    if k in cache:return cache[k]
    if k in stack:return None
    stack.add(k);t=n["type"];v=None
    if t=="temperature":
        x=temperature();v=(x["fahrenheit"] if s=="f" else x["celsius"]) if x else None
    elif t=="number":v=float(n.get("data",{}).get("value",0))
    elif t=="compare":
        a=getin(n,"a",nodes,links,cache,stack);b=getin(n,"b",nodes,links,cache,stack)
        v=False if a is None or b is None else comp(float(a),float(b),n.get("data",{}).get("op",">="))
    elif t=="logic":
        a=bool(getin(n,"a",nodes,links,cache,stack));b=bool(getin(n,"b",nodes,links,cache,stack));o=n.get("data",{}).get("op","AND");v=(a and b) if o=="AND" else ((a or b) if o=="OR" else (a!=b))
    elif t=="reroute":v=getin(n,"in",nodes,links,cache,stack)
    elif t=="camera":
        if s=="active":v=bool(getin(n,"trigger",nodes,links,cache,stack))
        elif s=="image":v="/camera/frame.jpg"
    stack.discard(k);cache[k]=v;return v
def loop():
    global prev
    while flow["running"]:
        try:
            if not os.path.exists(FLOW):flow["last_message"]="No saved flow";time.sleep(.5);continue
            f=json.load(open(FLOW));nodes={n["id"]:n for n in f.get("nodes",[])};links=f.get("links",[]);cache={};msgs=[]
            for n in nodes.values():
                if n["type"]=="camera":
                    trig=bool(getin(n,"trigger",nodes,links,cache,set()));was=prev.get(n["id"],False);mode=n.get("data",{}).get("mode","photo")
                    if trig and not was and mode=="photo":
                        try:msgs.append("Captured "+capture())
                        except Exception as e:msgs.append("Capture error "+str(e))
                    prev[n["id"]]=trig
            tt=temperature();flow["last_message"]=((f"{tt['fahrenheit']:.1f}°F • " if tt else "No temp • ")+(msgs[-1] if msgs else "Monitoring"))
        except Exception as e:flow["last_message"]="Flow error: "+str(e)
        time.sleep(.5)
@app.post("/api/flow/start")
def flowstart():
    if flow["running"]:return jsonify(message="Flow already running")
    flow["running"]=True;threading.Thread(target=loop,daemon=True).start();return jsonify(message="Flow started")
@app.post("/api/flow/stop")
def flowstop():flow["running"]=False;flow["last_message"]="Stopped";return jsonify(message="Flow stopped")

if __name__=="__main__":app.run(host="0.0.0.0",port=8090,threaded=True)

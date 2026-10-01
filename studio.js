"use strict";
const photoSizes={"9248x6944":"64 MP · Maximum · 9248 × 6944", "3840x2160":"4K · 3840 × 2160", "1920x1080":"1080p · 1920 × 1080", instant:"Preview snapshot · 1280 × 720"};
let studioCaptureBusy=false, noticeTimer=null, graphEpoch=0;
let captureTail=Promise.resolve(), capturePending=0, captureQueueEpoch=0;
const CAPTURE_QUEUE_LIMIT=8;
function updateCaptureQueue(){
  document.querySelectorAll(".captureButton").forEach(b=>{b.disabled=capturePending>=CAPTURE_QUEUE_LIMIT;b.textContent=capturePending?`＋ Take another · ${capturePending} pending`:"◎ Take Picture"});
  document.querySelectorAll(".focusControl").forEach(b=>b.disabled=capturePending>0);
}
let cameraStatusPolling=false;
function selectedCameras(value){return value==="both"?[0,1]:[Number(value??0)]}
async function focusCameras(selection,payload){
  for(const camera of selectedCameras(selection))await API.post("/api/camera/focus",{...payload,camera});
}
async function refreshCameraReadiness(){
  if(cameraStatusPolling||!linked||document.hidden)return;
  cameraStatusPolling=true;
  try{
    const status=await API.get("/api/camera/status");
    const cameras=status.cameras||[{...status,camera:0}];
    document.querySelectorAll("[data-camera-ready]").forEach(el=>{
      el.textContent=selectedCameras(el.dataset.cameraReady).map(id=>{
        const c=cameras.find(c=>c.camera===id);
        return `Camera ${id+1}: `+(!c?"Not detected · check connector/configuration":c.error?c.error:!c.streaming?"No live frames · reconnect or check ribbon":c.phase==="saving"?"Captured · saving…":c.focus==="focused"?"Ready · focus locked":c.focus==="focusing"?"Autofocusing…":"Live · waiting for focus");
      }).join("\n");el.style.whiteSpace="pre-line";
    });
  }catch(e){document.querySelectorAll("[data-camera-ready]").forEach(el=>el.textContent="Camera unavailable · reconnecting…")}
  finally{cameraStatusPolling=false}
}
const nodeActivityTimers=new Map(), activeNodeIds=new Set(), firingLinks=new Map();
function activity(id,state,message){
  const node=document.querySelector(`.node[data-id="${id}"]`), label=document.getElementById(`activity-${id}`);
  clearTimeout(nodeActivityTimers.get(id));
  if(state==="active")activeNodeIds.add(id);else activeNodeIds.delete(id);
  if(node)node.dataset.state=state;
  if(label){label.textContent=(state==="active"?"● ":state==="done"?"✓ ":state==="error"?"! ":"")+message;label.title=message}
  const active=[...activeNodeIds].map(x=>nodeById(x)).filter(Boolean).map(n=>defs[n.type].title);
  document.getElementById("activitySummary").textContent=active.length?"Active: "+active.join(" · "):message;
}
function pulseNode(id,message){
  if(activeNodeIds.has(id))return;
  activity(id,"active",message);
  nodeActivityTimers.set(id,setTimeout(()=>activity(id,"done",message),350));
}
function stopGraph(){
  running=false;graphEpoch++;
  captureQueueEpoch++; // Do not start queued photos after Stop; current exposure may finish.
  for(const timer of nodeActivityTimers.values())clearTimeout(timer);
  nodeActivityTimers.clear();
  for(const id of [...activeNodeIds])activity(id,"idle","Stopped");
  firingLinks.clear();drawWires();
  document.getElementById("runState").textContent="■ Stopped";
  document.getElementById("activitySummary").textContent="Sequence stopped • manual camera controls are available";
}
function fitGraph(){
  if(!nodes.length)return;
  const minX=Math.min(...nodes.map(n=>n.x)),minY=Math.min(...nodes.map(n=>n.y));
  const maxX=Math.max(...nodes.map(n=>n.x+258)),maxY=Math.max(...nodes.map(n=>n.y+(document.querySelector(`.node[data-id="${n.id}"]`)?.offsetHeight||200)));
  zoom=Math.max(.25,Math.min(1,(workspace.clientWidth-90)/(maxX-minX),(workspace.clientHeight-90)/(maxY-minY)));
  panX=(workspace.clientWidth-(maxX-minX)*zoom)/2-minX*zoom;panY=(workspace.clientHeight-(maxY-minY)*zoom)/2-minY*zoom;applyPan();
}
function captureNotice(state,title,detail,photo=null){
  clearTimeout(noticeTimer);
  const box=document.getElementById("captureNotice");box.hidden=false;box.dataset.state=state;box.replaceChildren();
  const strong=document.createElement("strong"),small=document.createElement("small");strong.textContent=title;small.textContent=detail;box.append(strong,small);
  if(photo){const button=document.createElement("button");button.textContent="View photo ↗";button.onclick=()=>openPhoto(photo);box.append(document.createElement("br"),button)}
  if(state!=="active")noticeTimer=setTimeout(()=>box.hidden=true,6000);
}
function captureCamera(n){
  if(capturePending>=CAPTURE_QUEUE_LIMIT){toast("Shot queue is full; wait for a photo to finish");return Promise.reject(new Error("Shot queue is full"))}
  const epoch=captureQueueEpoch,snapshot={...n,props:{...n.props}};
  capturePending++;updateCaptureQueue();
  const job=captureTail.then(()=>{
    if(epoch!==captureQueueEpoch)throw new Error("Queued photo cancelled");
    return performCameraCapture(snapshot);
  });
  captureTail=job.catch(()=>{if(epoch===captureQueueEpoch)captureQueueEpoch++});
  return job.finally(()=>{capturePending--;updateCaptureQueue()});
}
async function performCameraCapture(n){
  if(!linked){toast("Connect to the Pi before taking a photo");throw new Error("Pi not connected")}
  studioCaptureBusy=true;
  const resolution=n.props.resolution||"9248x6944", started=performance.now();
  activity(n.id,"active",resolution==="instant"?"Saving current frame…":"Autofocus → capture → save…");
  captureNotice("active",resolution==="instant"?"◌ Taking your picture":"◌ Focus → capture → save",resolution==="instant"?"Saving the live preview frame":photoSizes[resolution]+" · camera already running; keep the book still.");
  try{
    const photos=[],failures=[];
    for(const camera of selectedCameras(n.props.camera)){
      activity(n.id,"active",`Camera ${camera+1} · focus → capture → save…`);
      try{photos.push(await API.post("/api/camera/action",{camera,mode:"Take Picture",resolution,focus_mode:n.props.focusMode||"hold"}))}
      catch(e){failures.push(`Camera ${camera+1}: ${e.message}`)}
    }
    if(failures.length){if(photos.length){setValue(n.id,"Image URL",photos[0].url);loadGallery()}throw new Error(`${photos.length} photo(s) saved. ${failures.join("; ")}`)}
    const d=photos[0];
    setValue(n.id,"Image URL",d.url);
    const timing=((performance.now()-started)/1000).toFixed(2);
    activity(n.id,"done",`${photos.length} photo(s) saved · ${d.width} × ${d.height}`);
    const shutter=d.shutter_ms==null?"":` · captured in ${(d.shutter_ms/1000).toFixed(2)}s`;
    captureNotice("done",`✓ ${photos.length} photo(s) saved`,`${d.width} × ${d.height}${shutter} · saved in ${timing}s · Camera Gallery`,{name:d.filename,url:d.url});
    if(document.getElementById("galleryPanel").style.display==="block")loadGallery();
    return d;
  }catch(e){activity(n.id,"error",e.message);captureNotice("error","Picture could not be taken",e.message);throw e}
  finally{
    studioCaptureBusy=false;
  }
}

let galleryItems=[],galleryTrashMode=false,galleryRequest=0;
async function loadGallery(){
  const request=++galleryRequest, grid=document.getElementById("galleryGrid");
  grid.textContent="Loading photos…";
  try{
    const d=await API.get(`/api/captures?trash=${galleryTrashMode?1:0}`);
    if(request!==galleryRequest)return;
    galleryItems=d.captures;grid.replaceChildren();
    document.getElementById("galleryCount").textContent=`${galleryItems.length} ${galleryTrashMode?"photos in Trash • restore any time":"photos • click to zoom and inspect"}`;
    document.getElementById("galleryTrash").textContent=galleryTrashMode?"← All photos":"Trash";
    if(!galleryItems.length){const empty=document.createElement("div");empty.className="emptyGallery";const title=document.createElement("strong");title.textContent=galleryTrashMode?"Trash is empty":"Your next scan starts here";empty.append(title,document.createTextNode(galleryTrashMode?"Deleted photos can be restored from here.":"Take a picture from a camera node and it will appear here."));grid.append(empty);return}
    for(const item of galleryItems){
      const card=document.createElement("article");card.className="galleryItem";
      const open=document.createElement("button");open.className="photoOpen";open.setAttribute("aria-label","Open photo "+item.name);open.onclick=()=>openPhoto(item);
      const im=document.createElement("img");im.src=item.url;im.alt="Camera capture "+item.name;im.loading="lazy";im.decoding="async";open.append(im);
      const details=document.createElement("div");details.className="photoDetails";
      const title=document.createElement("strong");title.textContent=new Date(item.taken*1000).toLocaleString(undefined,{month:"short",day:"numeric",hour:"numeric",minute:"2-digit",second:"2-digit"});
      const meta=document.createElement("small");meta.textContent=`${(item.bytes/1024/1024).toFixed(2)} MB · JPEG`;details.append(title,meta);
      const del=document.createElement("button");del.className="deletePhoto";del.textContent=galleryTrashMode?"↶ Restore":"Move to Trash";
      del.onclick=async()=>{del.disabled=true;try{await API.post(`/api/captures/${galleryTrashMode?"restore":"delete"}`,{name:item.name});toast(galleryTrashMode?"Photo restored":"Moved to Trash — you can restore it there");await loadGallery()}catch(e){toast(e.message);del.disabled=false}};
      details.append(del);card.append(open,details);grid.append(card);
    }
  }catch(e){if(request===galleryRequest)grid.textContent="Gallery unavailable: "+e.message}
}

let viewedPhoto=null,photoScale=1,photoX=0,photoY=0,photoDrag=null,photoReady=false,photoReturnFocus=null;
const photoDialog=document.getElementById("photoViewer"),photoStage=document.getElementById("photoStage"),photoImage=document.getElementById("photoFull");
function applyPhotoTransform(){photoImage.style.transform=`translate(${photoX}px,${photoY}px) scale(${photoScale})`;document.getElementById("photoZoom").textContent=Math.round(photoScale*100)+"%"}
function fitPhoto(){if(!photoReady)return;photoScale=Math.min((photoStage.clientWidth-32)/photoImage.naturalWidth,(photoStage.clientHeight-32)/photoImage.naturalHeight,1);photoX=(photoStage.clientWidth-photoImage.naturalWidth*photoScale)/2;photoY=(photoStage.clientHeight-photoImage.naturalHeight*photoScale)/2;applyPhotoTransform()}
function zoomPhoto(next,x=photoStage.clientWidth/2,y=photoStage.clientHeight/2){if(!photoReady)return;const scale=Math.max(.01,Math.min(8,next));photoX=x-(x-photoX)*scale/photoScale;photoY=y-(y-photoY)*scale/photoScale;photoScale=scale;applyPhotoTransform()}
function openPhoto(item){
  viewedPhoto=item;photoReady=false;photoImage.style.visibility="hidden";
  if(!photoDialog.open){photoReturnFocus=document.activeElement;photoDialog.showModal()}
  document.getElementById("photoTitle").textContent=item.name;
  document.getElementById("photoInfo").textContent="Loading full-resolution image…";
  const download=document.getElementById("photoDownload");download.href=item.url;download.download=item.name;
  photoImage.onload=()=>{photoReady=true;photoImage.style.visibility="visible";document.getElementById("photoInfo").textContent=`${photoImage.naturalWidth} × ${photoImage.naturalHeight} pixels · JPEG`;fitPhoto()};
  photoImage.onerror=()=>{document.getElementById("photoInfo").textContent="This image could not be loaded. Refresh the gallery and try again."};
  photoImage.src=item.url;
  const index=galleryItems.findIndex(x=>x.name===item.name);
  document.getElementById("photoPrev").disabled=index<=0;
  document.getElementById("photoNext").disabled=index<0||index>=galleryItems.length-1;
}
function adjacentPhoto(delta){const i=galleryItems.findIndex(x=>x.name===viewedPhoto?.name);if(i>=0&&galleryItems[i+delta])openPhoto(galleryItems[i+delta])}
document.addEventListener("DOMContentLoaded",()=>{
  fitGraph();
  refreshCameraReadiness();setInterval(refreshCameraReadiness,700);
  document.getElementById("galleryTrash").onclick=()=>{galleryTrashMode=!galleryTrashMode;loadGallery()};
  document.getElementById("photoClose").onclick=()=>photoDialog.close();
  photoDialog.addEventListener("close",()=>{photoImage.src="";photoReady=false;photoReturnFocus?.focus()});
  document.getElementById("photoFit").onclick=fitPhoto;
  document.getElementById("photoActual").onclick=()=>zoomPhoto(1);
  document.getElementById("photoPlus").onclick=()=>zoomPhoto(photoScale*1.3);
  document.getElementById("photoMinus").onclick=()=>zoomPhoto(photoScale/1.3);
  document.getElementById("photoPrev").onclick=()=>adjacentPhoto(-1);
  document.getElementById("photoNext").onclick=()=>adjacentPhoto(1);
  photoDialog.addEventListener("keydown",e=>{if(e.key==="ArrowLeft")adjacentPhoto(-1);if(e.key==="ArrowRight")adjacentPhoto(1);e.stopPropagation()});
  photoStage.addEventListener("wheel",e=>{e.preventDefault();const r=photoStage.getBoundingClientRect();zoomPhoto(photoScale*Math.exp(-e.deltaY*.002),e.clientX-r.left,e.clientY-r.top)},{passive:false});
  photoStage.addEventListener("dblclick",fitPhoto);
  photoStage.addEventListener("pointerdown",e=>{if(e.button!==0)return;e.preventDefault();photoStage.setPointerCapture(e.pointerId);photoDrag={x:e.clientX,y:e.clientY,ox:photoX,oy:photoY}});
  photoStage.addEventListener("pointermove",e=>{if(photoDrag){photoX=photoDrag.ox+e.clientX-photoDrag.x;photoY=photoDrag.oy+e.clientY-photoDrag.y;applyPhotoTransform()}});
  photoStage.addEventListener("pointerup",()=>photoDrag=null);photoStage.addEventListener("pointercancel",()=>photoDrag=null);
  new ResizeObserver(()=>{if(photoDialog.open)fitPhoto()}).observe(photoStage);
  document.getElementById("addNodeBtn").onclick=()=>openAddMenu(40,40);
  setInterval(()=>{const status=document.getElementById("gpioStatus");document.getElementById("gpioStatusFooter").textContent=status.textContent},1000);
});

"use strict";
(()=>{
  const el=id=>document.getElementById(id),panel=el('timelapsePanel');
  let polling=false,lastSignature='',lastActive=null;
  const config=()=>({cameras:el('tlCameras').value==='both'?[0,1]:[Number(el('tlCameras').value)],
    interval:Number(el('tlInterval').value),duration:Number(el('tlDuration').value)*60,
    resolution:el('tlResolution').value,focus:el('tlFocus').value,fps:Number(el('tlFps').value)});
  function estimate(){const c=config(),frames=Math.ceil(c.duration/c.interval);el('tlEstimate').textContent=Number.isFinite(frames)&&c.fps>0?`Up to ${frames.toLocaleString()} photos per camera → ${(frames/c.fps).toFixed(1)} seconds of video at ${c.fps} FPS.`:'Enter your recording settings.'}
  el('tlForm').addEventListener('input',estimate);estimate();
  el('timelapseBtn').onclick=()=>{panel.showModal();refresh()};el('tlClose').onclick=()=>panel.close();
  el('tlForm').onsubmit=async event=>{event.preventDefault();el('tlStart').disabled=true;el('tlStatus').textContent='Starting…';try{await API.post('/api/timelapse/start',config());lastSignature='';await refresh()}catch(e){el('tlStatus').textContent=e.message;el('tlStart').disabled=false}};
  el('tlStop').onclick=async()=>{el('tlStop').disabled=true;try{await API.post('/api/timelapse/stop',{});await refresh()}catch(e){el('tlStatus').textContent=e.message}};
  async function refresh(){
    if(polling)return;polling=true;
    try{
      const data=await API.get('/api/timelapse/status'),job=data.jobs.find(j=>j.id===data.active);
      if(job&&lastActive?.id!==job.id){const c=job.settings;el('tlCameras').value=c.cameras.length===2?'both':String(c.cameras[0]);el('tlInterval').value=c.interval;el('tlDuration').value=c.duration/60;el('tlResolution').value=c.resolution;el('tlFocus').value=c.focus;el('tlFps').value=c.fps;estimate()}
      lastActive=job;
      el('tlSettings').disabled=!!job;el('tlStart').disabled=!!job;el('tlStop').disabled=job?.state!=='recording';
      el('timelapseBtn').textContent=job?'● Timelapse · '+job.state:'◷ Timelapse';
      el('tlStatus').textContent=job?`${job.state.toUpperCase()} · ${Object.entries(job.counts).map(([c,n])=>`Camera ${Number(c)+1}: ${n} photos`).join(' · ')} · ${Math.max(0,Math.ceil(job.settings.duration-(Date.now()/1000-job.started)))} seconds remaining. ${job.state==='stopping'?'Finishing in-flight photos (up to 50 seconds).':job.state==='processing'?'Creating MP4 files…':''}`:'Ready to record. Completed videos appear below.';
      const signature=JSON.stringify(data.jobs);
      if(signature!==lastSignature){lastSignature=signature;el('tlJobs').replaceChildren();
        if(!data.jobs.length)el('tlJobs').textContent='Your first timelapse starts here.';
        for(const j of data.jobs){
          const card=document.createElement('article'),title=document.createElement('h4'),details=document.createElement('p');
          title.textContent=`${new Date(j.started*1000).toLocaleString()} · ${j.state}`;
          details.textContent=`${j.settings.resolution} · every ${j.settings.interval}s · ${j.settings.fps} FPS · ${Object.values(j.counts).reduce((a,b)=>a+b,0)} photos · ${j.missed} skipped intervals`;
          card.append(title,details);
          const errors=[j.error,...Object.entries(j.errors||{}).map(([c,e])=>`Camera ${Number(c)+1}: ${e}`)].filter(Boolean);
          if(errors.length){const warning=document.createElement('p');warning.className='tlWarning';warning.textContent=errors.join(' · ');card.append(warning)}
          for(const v of j.videos){const label=document.createElement('p'),video=document.createElement('video'),download=document.createElement('a');label.textContent=`Camera ${v.camera+1} · ${v.frames} frames · ${v.seconds}s · ${v.width} × ${v.height}`;video.controls=true;video.preload='none';video.src=v.url;download.href=v.url;download.download=`timelapse-camera-${v.camera+1}.mp4`;download.textContent='↓ Download MP4';card.append(label,video,download)}
          if(['error','interrupted'].includes(j.state)){const retry=document.createElement('button');retry.textContent='Build videos from saved photos';retry.disabled=!!job;retry.onclick=async()=>{try{await API.post('/api/timelapse/rebuild',{id:j.id});await refresh()}catch(e){el('tlStatus').textContent=e.message}};card.append(retry)}
          el('tlJobs').append(card);
        }
      }
    }catch(e){el('tlStatus').textContent='Recorder unavailable: '+e.message}
    finally{polling=false}
  }
  refresh();setInterval(()=>{if(panel.open||lastActive)refresh()},1500);
})();

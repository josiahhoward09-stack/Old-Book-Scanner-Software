// Run with node tests/graph-runtime.cjs; no packages or hardware required.
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
const runtime=html.slice(html.indexOf('function fireEvent('),html.indexOf('\nfunction saveGraph('));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const records=[],nodes=[{id:1,type:'start'},{id:2,type:'delay',props:{ms:60}},{id:3,type:'camera',props:{mode:'Take Picture'}}];
let photos=0;
const context={running:true,graphEpoch:0,linked:true,setTimeout,Date,Promise,Number,Math,JSON,Boolean,
  nodeById:id=>nodes.find(n=>n.id===id),nodeRuntime:id=>states[id]||(states[id]={}),
  activity:(id,state)=>records.push([id,state]),pulseNode:()=>{},drawWires:()=>{},firingLinks:new Map(),
  outgoing:(id,port)=>id===1?[{fromNode:1,fromPort:'Trigger',toNode:2,toPort:'Trigger'}]:id===2?[{fromNode:2,fromPort:'Next',toNode:3,toPort:'Trigger'}]:[],
  captureCamera:async()=>{photos++;await sleep(20)},defs:{delay:{title:'Delay'},camera:{title:'Camera'}},toast:()=>{}};
const states={};vm.createContext(context);vm.runInContext(runtime,context);
(async()=>{
  const first=context.handleEvent(2,'Trigger');
  assert(records.some(([id,s])=>id===2&&s==='active'),'Delay must display active while waiting');
  context.running=false;context.graphEpoch++;context.running=true;
  await first;await sleep(40);
  assert.equal(photos,0,'Old delayed event must not fire after stop and restart');
  context.fireEvent(1,'Trigger');await sleep(160);
  assert.equal(photos,1,'Start → Delay → Camera should take one photo');
  const before=photos;
  await Promise.all([context.handleEvent(3,'Trigger'),context.handleEvent(3,'Trigger')]);
  assert.equal(photos,before+1,'Repeated triggers must not overlap on a busy node');
  context.running=false;context.fireEvent(1,'Trigger');await sleep(100);
  assert.equal(photos,before+1,'Stopped graph must not execute');
  console.log('PASS: active-state feedback, sequence propagation, stop cancellation, duplicate-trigger guard');
})().catch(e=>{console.error(e);process.exitCode=1});

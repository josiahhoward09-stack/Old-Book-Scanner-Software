const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../studio.js'),'utf8').split('let galleryItems=')[0];
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let active=0,maxActive=0,completed=0,failNext=false;
const context={document:{querySelectorAll:()=>[]},Map,Set,Promise,Error,toast:()=>{},
  perform:async()=>{active++;maxActive=Math.max(maxActive,active);try{await sleep(5);if(failNext){failNext=false;throw new Error('test failure')}completed++;return {ok:true}}finally{active--}}};
vm.createContext(context);vm.runInContext(source,context);
vm.runInContext('performCameraCapture=perform',context);
const node={id:1,props:{resolution:'9248x6944'}};
(async()=>{
  const burst=Array.from({length:8},()=>context.captureCamera(node));
  await assert.rejects(context.captureCamera(node),/queue is full/i);
  await Promise.all(burst);
  assert.equal(completed,8);assert.equal(maxActive,1);
  for(let i=0;i<40;i++)await context.captureCamera(node);
  assert.equal(completed,48);assert.equal(vm.runInContext('capturePending',context),0);
  failNext=true;
  const errors=await Promise.allSettled([context.captureCamera(node),context.captureCamera(node)]);
  assert(errors.every(x=>x.status==='rejected'),'A failure must cancel waiting shots');
  await context.captureCamera(node);assert.equal(completed,49,'Queue must recover after error');
  const stopped=[context.captureCamera(node),context.captureCamera(node)];
  vm.runInContext('captureQueueEpoch++',context);
  const canceled=await Promise.allSettled(stopped);
  assert(canceled.every(x=>x.status==='rejected'));
  assert.equal(vm.runInContext('capturePending',context),0);
  console.log('PASS: bounded queue, serialized bursts, 40 repeat shots, failure recovery, Stop cancellation');
})().catch(e=>{console.error(e);process.exitCode=1});

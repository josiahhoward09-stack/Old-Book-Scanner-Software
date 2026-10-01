const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../studio.js'),'utf8').split('let galleryItems=')[0];
const calls=[],saved=[];
let failCamera=null;
const context={Map,Set,Promise,Error,Number,performance,clearTimeout,setTimeout,
  linked:true,document:{querySelectorAll:()=>[],getElementById:()=>({style:{display:'none'}})},
  toast:()=>{},setValue:(_id,_port,url)=>saved.push(url),loadGallery:()=>{},
  API:{post:async(_url,data)=>{calls.push(data.camera);if(data.camera===failCamera)throw new Error('not detected');return {url:'/captures/test.jpg',filename:'test.jpg',width:1280,height:720}}}};
vm.createContext(context);vm.runInContext(source,context);
vm.runInContext('activity=()=>{};captureNotice=()=>{};',context);
(async()=>{
  for(const camera of [0,1,'both']){
    calls.length=0;
    await context.performCameraCapture({id:1,props:{camera,resolution:'instant'}});
    assert.deepEqual(calls,camera==='both'?[0,1]:[camera]);
  }
  calls.length=0;failCamera=0;
  await assert.rejects(context.performCameraCapture({id:1,props:{camera:'both',resolution:'instant'}}),/1 photo\(s\) saved.*Camera 1/);
  assert.deepEqual(calls,[0,1]);assert(saved.length>0);
  failCamera=1;
  await assert.rejects(context.performCameraCapture({id:1,props:{camera:1,resolution:'instant'}}),/0 photo\(s\) saved/);
  const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
  for(const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
  console.log('PASS: individual and dual capture routing, partial failure preserves photos, HTML syntax');
})().catch(e=>{console.error(e);process.exitCode=1});

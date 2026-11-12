// Exercise the actual async save functions with delayed browser/network stubs.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('frontend/app.js','utf8');
const functions=code.slice(code.indexOf('function saveProject(copy=false)'),code.indexOf("on('saveProject','click'"));
const maskFunction=code.slice(code.indexOf('async function saveMask()'),code.indexOf('function point('));
const dirtyFunction=code.slice(code.indexOf('let editSerial='),code.indexOf('function image('));
let checks=0;
function fixture(){
 const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{value:id==='projectName'?'First':'1'});return elements.get(id);};
 const calls=[];const state={project:'p1',projectUpdated:1,source:'image',original:null,width:256,height:256,layers:[{name:'before'}],refs:[],maskId:null,dirty:true};
 const ctx=vm.createContext({state,$,structuredClone,File:class{},mask:{toBlob:cb=>cb({})},hasMask:()=>true,upload:async()=>({id:'mask1'}),localStorage:{setItem(){}},post:async(path,spec)=>{calls.push(spec);return{id:spec.id||'copy',updated:2,name:spec.name};}});
 vm.runInContext(dirtyFunction+maskFunction+functions,ctx);return{ctx,state,$,calls,run:js=>vm.runInContext(js,ctx)};
}
(async()=>{
 let f=fixture(),release;f.ctx.post=async(path,spec)=>{f.calls.push(spec);await new Promise(r=>release=r);return{id:'p1',updated:2,name:spec.name};};
 let saving=f.run('saveProject()');await new Promise(r=>setImmediate(r));f.state.layers[0].name='after';f.$('projectName').value='Later';f.run('dirty()');release();await saving;
 assert.equal(f.calls[0].payload.layers[0].name,'before');assert.equal(f.state.dirty,true);assert.equal(f.$('projectName').value,'Later');assert.equal(f.state.projectUpdated,2);checks++;
 f=fixture();f.ctx.upload=()=>new Promise(r=>release=r);saving=f.run('saveMask()');await new Promise(r=>setImmediate(r));f.run('maskSerial++');release({id:'stale-mask'});assert.equal(await saving,'stale-mask');assert.equal(f.state.maskId,null);checks++;
 f=fixture();let active=0,maxActive=0;f.ctx.post=async(path,spec)=>{active++;maxActive=Math.max(maxActive,active);f.calls.push(spec);await new Promise(r=>setImmediate(r));active--;return{id:'p1',updated:spec.base_updated+1,name:spec.name};};
 await Promise.all([f.run('saveProject()'),f.run('saveProject()')]);assert.equal(maxActive,1);assert.equal(f.calls[1].base_updated,2);checks++;
 f=fixture();f.ctx.post=async()=>{throw Error('offline');};await assert.rejects(f.run('saveProject()'),/offline/);f.ctx.post=async(_,spec)=>({id:'p1',updated:2,name:spec.name});await f.run('saveProject()');assert.equal(f.state.dirty,false);checks++;
 f=fixture();f.ctx.post=async()=>{await new Promise(r=>release=r);return{id:'p1',updated:2,name:'Old'};};saving=f.run('saveProject()');await new Promise(r=>setImmediate(r));f.state.project='p2';release();await saving;assert.equal(f.state.project,'p2');assert.equal(f.state.projectUpdated,1);checks++;
 f=fixture();await f.run('saveProject(true)');assert.equal(f.calls[0].id,null);assert.equal(f.calls[0].base_updated,null);assert.equal(f.state.project,'copy');checks++;
 console.log(checks+' frontend save concurrency checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

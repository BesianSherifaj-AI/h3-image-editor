// Execute the real submit handler with API doubles; never mutate the live project.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('frontend/pro.js','utf8');
const handler=code.slice(code.indexOf("on('runVideo','click'"),code.indexOf('\nasync function refreshPro'));
(async()=>{
 let submit,posted=[],requested=[],maskCalls=0;
 const fields=Object.fromEntries(Object.entries({videoStart:'0',videoAction:'h3',videoSelection:'auto',videoDuration:'1',videoFps:'4',videoPrompt:'Make the lemon green',videoObject:'',videoColor:'#0000ff',videoTracking:'sam',videoGrow:'6',videoFeather:'2',videoAttempts:'2'}).map(([k,value])=>[k,{value}]));
 fields.videoAudio={checked:true};fields.videoReview={checked:true};fields.videoBlend={checked:true};
 const state={source:'untouched-canvas'},proState={video:{id:'clip'},frame:null};
 const ctx=vm.createContext({on:(id,event,fn)=>submit=fn,$:id=>fields[id],state,proState,hasMask:()=>true,currentInput:async()=>{maskCalls++;return{mask:'painted'};},api:async url=>{requested.push(url);return{id:'frame',start:Number(fields.videoStart.value)};},post:async(url,payload)=>posted.push(payload),toast(){},refreshPro:async()=>{}});
 vm.runInContext(handler,ctx);await submit();assert.equal(posted[0].source,'frame');assert.equal(posted[0].mask,null);assert.equal(posted[0].max_attempts,2);assert.equal(maskCalls,0);assert.equal(state.source,'untouched-canvas');
 await submit();assert.equal(requested.length,1); // Cached matching preview.
 fields.videoStart.value='1';await submit();assert.equal(requested.length,2); // Start-time change refreshes source.
 fields.videoSelection.value='paint';await assert.rejects(submit(),/Load the selection frame/);assert.equal(posted.length,3);
 state.source='frame';ctx.hasMask=()=>false;await assert.rejects(submit(),/Paint a selection/);
 ctx.hasMask=()=>true;await submit();assert.equal(posted[3].mask,'painted');assert.equal(maskCalls,1);
 fields.videoAction.value='trim';await submit();assert.equal(posted[4].mask,null);assert.equal(maskCalls,1);
 console.log('7 video UI checks passed: automatic selection, cache, start change, stale/missing mask, painted mode and trim.');
})().catch(e=>{console.error(e);process.exitCode=1;});

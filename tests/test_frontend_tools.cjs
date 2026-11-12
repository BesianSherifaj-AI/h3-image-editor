// Actual asynchronous tool functions: stale-result protection, cache recovery and save guards.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const app=fs.readFileSync('frontend/app.js','utf8'),code=fs.readFileSync('frontend/tools.js','utf8');
const line=name=>code.split('\n').find(l=>l.startsWith('function '+name+'(')||l.startsWith('async function '+name+'('));
let checks=0;
(async()=>{
 const cacheCode=app.slice(app.indexOf('let compositionCache='),app.indexOf("on('flatten'"));
 const state={width:100,height:100,layers:[{asset:'a',x:0}]};let calls=[],release;
 const ctx=vm.createContext({state,structuredClone,post:async(_,spec)=>{calls.push(spec);return{id:'composition'+calls.length};}});
 vm.runInContext(cacheCode,ctx);const run=s=>vm.runInContext(s,ctx);
 const [one,two]=await Promise.all([run('composition()'),run('composition()')]);assert.equal(one.id,two.id);assert.equal(calls.length,1);checks++;
 state.layers[0].x=8;assert.equal((await run('composition()')).id,'composition2');assert.equal(calls[0].layers[0].x,0);checks++;
 ctx.post=async()=>{throw Error('offline');};state.layers[0].x=10;await assert.rejects(run('composition()'),/offline/);ctx.post=async()=>({id:'recovered'});assert.equal((await run('composition()')).id,'recovered');checks++;
 ctx.post=async()=>new Promise(r=>release=r);state.layers[0].x=20;const older=run('composition()');await new Promise(r=>setImmediate(r));state.layers[0].x=30;ctx.post=async()=>({id:'newer'});await run('composition()');release({id:'older'});await older;assert.equal((await run('composition()')).id,'newer');checks++;

 const pixels=value=>({data:new Uint8ClampedArray([value,value,value,255])});
 const selectionCtx=vm.createContext({});vm.runInContext(line('combineMaskPixels'),selectionCtx);
 for(const [mode,expected]of [['replace',150],['add',150],['subtract',0],['intersect',100]]){selectionCtx.base=pixels(100);selectionCtx.incoming=pixels(150);selectionCtx.mode=mode;assert.equal(vm.runInContext('combineMaskPixels(base,incoming,mode).data[0]',selectionCtx),expected);}checks++;
 let remembers=0,changes=0,mutations=0;
 const selection={editSerial:1,image:()=>new Promise(r=>release=r),mask:{width:32,height:32},rememberMask:()=>remembers++,markMaskChanged:()=>changes++,mctx:{getImageData:()=>pixels(100),putImageData:()=>mutations++},document:{createElement:()=>({getContext:()=>({drawImage(){},getImageData:()=>pixels(150)})})}};
 const sc=vm.createContext(selection);vm.runInContext(line('combineMaskPixels')+'\n'+line('applyIncomingSelection'),sc);
 let applying=vm.runInContext("applyIncomingSelection('mask','add',1)",sc);sc.editSerial=2;release({naturalWidth:32,naturalHeight:32});await assert.rejects(applying,/canvas changed/);assert.equal(remembers+mutations,0);checks++;
 sc.image=async()=>({naturalWidth:64,naturalHeight:32});await assert.rejects(vm.runInContext("applyIncomingSelection('mask','add',2)",sc),/dimensions/);assert.equal(remembers+mutations,0);checks++;
 sc.image=async()=>({naturalWidth:32,naturalHeight:32});await vm.runInContext("applyIncomingSelection('mask','add',2)",sc);assert.deepEqual([remembers,changes,mutations],[1,1,1]);checks++;

 let saves=0;const elements={autoSave:{checked:true},savedState:{}};
 const ac=vm.createContext({toolsState:{observed:5,changedAt:0,autoSaving:false,autoError:false,lasso:null},state:{dirty:true,source:'image',project:'p'},editSerial:5,drawing:false,layerDrag:null,Date:{now:()=>10000},$:id=>elements[id],saveProject:async()=>{saves++;ac.state.dirty=false;},toast(){}});vm.runInContext(line('autoSaveTick'),ac);
 for(const [key,value]of [['drawing',true],['layerDrag',{}]]){ac[key]=value;await vm.runInContext('autoSaveTick()',ac);assert.equal(saves,0);ac[key]=key==='drawing'?false:null;}ac.toolsState.lasso={};await vm.runInContext('autoSaveTick()',ac);assert.equal(saves,0);ac.toolsState.lasso=null;checks++;
 ac.toolsState.changedAt=9000;await vm.runInContext('autoSaveTick()',ac);assert.equal(saves,0);ac.toolsState.changedAt=0;await vm.runInContext('autoSaveTick()',ac);assert.equal(saves,1);assert.equal(elements.savedState.textContent,'Auto-saved on this computer');checks++;
 ac.state.dirty=true;ac.saveProject=async()=>{saves++;throw Error('revision conflict');};await vm.runInContext('autoSaveTick()',ac);assert.equal(ac.toolsState.autoError,true);await vm.runInContext('autoSaveTick()',ac);assert.equal(saves,2);ac.state.dirty=false;await vm.runInContext('autoSaveTick()',ac);assert.equal(ac.toolsState.autoError,false);checks++;
 let restores=0,maskChanges=0;const controls={finishPolygon:{},cancelPolygon:{}};
 const lc=vm.createContext({toolsState:{lasso:{owner:'old',base:{}}},canvasOwner:()=> 'new',mctx:{putImageData:()=>restores++},$:id=>controls[id],markMaskChanged:()=>maskChanges++});
 vm.runInContext(line('cancelLasso')+'\n'+line('finishLasso'),lc);vm.runInContext('cancelLasso()',lc);assert.equal(restores+maskChanges,0);assert.equal(lc.toolsState.lasso,null);checks++;
 lc.toolsState.lasso={owner:'new',base:{}};vm.runInContext('cancelLasso()',lc);assert.equal(restores,1);assert.equal(maskChanges,1);assert.equal(controls.cancelPolygon.disabled,true);checks++;
 lc.toolsState.lasso={owner:'old',base:{},points:[1,2,3]};vm.runInContext('finishLasso()',lc);assert.equal(restores,1);assert.equal(maskChanges,1);assert.equal(lc.toolsState.lasso,null);checks++;
 const review={spec:{prompt:'Make the lemon green.',source_sha:'reviewed'},outputs:[{text:'Inspect the edge.'}]};let opened=0,closed=0;
 const reviewFields=Object.fromEntries(['directorPrompt','directorObject','directorMode','directorAttempts','directorWhole'].map(k=>[k,{}]));
 const rc=vm.createContext({canvasOwner:()=> 'canvas',composition:async()=>({sha256:'different'}),$:id=>reviewFields[id],reviewDialog:{close:()=>closed++},openPro:async()=>opened++,toast(){},review});vm.runInContext(line('refinementDraft')+'\n'+line('prepareReviewRefinement'),rc);
 await assert.rejects(vm.runInContext('prepareReviewRefinement(review)',rc),/differs/);assert.equal(opened+closed,0);assert.equal(reviewFields.directorPrompt.value,undefined);checks++;
 rc.composition=async()=>({sha256:'reviewed'});await vm.runInContext('prepareReviewRefinement(review)',rc);assert.equal(opened,1);assert.equal(closed,1);assert.equal(reviewFields.directorMode.value,'plan');assert.equal(reviewFields.directorWhole.checked,false);assert.match(reviewFields.directorPrompt.value,/Inspect the edge/);checks++;
 rc.review={spec:{prompt:'g'.repeat(5000)},outputs:[{text:'f'.repeat(9000)}]};assert.ok(vm.runInContext('refinementDraft(review).length',rc)<=1800);checks++;
 console.log(checks+' frontend tool behavior checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

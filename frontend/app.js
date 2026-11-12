'use strict';

const $=id=>document.getElementById(id);

const view={zoom:1,x:0,y:0,space:false,panning:false};
const state={projectUpdated:null,source:null,original:null,layers:[],selected:0,refs:[],project:null,tool:'brush',dirty:false,width:1024,height:1024,maskId:null,shownJobs:new Set(),frames:[]};

let models=[],jobs=[],maskUndo=[],maskRedo=[],drawing=false,startPoint=null,lastPoint=null,uploadMode='source',multiMode='refs',polling=false;

const mask=document.createElement('canvas'),mctx=mask.getContext('2d',{willReadFrequently:true});

const canvas=$('imageCanvas'),ctx=canvas.getContext('2d'),overlay=$('maskCanvas'),octx=overlay.getContext('2d');

const imageCache=new Map();

function toast(message,error=false){$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').style.display='none',error?10000:4200);}

async function api(path,options={}){const r=await fetch('/api'+path,{...options,headers:{...(options.body && !(options.body instanceof FormData)?{'Content-Type':'application/json'}:{}),...options.headers}});if(!r.ok){let msg;try{const j=await r.json();msg=typeof j.detail==='string'?j.detail:JSON.stringify(j.detail);}catch{msg=r.statusText;}throw new Error(msg||'Request failed');}return r.json();}

const post=(path,value)=>api(path,{method:'POST',body:JSON.stringify(value)});

function on(id,event,fn){$(id).addEventListener(event,async e=>{try{await fn(e);}catch(err){toast(err.message,true);}});}

let editSerial=0,maskSerial=0,saveQueue=Promise.resolve();
function dirty(){editSerial++;state.dirty=true;$('savedState').textContent='Unsaved changes';}

function image(id){if(!imageCache.has(id))imageCache.set(id,new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=()=>reject(new Error('Image could not be loaded'));im.src='/api/assets/'+id;}));return imageCache.get(id);}

function emptyMask(){view.zoom=1;view.x=view.y=0;maskRedo=[];mask.width=state.width;mask.height=state.height;mctx.fillStyle='black';mctx.fillRect(0,0,mask.width,mask.height);state.maskId=null;maskSerial++;maskUndo=[];renderMask();}

function rememberMask(){maskRedo=[];state.maskId=null;maskSerial++;maskUndo.push(mctx.getImageData(0,0,mask.width,mask.height));const limit=Math.max(1,Math.min(8,Math.floor(64*1024*1024/(mask.width*mask.height*4))));while(maskUndo.length>limit)maskUndo.shift();}

function hasMask(){const a=mctx.getImageData(0,0,mask.width,mask.height).data;for(let i=0;i<a.length;i+=4)if(a[i]>0)return true;return false;}

let maskOverlayData=null;
function renderMask(){if(overlay.width!==state.width||overlay.height!==state.height||!maskOverlayData){overlay.width=state.width;overlay.height=state.height;maskOverlayData=octx.createImageData(mask.width,mask.height);for(let i=0;i<maskOverlayData.data.length;i+=4){maskOverlayData.data[i]=180;maskOverlayData.data[i+1]=175;maskOverlayData.data[i+2]=255;}}const a=mctx.getImageData(0,0,mask.width,mask.height).data,b=maskOverlayData.data;for(let i=0;i<a.length;i+=4)b[i+3]=a[i];octx.putImageData(maskOverlayData,0,0);}

function fit(){const rect=$('dropzone').getBoundingClientRect(),ratio=Math.max(.01,Math.min((rect.width-32)/state.width,(rect.height-40)/state.height,1.5));view.ratio=ratio;const wrap=$('canvasWrap');wrap.style.width=Math.floor(state.width*ratio*view.zoom)+'px';wrap.style.height=Math.floor(state.height*ratio*view.zoom)+'px';wrap.style.transform=`translate(${view.x}px,${view.y}px)`;if($('zoomValue'))$('zoomValue').textContent=Math.round(ratio*view.zoom*100)+'%';}


let renderVersion=0;

async function renderCanvas(){const version=++renderVersion;if(!state.layers.length&&!state.source){$('emptyState').classList.remove('hidden');$('canvasWrap').classList.add('hidden');return;}

$('emptyState').classList.add('hidden');$('canvasWrap').classList.remove('hidden');

const layers=[...state.layers];const ims=await Promise.all(layers.map(l=>image(l.asset)));if(version!==renderVersion)return;
if(canvas.width!==state.width||canvas.height!==state.height){canvas.width=state.width;canvas.height=state.height;}ctx.clearRect(0,0,canvas.width,canvas.height);

for(let i=0;i<layers.length;i++){const l=layers[i],im=ims[i];if(!l.visible)continue;ctx.save();ctx.globalAlpha=l.opacity;ctx.globalCompositeOperation=!l.blend||l.blend==='normal'?'source-over':l.blend;const w=im.width*l.scale,h=im.height*l.scale;ctx.translate(l.x+w/2,l.y+h/2);ctx.rotate(l.rotation*Math.PI/180);ctx.scale(l.flip_x?-1:1,l.flip_y?-1:1);ctx.drawImage(im,-w/2,-h/2,w,h);ctx.restore();}

fit();$('canvasMeta').textContent=state.width+' × '+state.height+' · '+state.layers.length+' layer'+(state.layers.length===1?'':'s');$('download').disabled=false;$('compare').disabled=!state.original;}

function makeLayer(asset,name){return{asset:asset.id||asset,name:name||asset.name||'Image',visible:true,x:0,y:0,scale:1,opacity:1,rotation:0};}

async function adopt(asset,keepOriginal=false){if(state.dirty&&state.source)await saveProject();if(keepOriginal&&state.source)state.original=state.source;else state.original=null;state.source=asset.id;state.width=asset.width;state.height=asset.height;state.layers=[makeLayer(asset)];state.selected=0;emptyMask();renderLayers();await renderCanvas();$('canvasLabel').textContent=asset.name||'Image workspace';dirty();}

function renderLayers(){const host=$('layers');host.replaceChildren();if(!state.layers.length){const p=document.createElement('p');p.className='quiet-empty';p.textContent='Add an image to start a layer stack.';host.append(p);}

state.layers.forEach((l,i)=>{const row=document.createElement('div');row.className='layer'+(i===state.selected?' selected':'');row.tabIndex=0;row.setAttribute('aria-label',l.name);const im=document.createElement('img');im.src='/api/assets/'+l.asset;im.alt='';const t=document.createElement('span');t.textContent=l.name;const b=document.createElement('button');b.textContent=l.visible?'◉':'○';b.title='Toggle visibility';b.onclick=e=>{e.stopPropagation();l.visible=!l.visible;renderLayers();renderCanvas();dirty();};row.append(im,t,b);row.onclick=()=>{state.selected=i;renderLayers();};row.onkeydown=e=>{if(e.key==='Enter'){state.selected=i;renderLayers();}};host.append(row);});

const l=state.layers[state.selected];$('layerControls').classList.toggle('hidden',!l);if(l){$('layerName').value=l.name;for(const[k,id]of [['x','layerX'],['y','layerY'],['scale','layerScale'],['rotation','layerRotation'],['opacity','layerOpacity']])$(id).value=l[k];$('opacityValue').textContent=Math.round(l.opacity*100)+'%';if($('layerBlend')){$('layerBlend').value=l.blend||'normal';$('flipLayerX').setAttribute('aria-pressed',!!l.flip_x);$('flipLayerY').setAttribute('aria-pressed',!!l.flip_y);}}}

async function upload(file){if(!file)return;if(file.size>40*1024*1024)throw new Error('Use an image up to 40 MB.');const f=new FormData();f.append('file',file);return api('/assets',{method:'POST',body:f});}

async function saveMask(){if(!state.source||!hasMask())return null;if(state.maskId)return state.maskId;const serial=maskSerial;const blob=await new Promise(r=>mask.toBlob(r,'image/png'));const a=await upload(new File([blob],'selection.png',{type:'image/png'}));if(serial===maskSerial)state.maskId=a.id;return a.id;}

function point(e){const r=overlay.getBoundingClientRect();return{x:Math.max(0,Math.min(mask.width,(e.clientX-r.left)*mask.width/r.width)),y:Math.max(0,Math.min(mask.height,(e.clientY-r.top)*mask.height/r.height))};}

overlay.addEventListener('pointerdown',e=>{if(!state.source||e.button!==0||!['brush','rectangle','erase'].includes(state.tool))return;drawing=true;overlay.setPointerCapture(e.pointerId);rememberMask();startPoint=lastPoint=point(e);drawStroke(e);});

function drawStroke(e){if(!drawing)return;state.maskId=null;maskSerial++;dirty();const p=point(e);mctx.fillStyle=mctx.strokeStyle=state.tool==='erase'?'black':'white';if(state.tool==='rectangle'){mctx.putImageData(maskUndo[maskUndo.length-1],0,0);mctx.fillRect(startPoint.x,startPoint.y,p.x-startPoint.x,p.y-startPoint.y);}else{mctx.lineWidth=Number($('brushSize').value);mctx.lineCap='round';mctx.lineJoin='round';mctx.beginPath();mctx.moveTo(lastPoint.x,lastPoint.y);mctx.lineTo(p.x+.01,p.y+.01);mctx.stroke();}lastPoint=p;renderMask();}

overlay.addEventListener('pointermove',drawStroke);

overlay.addEventListener('pointerup',()=>{if(!drawing)return;drawing=false;state.maskId=null;maskSerial++;dirty();});

overlay.addEventListener('pointercancel',()=>drawing=false);

document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>{state.tool=b.dataset.tool;document.querySelectorAll('[data-tool]').forEach(x=>x.classList.toggle('active',x===b));});

on('undoMask','click',()=>{if(maskUndo.length){maskRedo.push(mctx.getImageData(0,0,mask.width,mask.height));state.maskId=null;maskSerial++;mctx.putImageData(maskUndo.pop(),0,0);renderMask();dirty();}});

on('clearMask','click',()=>{rememberMask();mctx.fillStyle='black';mctx.fillRect(0,0,mask.width,mask.height);state.maskId=null;maskSerial++;renderMask();dirty();});

on('invertMask','click',()=>{const a=mctx.getImageData(0,0,mask.width,mask.height);rememberMask();const b=mctx.getImageData(0,0,mask.width,mask.height);for(let i=0;i<b.data.length;i+=4)b.data[i]=b.data[i+1]=b.data[i+2]=255-b.data[i];mctx.putImageData(b,0,0);state.maskId=null;maskSerial++;renderMask();dirty();});

for(const id of ['uploadMain','addImage','addLayer'])on(id,'click',()=>{uploadMode=id==='addLayer'?'layer':'source';$('imageInput').value='';$('imageInput').click();});

async function importFile(file){const a=await upload(file);if(uploadMode==='layer'&&state.source){state.layers.push(makeLayer(a));state.selected=state.layers.length-1;renderLayers();await renderCanvas();dirty();}else{await adopt(a);$('task').value='edit';updateControls();}toast('Image added. Your original stays untouched.');}

on('imageInput','change',async e=>{if(e.target.files[0])await importFile(e.target.files[0]);});

$('dropzone').addEventListener('dragover',e=>{e.preventDefault();$('dropzone').classList.add('dragover');});

$('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('dragover'));

$('dropzone').addEventListener('drop',async e=>{e.preventDefault();$('dropzone').classList.remove('dragover');uploadMode='source';try{if(e.dataTransfer.files[0])await importFile(e.dataTransfer.files[0]);}catch(err){toast(err.message,true);}});

function updateControls(){const m=models.find(x=>x.id===$('model').value);if(!m)return;const task=$('task').value;

const fixedSteps=m.id==='h3'?6:m.id==='zimage'?(task==='create'?9:8):null;
$('steps').disabled=fixedSteps!==null;if(fixedSteps!==null)$('steps').value=fixedSteps;
$('steps').title=fixedSteps!==null?'This model uses its native '+fixedSteps+'-step schedule.':'Number of Qwen sampling steps';
$('layerCount').disabled=m.id!=='layered';$('padding').disabled=task!=='outpaint';
$('strength').disabled=m.id!=='zimage'||task==='create';$('controlMode').disabled=m.id!=='zimage'||task!=='control';
$('addReferences').disabled=!['h3','qwen21'].includes(m.id);

$('modelBadge').textContent=m.available?((m.tested_tasks||[]).includes(task)?'tested locally':'installed · test pending'):'downloading / missing';$('modelHelp').textContent=m.available?m.about:'Waiting for '+m.missing.map(x=>x.split('.')[0]).join(', ');

$('generate').disabled=!m.available;$('generateLabel').textContent=task==='create'?'Generate image':task==='layers'?'Separate layers':'Queue image edit';

$('editHint').classList.toggle('hidden',task==='create'||task==='layers');$('size').disabled=false;

document.querySelectorAll('[data-mode]').forEach(b=>{const active=b.dataset.mode===(task==='create'?'create':'edit');b.classList.toggle('selected',active);b.setAttribute('aria-selected',active);});

for(const o of $('task').options)o.disabled=!m.tasks.includes(o.value);

if(!m.tasks.includes(task)){$('task').value=m.tasks[0];updateControls();}}

on('model','change',()=>{const m=models.find(x=>x.id===$('model').value);$('steps').value=m.steps;updateControls();dirty();});on('task','change',()=>{updateControls();dirty();});

document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{$('task').value=b.dataset.mode==='create'?'create':'edit';if($('model').value==='layered')$('model').value='h3';updateControls();dirty();});

const presets={photo:'Editorial photograph of a sculptural glass vase on warm travertine, soft afternoon window light, subtle shadows, muted natural colors, refined composition, realistic detail.',product:'Premium studio product photograph of a minimal ceramic perfume bottle, pale sage background, large softbox lighting, elegant shadows, fine material detail, no text.',illustration:'A tiny botanical greenhouse on a quiet hillside, hand-painted storybook illustration, warm sunset palette, delicate ink lines, charming plants and rich atmospheric depth.'};

document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{$('prompt').value=presets[b.dataset.preset];dirty();});

for(const id of ['prompt','seed','size','steps','layerCount','padding','strength','controlMode','feather','variationCount','projectName'])on(id,'input',dirty);

on('generate','click',async()=>{const task=$('task').value;if(!promptValue())throw new Error('Describe the image or edit you want.');if(task!=='create'&&!state.source)throw new Error('Add a source image first.');

$('generate').disabled=true;try{if(task!=='create'){state.source=(await composition()).id;}const size=$('size').value.split(',').map(Number);const maskId=task!=='create'?await saveMask():null;

const spec={model:$('model').value,task,prompt:promptValue(),width:size[0],height:size[1],seed:Number($('seed').value),steps:Number($('steps').value),layers:Number($('layerCount').value),padding:Number($('padding').value),feather:Number($('feather').value),strength:Number($('strength').value),control_mode:$('controlMode').value,source:task==='create'?null:state.source,mask:maskId,references:state.refs.map(x=>x.id)};

const count=+$('variationCount').value;await post('/studio/variations',{request_id:crypto.randomUUID(),spec,count});toast(count>1?count+' variations queued. Compare them in the Library.':'Added to your persistent queue.');await refresh();}finally{updateControls();}});

function promptValue(){return $('prompt').value.trim();}

async function refresh(){if(polling)return;polling=true;try{const[h,js]=await Promise.all([api('/health'),api('/jobs')]);jobs=js;document.dispatchEvent(new CustomEvent('studio-health',{detail:h}));$('queueStatus').textContent=h.status.message;$('pauseQueue').textContent=h.paused?'Resume queue':'Pause queue';$('pauseQueue').dataset.paused=h.paused;renderJobs();}catch(e){$('queueStatus').textContent='Studio reconnecting… Your queue is saved.';}finally{polling=false;}}

function renderJobs(){const host=$('jobs');host.replaceChildren();$('jobCount').textContent=jobs.length;if(!jobs.length){const p=document.createElement('div');p.className='quiet-empty';p.textContent='Your next image will appear here. You can keep editing while it renders.';host.append(p);}

jobs.forEach(j=>{const card=document.createElement('article');card.className='job '+j.state;const output=j.outputs[0];if(output?.url){const im=document.createElement('img');im.src=output.url;im.alt=j.spec.prompt||'Enhanced image';im.onclick=()=>useJob(j).catch(e=>toast(e.message,true));card.append(im);}const body=document.createElement('div');body.className='job-body';const title=document.createElement('div');title.className='job-title';const label=j.spec._quality_review?'AI quality review':j.spec._video_review?'Video · inspect edited frames':j.spec._director_prompt?(j.spec._stage==='reviewing'?'AI Director · review image':'AI Director · inspect and plan'):j.spec._heal?'AI Heal · '+j.spec.prompt:j.spec.model==='upscale'?'SeedVR2 · '+j.spec.scale+'× enhancement':j.spec.prompt;title.textContent=label;title.title=label;const p=document.createElement('p');p.textContent=j.state+' · '+j.message;const small=document.createElement('small');small.textContent=(models.find(x=>x.id===j.spec.model)?.name||j.spec.model)+' · seed '+j.spec.seed;body.append(title,p,small);

const b=document.createElement('button');if(output){b.textContent=j.spec._quality_review?'Review report':j.spec.model==='vision'?'Review suggestion':j.spec.model==='segment'?'Use selection':j.spec.model==='layered'?'Open layer stack':j.spec.model==='samtrack'?'Review masks':'Use image';b.onclick=()=>useJob(j).catch(e=>toast(e.message,true));body.append(document.createElement('br'),b);}else if(!['failed','cancelled','complete'].includes(j.state)){b.textContent='Cancel';b.onclick=async()=>{try{await post('/jobs/'+j.id+'/cancel',{});await refresh();}catch(e){toast(e.message,true);}};body.append(document.createElement('br'),b);}

if(j.state==='complete'){const exp=document.createElement('a');exp.href='/api/jobs/'+j.id+'/export';exp.download='comfy-workflow-bundle.zip';exp.textContent='Export workflow';exp.className='workflow-export';body.append(document.createElement('br'),exp);}card.append(body);const bar=document.createElement('div');bar.className='progress';card.append(bar);host.append(card);});}

async function useJob(j){if(j.spec._quality_review){showQualityReview(j);return;}if(j.spec.model==='samtrack'){showTracked(j.outputs);toast('Tracking masks ready. Inspect every frame.');return;}if(j.spec.model==='vision'){$('visionSuggestion').value=j.outputs[0].text;$('visionSuggestion').closest('details').open=true;toast('Review the suggestion. Nothing was applied.');return;}if(j.spec.model==='segment'){const serial=editSerial;if(j.spec.source_sha?((await composition()).sha256!==j.spec.source_sha):(state.source!==j.spec.source))throw new Error('Restore the canvas used for this selection first.');await applyIncomingSelection(j.outputs[0],$('selectionCombine').value,serial);toast('Selection applied. Inspect its edges before editing.');return;}if(j.spec.model==='layered'){if(state.dirty&&state.source)await saveProject();const layers=j.outputs.filter(x=>x.role==='layer');if(!layers.length)throw new Error('No separated layers returned.');state.original=j.spec.source;state.source=j.outputs[0].id;state.width=layers[0].width;state.height=layers[0].height;state.layers=layers.map(x=>makeLayer(x));state.selected=state.layers.length-1;emptyMask();renderLayers();await renderCanvas();dirty();}else await adopt(j.outputs[0],true);toast('Loaded into your workspace.');}

on('pauseQueue','click',async()=>{await post('/queue',{paused:$('pauseQueue').dataset.paused!=='true'});await refresh();});

for(const [id,key]of [['layerName','name'],['layerX','x'],['layerY','y'],['layerScale','scale'],['layerRotation','rotation'],['layerOpacity','opacity']])on(id,'input',async()=>{const l=state.layers[state.selected];if(!l)return;const v=key==='name'?$(id).value:Number($(id).value);if(typeof v==='number'&&!Number.isFinite(v))return;l[key]=v;await renderCanvas();if(key==='opacity')$('opacityValue').textContent=Math.round(v*100)+'%';dirty();});

on('layerName','change',renderLayers);

for(const[id,d]of [['layerDown',-1],['layerUp',1]])on(id,'click',async()=>{const i=state.selected,n=i+d;if(n<0||n>=state.layers.length)return;[state.layers[i],state.layers[n]]=[state.layers[n],state.layers[i]];state.selected=n;renderLayers();await renderCanvas();dirty();});

on('removeLayer','click',async()=>{state.layers.splice(state.selected,1);state.selected=Math.max(0,state.selected-1);renderLayers();await renderCanvas();dirty();});

let compositionCache=null;
async function composition(){if(!state.layers.length)throw new Error('Add a layer first.');const snapshot=structuredClone({width:state.width,height:state.height,layers:state.layers}),key=JSON.stringify(snapshot);if(compositionCache?.key===key)return compositionCache.promise;const promise=post('/compose',snapshot);compositionCache={key,promise};try{return await promise;}catch(e){if(compositionCache?.promise===promise)compositionCache=null;throw e;}}

on('flatten','click',async()=>{const a=await composition();state.original=state.source;state.source=a.id;toast('Composition saved as a new image. The layer stack is still editable.');dirty();});

on('download','click',async()=>{const a=await composition();const link=document.createElement('a');link.href=a.url;link.download=($('projectName').value||'atelier')+'.png';link.click();toast('PNG exported with transparency.');});

async function compareStart(){if(!state.original)return;const im=await image(state.original);ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(im,0,0,canvas.width,canvas.height);overlay.style.visibility='hidden';}

function compareEnd(){overlay.style.visibility='visible';renderCanvas();}

$('compare').addEventListener('pointerdown',compareStart);$('compare').addEventListener('pointerup',compareEnd);$('compare').addEventListener('pointerleave',compareEnd);$('compare').addEventListener('keydown',e=>{if(e.key===' ')compareStart();});$('compare').addEventListener('keyup',compareEnd);

async function cpu(action){if(!state.source)throw new Error('Add an image first.');state.source=(await composition()).id;const mid=await saveMask();if(!mid)throw new Error('Paint the area first.');const a=await post('/cpu',{source:state.source,mask:mid,action,color:$('fillColor').value});await adopt(a,true);toast('Saved a new image using CPU tools.');}

on('cpuCutout','click',()=>cpu('cutout'));on('cpuRemove','click',()=>cpu('remove'));on('cpuRecolor','click',()=>cpu('recolor'));

on('resizeImage','click',async()=>{if(!state.source)throw new Error('Add an image first.');state.source=(await composition()).id;const [w,h]=$('size').value.split(',').map(Number);const a=await post('/cpu',{source:state.source,action:'resize',width:w,height:h});await adopt(a,true);toast('Resized to the selected canvas size.');});

on('addReferences','click',()=>{multiMode='refs';$('multiInput').value='';$('multiInput').click();});

function renderReferences(){const host=$('references');host.replaceChildren();state.refs.forEach((a,i)=>{const im=document.createElement('img');im.src=a.url;im.title='Reference '+(i+2)+' · click to remove';im.onclick=()=>{state.refs.splice(i,1);renderReferences();dirty();};host.append(im);});}

on('trackFrames','click',()=>{if(!state.source||!hasMask())throw new Error('Add the first frame and paint its selection before tracking.');multiMode='track';$('multiInput').value='';$('multiInput').click();});

on('multiInput','change',async e=>{let files=Array.from(e.target.files);if(multiMode==='refs'){if(files.length+state.refs.length>3)throw new Error('Use up to 3 references.');for(const f of files)state.refs.push(await upload(f));renderReferences();dirty();return;}

files.sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));if(files.length<2||files.length>24)throw new Error('Select 2–24 frames, including the first frame.');toast($('trackMethod').value==='sam'?'Preparing frames for SAM tracking…':'Tracking the selected mask on CPU…');const ids=[];for(const f of files)ids.push((await upload(f)).id);const r=await post('/track',{frames:ids,mask:await saveMask(),method:$('trackMethod').value});if(r.id){toast('SAM tracking queued.');await refresh();return;}showTracked(r.masks);toast('Masks ready. Inspect for drift; click each to export.');});
function showTracked(masks){$('tracked').closest('details').open=true;$('tracked').replaceChildren();masks.forEach(a=>{const link=document.createElement('a');link.href=a.url;link.download=a.name+'.png';const im=document.createElement('img');im.src=a.url;im.alt=a.name;link.append(im);$('tracked').append(link);});}

function saveProject(copy=false){const pending=saveQueue.catch(()=>{}).then(()=>persistProject(copy));saveQueue=pending;return pending;}
async function persistProject(copy=false){
 const serial=editSerial,project=state.project,name=$('projectName').value;
 const payload=structuredClone({source:state.source,original:state.original,width:state.width,height:state.height,layers:state.layers,references:state.refs,form:Object.fromEntries(['model','task','prompt','size','seed','steps','layerCount','padding','strength','controlMode','feather','variationCount'].map(id=>[id,$(id).value]))});
 const revision=state.projectUpdated;payload.mask=await saveMask();
 const p=await post('/projects',{id:copy?null:project,base_updated:copy?null:revision,name:name+(copy?' (copy)':''),payload});
 if(state.project===project){state.project=p.id;state.projectUpdated=p.updated;if($('projectName').value===name)$('projectName').value=p.name;state.dirty=serial!==editSerial;$('savedState').textContent=state.dirty?'Saved revision · newer changes unsaved':'Saved on this computer';localStorage.setItem('atelier-last-project',p.id);}
 return p;
}

on('saveProject','click',async()=>{await saveProject();toast('Project saved with images, mask and editable layers.');});

on('exportProject','click',async()=>{const p=await saveProject();const a=document.createElement('a');a.href='/api/projects/'+p.id+'/export';a.download='image-studio-project.zip';a.click();});

on('openProjects','click',async()=>{const ps=await api('/projects');$('projectList').replaceChildren();if(!ps.length){const p=document.createElement('p');p.textContent='Save a project to return to it here.';$('projectList').append(p);}

ps.forEach(p=>{const b=document.createElement('button');b.textContent=p.name+' · '+new Date(p.updated*1000).toLocaleString();b.onclick=async()=>{try{if(state.dirty)await saveProject();await openProject(p.id);$('projectDialog').close();}catch(e){toast(e.message,true);}};$('projectList').append(b);});$('projectDialog').showModal();});

on('newProject','click',async()=>{if(state.dirty)await saveProject();Object.assign(state,{project:null,projectUpdated:null,source:null,original:null,layers:[],refs:[],selected:0,width:1024,height:1024,maskId:null,dirty:false});$('projectName').value='Untitled exploration';$('prompt').value='';$('variationCount').value='1';$('task').value='create';$('model').value='h3';$('steps').value='6';$('canvasLabel').textContent='Creative workspace';$('canvasMeta').textContent='';$('savedState').textContent='New workspace';$('download').disabled=true;$('compare').disabled=true;localStorage.removeItem('atelier-last-project');emptyMask();renderLayers();renderReferences();await renderCanvas();updateControls();$('projectDialog').close();});

on('closeProjects','click',()=>$('projectDialog').close());

async function openProject(id){const p=await api('/projects/'+id),v=p.payload;editSerial++;state.project=p.id;state.projectUpdated=p.updated;state.source=v.source;state.original=v.original;state.width=v.width||1024;state.height=v.height||1024;state.layers=v.layers||[];state.refs=v.references||[];state.selected=0;$('projectName').value=p.name;for(const [k,val]of Object.entries({model:'h3',task:'create',prompt:'',size:'1024,1024',seed:'42',steps:'6',layerCount:'2',padding:'128',strength:'.8',controlMode:'canny',feather:'0',variationCount:'1'}))$(k).value=val;for(const [k,val]of Object.entries(v.form||{}))if($(k))$(k).value=val;emptyMask();if(v.mask){const im=await image(v.mask);mctx.drawImage(im,0,0);state.maskId=v.mask;renderMask();}renderLayers();renderReferences();await renderCanvas();updateControls();state.dirty=false;$('savedState').textContent='Saved on this computer';localStorage.setItem('atelier-last-project',p.id);}

on('askVision','click',async()=>{if(!state.source)throw new Error('Add an image first.');$('askVision').disabled=true;try{state.source=(await composition()).id;dirty();const r=await post('/vision',{source:state.source,instruction:promptValue()||'Suggest a useful edit and a bounding box for its subject.'});toast('Vision queued. Use Review suggestion when complete.');await refresh();}finally{$('askVision').disabled=false;}});

on('selectObject','click',async()=>{if(!state.source)throw new Error('Add an image first.');state.source=(await composition()).id;dirty();await post('/vision',{source:state.source,kind:'segment',instruction:$('objectName').value.trim()});toast('Object selection queued.');await refresh();});

on('applyVision','click',()=>{let text=$('visionSuggestion').value;if(!text)throw new Error('Ask for a suggestion first.');try{const o=JSON.parse(text.replace(/^```json\s*|\s*```$/g,''));text=o.prompt||text;if(o.box&&state.source){const b=o.box;const vals=['x','y','width','height'].map(k=>Number(b[k]));if(vals.every(v=>Number.isFinite(v)&&v>=0&&v<=1)){emptyMask();mctx.fillStyle='white';mctx.fillRect(vals[0]*state.width,vals[1]*state.height,Math.min(vals[2],1-vals[0])*state.width,Math.min(vals[3],1-vals[1])*state.height);renderMask();}}}catch{}$('prompt').value=text;dirty();toast('Suggestion applied for review. Nothing was queued.');});

new ResizeObserver(fit).observe($('dropzone'));

window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});

async function init(){models=await api('/models');for(const m of models){const o=document.createElement('option');o.value=m.id;o.textContent=m.name;$('model').append(o);}updateControls();const v=await api('/vision');$('visionState').textContent=v.message;$('askVision').disabled=!v.available;$('selectObject').disabled=!v.segment;

const last=localStorage.getItem('atelier-last-project');if(last)try{await openProject(last);}catch{}await refresh();setInterval(refresh,3000);setInterval(async()=>{try{const [m,v]=await Promise.all([api('/models'),api('/vision')]);models=m;updateControls();$('visionState').textContent=v.message;$('askVision').disabled=!v.available;$('selectObject').disabled=!v.segment;}catch{}},30000);}

init().catch(e=>toast(e.message,true));




on('importProject','click',()=>{$('projectInput').value='';$('projectInput').click();});
on('projectInput','change',async e=>{const file=e.target.files[0];if(!file)return;if(file.size>40*1024*1024)throw new Error('Project ZIP must be at most 40 MB.');if(state.dirty)await saveProject();const f=new FormData();f.append('file',file);const p=await api('/projects/import',{method:'POST',body:f});await openProject(p.id);$('projectDialog').close();toast('Project restored as a separate copy.');});

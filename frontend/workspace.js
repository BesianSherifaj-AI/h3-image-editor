'use strict';
// Organize existing controls without replacing their event handlers or project state.
const workspaceIcons={search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',image:'M3 3h18v18H3zM3 16l5-5 4 4 3-3 6 6M8 7h.01',video:'M3 5h13v14H3zM16 9l5-3v12l-5-3',spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',link:'M10 14l4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0'};
function wsIcon(key){return `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${workspaceIcons[key]||workspaceIcons.grid}"/></svg>`;}
function wsButton(id,label,icon){const b=document.createElement('button');b.id=id;b.innerHTML=wsIcon(icon)+`<span>${label}</span>`;return b;}
function wsReveal(el){if(!el)return;for(let p=el.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;el.scrollIntoView({block:'nearest',behavior:'smooth'});el.focus();}
function wsDisclosure(title,nodes,open=false){nodes=nodes.filter(Boolean);if(!nodes.length)return;const d=document.createElement('details');d.className='ws-disclosure';d.open=open;const s=document.createElement('summary');s.textContent=title;d.append(s);nodes[0].before(d);nodes.forEach(n=>d.append(n));return d;}
const wsNav=document.createElement('nav');wsNav.className='workspace-nav';wsNav.setAttribute('aria-label','Creative workspace');
const imageTab=wsButton('workspaceImage','Image editor','image');imageTab.setAttribute('aria-current','page');wsNav.append(imageTab);
for(const [id,label,icon] of [['openDirector','AI Director','spark'],['openVideo','Video lab','video'],['openLibrary','Library','grid']]){const b=$(id);if(b){b.innerHTML=wsIcon(icon)+`<span>${label}</span>`;wsNav.append(b);}}
const searchTools=wsButton('searchTools','All tools','search');searchTools.title='Find any tool (Ctrl+K)';wsNav.append(searchTools);
const wsUtilities=document.createElement('div');wsUtilities.className='workspace-utilities';
const h3Button=wsButton('openH3Handoff','H3 video','link');wsUtilities.append(h3Button);
const modelsButton=$('openModels');modelsButton.textContent='Connections';wsUtilities.append(modelsButton);
const density=wsButton('workspaceDensity','Compact view','grid');wsUtilities.append(density);wsNav.append(wsUtilities);document.querySelector('body>header').after(wsNav);
imageTab.onclick=()=>{document.querySelectorAll('dialog[open]').forEach(d=>d.close());wsReveal($('task'));};
try{document.body.classList.toggle('compact-workspace',localStorage.getItem('atelier-density')==='compact');}catch{}
function wsDensityLabel(){density.querySelector('span').textContent=document.body.classList.contains('compact-workspace')?'Comfortable view':'Compact view';}wsDensityLabel();density.onclick=()=>{document.body.classList.toggle('compact-workspace');try{localStorage.setItem('atelier-density',document.body.classList.contains('compact-workspace')?'compact':'comfortable');}catch{}wsDensityLabel();};
document.querySelector('.brief h1').textContent='Create & edit';document.querySelector('.brief>.eyebrow').textContent='IMAGE SETTINGS';
document.querySelector('.creative-label b').textContent='Selected area';document.querySelector('.creative-label small').textContent='Paint, draw or ask AI to select';
const layerProperties=wsDisclosure('Layer position & appearance',[$('layerControls')]);
document.querySelectorAll('.inspector-section').forEach((section,i)=>wsDisclosure(i===0?'Quick edits · CPU':'Find an object · AI',[section],i===1));
wsDisclosure('Frame-by-frame tracking',[document.querySelector('.tracking')]);
const selectionMore=document.createElement('details');selectionMore.className='ws-tool-more';selectionMore.innerHTML='<summary>Selection actions ▾</summary><div class="ws-tool-menu"></div>';document.querySelector('.toolstrip').append(selectionMore);
for(const id of ['clearMask','invertMask','growSelection','refineSelection','toggleSelection'])if($(id))selectionMore.lastElementChild.append($(id));
const promptRecipes=document.querySelector('.brief .chips');wsDisclosure('Prompt recipes & starting points',[promptRecipes]);

const wsCommands=[
 ['Import an image','Start','CPU','PNG, JPG or WebP · originals stay saved','addImage'],
 ['Create a new image','Create','GPU','H3, Z-Image Turbo or Qwen Image','task:create'],
 ['Extend the canvas','Create','GPU','Outpaint beyond the existing edges','task:outpaint'],
 ['Follow a visual guide','Create','GPU','Z-Image ControlNet · edges or supplied guide','task:control'],
 ['Separate into layers','Layers','GPU','Qwen Layered · editable RGBA components','task:layers'],
 ['Remove background','Edit','GPU','Qwen Image transparency','task:cutout'],
 ['AI Director','Edit','GPU / LM','Describe the edit · select · review · bounded retry','openDirector'],
 ['Heal with AI','Edit','GPU','H3 reconstructs the selected area','aiHeal'],
 ['Remove an object','Edit','GPU','Identify the selection and reconstruct the background','aiRemove'],
 ['Replace selected content','Edit','GPU','Use your prompt and painted selection','aiReplace'],
 ['Photo adjustments','Enhance','CPU','Exposure, contrast, color, sharpen, blur and crop','openAdjust'],
 ['AI upscale · SeedVR2','Enhance','GPU','2× or 4× detail enhancement; installed model required','upscaleFactor'],
 ['Resize image','Enhance','CPU','Change dimensions without loading AI','resizeImage'],
 ['Quick patch','Edit','CPU','Small blemishes using neighboring pixels','cpuRemove'],
 ['Flat color fill','Edit','CPU','Fill only the selection with your chosen color','cpuRecolor'],
 ['Cut out selection','Layers','CPU','Transparent background from the mask','cpuCutout'],
 ['Select an object by name','Select','GPU','SAM 3.1 semantic selection','objectName'],
 ['Brush selection','Select','CPU','Paint the area to edit','tool:brush'],
 ['Rectangle selection','Select','CPU','Select a rectangular region','tool:rectangle'],
 ['Lasso & polygon','Select','CPU','Freehand or precise polygon selection','tool:lasso'],
 ['Magic wand','Select','CPU','Select similar colors with tolerance','tool:wand'],
 ['Click to select · SAM','Select','GPU','Positive and negative object points','tool:points'],
 ['Refine selection edges','Select','CPU','Grow, shrink, feather and inspect the mask','refineSelection'],
 ['Video lab','Video','CPU / GPU','Trim, track, blur, tint or H3 edit short clips','openVideo'],
 ['Send image to H3 video','Video','CPU','Copy a reference into a separate video draft','openH3Handoff'],
 ['Model connections','System','Local','Installed models, queue, unload and LM Studio','openModels'],
 ['Library','Projects','Local','Search saved images and results','openLibrary'],
 ['Version history','Projects','Local','Restore a separate copy of a saved revision','openHistory'],
 ['Saved projects','Projects','Local','Reopen or import a project ZIP','openProjects'],
 ['Export image','Export','CPU','PNG, JPEG, WebP, dimensions and quality','exportOptions'],
 ['Export editable project','Export','Local','Archive project and layers as ZIP','exportProject'],
 ['Quality review','Review','GPU','Compare before/after and ask AI for critique','reviewImage'],
 ['Keyboard help','System','Local','Selection, canvas and saving shortcuts','workspaceHelp']
];
function wsTarget(action){return action.startsWith('tool:')?document.querySelector(`[data-tool="${action.slice(5)}"]`):$(action);}
const commandDialog=document.createElement('dialog');commandDialog.id='commandDialog';commandDialog.innerHTML='<div class="ws-dialog-heading"><div><h2>Find the right tool</h2><p>Search a task, model or effect. GPU tools wait while you game.</p></div><button id="closeCommands" aria-label="Close tools">✕</button></div><label for="commandSearch">Search all tools</label><input id="commandSearch" type="search" placeholder="Try remove, upscale, select, video…" autocomplete="off"><div id="commandCategories" aria-label="Tool categories"></div><p id="commandCount" role="status"></p><div id="commandResults"></div>';document.body.append(commandDialog);
let commandCategory='All';
function wsCommandsRender(){const q=$('commandSearch').value.toLowerCase().trim();const matches=wsCommands.filter(c=>(commandCategory==='All'||c[1]===commandCategory)&&c.join(' ').toLowerCase().includes(q));$('commandCount').textContent=`${matches.length} ${matches.length===1?"tool":"tools"} · Tab to choose · Esc to close`;$('commandResults').replaceChildren();for(const c of matches){const b=document.createElement('button');b.className='command-result';b.innerHTML=`<span class="command-icon">${wsIcon(c[1]==='Video'?'video':c[1]==='Select'?'search':'spark')}</span><span><b>${escapeHTML(c[0])}</b><small>${escapeHTML(c[3])}</small></span><span class="engine-badge ${c[2]==='CPU'?'cpu':''}">${escapeHTML(c[2])}</span>`;b.onclick=async()=>{commandDialog.close();try{const action=c[4];if(action.startsWith('task:')){const task=action.slice(5);const required={control:'zimage',cutout:'qwen21',layers:'layered'}[task];if(required){$('model').value=required;$('model').dispatchEvent(new Event('change'));}$('task').value=task;$('task').dispatchEvent(new Event('change'));wsReveal($('prompt'));return;}if(action==='upscaleFactor'){await openPro('adjust');wsReveal($(action));return;}const target=wsTarget(action);if(!target)throw new Error('This tool is unavailable in the current workspace.');if(target.tagName==='BUTTON')target.click();else wsReveal(target);}catch(e){toast(e.message,true);}};$('commandResults').append(b);}}
for(const cat of ['All',...new Set(wsCommands.map(c=>c[1]))]){const b=document.createElement('button');b.textContent=cat;b.setAttribute('aria-pressed',String(cat==='All'));b.onclick=()=>{commandCategory=cat;$('commandCategories').querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));wsCommandsRender();};$('commandCategories').append(b);}
function wsOpenCommands(){if(document.querySelector('dialog[open]'))return;commandDialog.showModal();wsCommandsRender();$('commandSearch').focus();}
searchTools.onclick=wsOpenCommands;$('closeCommands').onclick=()=>commandDialog.close();$('commandSearch').oninput=wsCommandsRender;
commandDialog.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();commandDialog.close();}});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();wsOpenCommands();}});

// Reveal controls relevant to this video operation; retain user-entered values.
function wsVideoField(id,visible){const el=$(id);if(!el)return;const label=el.closest('label');if(label){label.hidden=!visible;return;}el.hidden=!visible;const ownLabel=proDialog.querySelector(`label[for="${id}"]`);if(ownLabel)ownLabel.hidden=!visible;}
const videoBudget=document.createElement('p');videoBudget.id='videoBudget';videoBudget.className='pro-context';videoBudget.setAttribute('role','status');$('runVideo').before(videoBudget);
const actionLabel=proDialog.querySelector('label[for="videoAction"]');const selectionLabel=proDialog.querySelector('label[for="videoSelection"]');selectionLabel.before(actionLabel,$('videoAction'));
function wsVideoContext(){const action=$('videoAction').value,trim=action==='trim',h3=action==='h3',paint=$('videoSelection').value==='paint';for(const id of ['videoSelection','videoTracking','videoGrow','videoFeather'])wsVideoField(id,!trim);wsVideoField('videoObject',!trim&&!paint);wsVideoField('loadVideoFrame',!trim&&paint);wsVideoField('videoColor',action==='recolor');wsVideoField('videoBlend',h3);wsVideoField('videoAttempts',h3&&$('videoReview').checked);wsVideoField('videoReview',!trim);wsVideoField('videoPrompt',!trim);const frames=Math.floor(Number($('videoDuration').value)*Number($('videoFps').value));const limit=h3?8:!trim&&$('videoTracking').value==='sam'?24:120;const gpu=h3||(!trim&&(!paint||$('videoTracking').value==='sam'||$('videoReview').checked));videoBudget.textContent=`${frames} preview frames · ${gpu?'Uses GPU; waits during gaming':'CPU only'} · limit ${limit} frames${h3?' / 3 seconds':' / 12 seconds'}. ${frames>limit?'Reduce duration or frame rate.':''}`;videoBudget.dataset.warning=String(frames>limit);$('runVideo').textContent=trim?'Trim & export preview →':h3?'Select · track · edit · review →':'Track & '+(action==='blur'?'blur':'tint')+' preview →';}
for(const id of ['videoAction','videoSelection','videoTracking','videoReview','videoDuration','videoFps'])$(id).addEventListener('input',wsVideoContext);wsVideoContext();

const runFilter=document.createElement('div');runFilter.className='ws-run-filter';runFilter.innerHTML='<label for="runFilter">Results</label><select id="runFilter"><option value="context">This workspace</option><option value="all">All runs</option><option value="active">In progress</option><option value="review">Needs review</option></select><small id="visibleRuns"></small>';$('proRuns').before(runFilter);
let wsRunLimit=6;const moreRuns=document.createElement('button');moreRuns.className='wide';moreRuns.textContent='Show older results';$('proRuns').after(moreRuns);moreRuns.onclick=()=>{wsRunLimit+=6;wsFilterRuns();};
function wsFilterRuns(){const filter=$('runFilter').value;let count=0;for(const card of $('proRuns').children){if(!card.dataset.kind)continue;const active=!['complete','needs_review','failed','cancelled'].includes(card.dataset.state);const match=filter==='all'||filter==='active'&&active||filter==='review'&&card.dataset.verdict!=='approved'&&['complete','needs_review'].includes(card.dataset.state)||filter==='context'&&(!['director','video'].includes(proState.tab)||card.dataset.kind===proState.tab);if(match)count++;card.hidden=!match||(count>wsRunLimit&&!active);}moreRuns.hidden=count<=wsRunLimit;$('visibleRuns').textContent=`${count} saved runs`;}
$('runFilter').onchange=()=>{wsRunLimit=6;wsFilterRuns();};new MutationObserver(wsFilterRuns).observe($('proRuns'),{childList:true});proDialog.querySelectorAll('[data-pro]').forEach(b=>b.addEventListener('click',()=>{wsRunLimit=6;wsFilterRuns();}));new MutationObserver(()=>{if(proDialog.open)wsFilterRuns();}).observe(proDialog,{attributes:true,attributeFilter:['open']});

const handoffDialog=document.createElement('dialog');handoffDialog.id='handoffDialog';handoffDialog.innerHTML='<div class="ws-dialog-heading"><div><h2>Continue in H3 video</h2><p>Send the current composition as a reference in a new video draft. Your image project stays here. Nothing renders automatically.</p></div><button id="closeHandoff" aria-label="Close video handoff">✕</button></div><p id="h3Status" class="pro-context">Checking the local video studio…</p><label for="handoffTitle">Video draft name</label><input id="handoffTitle" maxlength="160"><label for="handoffIdea">Describe the motion or story</label><textarea id="handoffIdea" rows="3" maxlength="20000" placeholder="A slow camera move around the subject…"></textarea><button id="sendHandoff" class="primary wide">Copy image into a new video draft</button><p id="handoffResult" role="status"></p><a id="h3OpenLink" class="pro-link" target="_blank" rel="noopener">Open H3 video studio ↗</a><p class="helper">To edit a video frame here, import its MP4 in Video lab and choose “load frame”. H3 generation and image editing keep separate queues.</p>';document.body.append(handoffDialog);
let wsHandoffRequest=null;
h3Button.onclick=async()=>{handoffDialog.showModal();$('handoffTitle').value=$('projectName').value+' · video';$('sendHandoff').disabled=!state.source;$('handoffResult').textContent=state.source?'':'Add an image to send a reference.';try{const c=await api('/workspace/connections');$('h3Status').textContent=c.h3.ready?`H3 video ${c.h3.version} · connected`:'H3 video is offline. Start its launcher, then reopen this panel.';$('sendHandoff').disabled=!c.h3.ready||!state.source;$('h3OpenLink').href=c.h3.url;}catch(e){$('h3Status').textContent=e.message;$('sendHandoff').disabled=true;}};
$('closeHandoff').onclick=()=>handoffDialog.close();on('sendHandoff','click',async()=>{const button=$('sendHandoff');button.disabled=true;try{const input=await currentInput();const spec={source:input.source,title:$('handoffTitle').value,idea:$('handoffIdea').value};const signature=JSON.stringify(spec);if(wsHandoffRequest?.signature!==signature)wsHandoffRequest={signature,id:crypto.randomUUID()};$('handoffResult').textContent='Copying reference…';const r=await post('/workspace/handoff',{...spec,request_id:wsHandoffRequest.id});$('handoffResult').textContent=`Saved “${r.title}” in H3 video. Use the draft link below to continue.`;$('h3OpenLink').href=r.url;$('h3OpenLink').textContent='Open this video draft ↗';toast('Video draft saved. No GPU job was started.');}finally{button.disabled=false;}});

document.querySelectorAll('.tabs [data-mode]').forEach(b=>{const sync=()=>b.setAttribute('aria-selected',String(b.classList.contains('selected')));sync();new MutationObserver(sync).observe(b,{attributes:true,attributeFilter:['class']});});
document.querySelectorAll('.pro-nav [data-pro]').forEach(b=>{const sync=()=>b.setAttribute('aria-current',b.classList.contains('selected')?'page':'false');sync();new MutationObserver(sync).observe(b,{attributes:true,attributeFilter:['class']});});

const wsHealth=document.createElement('span');wsHealth.className='workspace-health';wsHealth.setAttribute('role','status');wsUtilities.prepend(wsHealth);document.addEventListener('studio-health',e=>{const h=e.detail;wsHealth.textContent=h.cpu_only?'CPU mode':h.status.game?'Gaming · GPU waiting':h.paused?'GPU queue paused':'Local workspace';wsHealth.title=h.cpu_only?'GPU rendering is disabled in this environment.':h.status.game?'Your game is protected. CPU editing is available.':h.status.message;});

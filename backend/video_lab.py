"""Small, inspectable video previews; no claims of temporally coherent diffusion."""
import hashlib,json,shutil,subprocess,time,uuid,zipfile
from pathlib import Path
from typing import Literal
import numpy as np
from PIL import Image,ImageDraw,ImageFilter,ImageOps
from fastapi import APIRouter,UploadFile,File
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from .context import app as a,pro as p
from .config import PROCESS_FLAGS
router=APIRouter()

def command(args,timeout=90):
    result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,creationflags=PROCESS_FLAGS)
    if result.returncode:raise ValueError('Video processing failed: '+result.stderr[-1200:])
    return result.stdout

def read(id):
    with a.db() as c:r=c.execute('SELECT metadata FROM videos WHERE id=?',(a.uid(id),)).fetchone()
    if not r:raise ValueError('Video not found.')
    return json.loads(r[0])

def path(id):return a.DATA/'videos'/(a.uid(id)+'.media')
def ffmpeg():
    exe=shutil.which('ffmpeg')
    if not exe:raise ValueError('FFmpeg is not installed.')
    return [exe,'-hide_banner','-loglevel','error','-nostdin','-y','-threads','2','-filter_threads','1']

def scale():return "scale=w='min(768,iw)':h='min(768,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"

@router.get('/videos')
def videos():
    with a.db() as c:return [json.loads(r[0]) for r in c.execute('SELECT metadata FROM videos ORDER BY rowid DESC LIMIT 40')]

@router.post('/videos')
async def upload(file:UploadFile=File(...)):
    id=str(uuid.uuid4());target=path(id);size=0
    try:
        with target.open('wb') as f:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>40*1024*1024:raise ValueError('Use a video up to 40 MB. Trim a longer recording first.')
                f.write(chunk)
        # Never follow playlists, manifests or network references from uploaded files.
        raw=json.loads(command([shutil.which('ffprobe') or 'ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,avi','-show_streams','-show_format','-of','json',str(target)],20))
        streams=[s for s in raw['streams'] if s['codec_type']=='video']
        if not streams:raise ValueError('No video stream found.')
        stream=streams[0];duration=float(raw['format'].get('duration',0));fmt=raw['format'].get('format_name','')
        if not any(k in fmt for k in ('mov','mp4','matroska','webm','avi')):raise ValueError('Use MP4, MOV, WebM or AVI.')
        if not 0<duration<=600 or stream['width']*stream['height']>33_200_000:raise ValueError('Use a clip up to 10 minutes and 8K.')
        meta=dict(id=id,name=Path(file.filename or 'Video').name[:150],duration=duration,width=stream['width'],height=stream['height'],audio=any(s['codec_type']=='audio' for s in raw['streams']),previews={})
        with a.db() as c:c.execute('INSERT INTO videos VALUES (?,?)',(id,json.dumps(meta)))
        return meta
    except Exception:
        target.unlink(missing_ok=True);raise

@router.get('/videos/{id}/frame')
def frame(id:str,start:float=0):
    meta=read(id)
    if not 0<=start<meta['duration']:raise ValueError('Start must be inside the video.')
    folder=a.DATA/'videos'/a.uid(id);folder.mkdir(exist_ok=True);output=folder/(str(uuid.uuid4())+'.png')
    command(ffmpeg()+['-protocol_whitelist','file,pipe','-i',str(path(id)),'-ss',str(start),'-frames:v','1','-vf',scale(),str(output)])
    asset=a.put_image(Image.open(output).convert('RGBA'),'Video selection frame');output.unlink()
    meta['previews'][asset['id']]=start
    with a.db() as c:c.execute('UPDATE videos SET metadata=? WHERE id=?',(json.dumps(meta),id))
    return asset|{'start':start,'video':id}

class VideoSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    video:str
    source:str
    mask:str|None=None
    start:float=Field(default=0,ge=0,le=600)
    duration:float=Field(default=2,ge=.5,le=12)
    fps:int=Field(default=4,ge=1,le=12)
    action:Literal['trim','blur','recolor','h3']='blur'
    color:str=Field(default='#607dff',pattern=r'^#[0-9a-fA-F]{6}$')
    prompt:str=Field(default='',max_length=1800)
    audio:bool=True
    mask_grow:int=Field(default=6,ge=0,le=16)
    feather:int=Field(default=2,ge=0,le=32)
    tracking:Literal['flow','sam']='flow'
    object:str=Field(default='',max_length=140)
    auto_review:bool=True
    max_attempts:int=Field(default=1,ge=1,le=2)
    seed:int=Field(default=42,ge=0,le=9007199254740991)
    blend_edges:bool=True

@router.post('/video-runs')
def submit(s:VideoSpec):
    if s.seed+s.max_attempts-1>9007199254740991:raise ValueError('Choose a lower seed to leave room for the corrected attempt.')
    meta=read(s.video)
    if s.source not in meta['previews'] or abs(meta['previews'][s.source]-s.start)>.001:raise ValueError('Load the selection frame for this start time first.')
    if s.start+s.duration>meta['duration']+.01:raise ValueError('Selected range exceeds the clip duration.')
    if s.duration*s.fps>120:raise ValueError('Use at most 120 frames for a CPU preview.')
    if int(s.duration*s.fps)<1:raise ValueError('Choose a range and frame rate containing at least one frame.')
    if s.tracking=='sam' and s.action!='trim':
        if s.duration*s.fps>24:raise ValueError('SAM tracking supports up to 24 frames per preview.')
        if not a.helpers.available()['segment']:raise ValueError('SAM 3.1 is not installed.')
    if s.action=='h3' and (s.duration*s.fps>8 or s.duration>3):raise ValueError('AI preview supports up to 8 frames and 3 seconds. Start with 2 seconds at 4 fps.')
    if s.action!='trim':
        if not s.mask:
            if not s.object.strip() and not s.prompt.strip():raise ValueError('Name the object or describe the edit, or paint a selection.')
            if not a.helpers.available()['segment']:raise ValueError('SAM 3.1 is required for automatic object selection.')
        elif a.load(s.mask).size!=a.load(s.source).size or a.load(s.mask).convert('L').getextrema()[1]==0:raise ValueError('Use a nonempty selection matching the first frame.')
    if s.action!='trim' and (s.auto_review or (not s.mask and not s.object.strip())) and not a.helpers.available()['vision']:raise ValueError('Built-in vision is required for automatic planning or review.')
    if s.action=='h3' and len(s.prompt.strip())<3:raise ValueError('Describe the AI video edit.')
    return p.new_run('video',s.model_dump())

def cancelled(run):return p.read_run(run['id'])['state']=='cancelled' or a.STOP.is_set()

def selection_masks(run,raw_masks):
    """Track the object itself; expand each resulting mask, not the SAM seed."""
    s=run['payload'];s['raw_masks']=raw_masks
    s['mask_coverage']=[float(np.mean(np.array(a.load(m).convert('L'))>127)) for m in raw_masks]
    coverage=s['mask_coverage']
    if any(c<.001 or c>.92 for c in coverage):
        s['message']='Tracking lost the object or selected almost the whole frame. Inspect the masks and use a shorter clip or a clearer selection.';p.save(run,'needs_review');return False
    if any(max(x,y)/max(min(x,y),.00001)>2.5 for x,y in zip(coverage,coverage[1:])):
        s['message']='The tracked area changed abruptly. Inspect the selection before editing; try SAM tracking or a shorter range.';p.save(run,'needs_review');return False
    s['masks']=[]
    for mid in raw_masks:
        mask=a.load(mid).convert('L')
        if s.get('mask_grow'):mask=mask.filter(ImageFilter.MaxFilter(s['mask_grow']*2+1))
        s['masks'].append(a.put_image(mask,'Video edit selection with edge margin')['id'])
    return True

def frame_checks(s):
    checks=[]
    for i,(source,out) in enumerate(zip(s['frames'],s['outputs'])):
        if s['action']=='trim':
            ok=np.array_equal(np.array(a.load(source)),np.array(a.load(out['id'])))
            checks.append({'pass':ok,'frame':i+1});continue
        check=p.protected_metric(source,out['id'],s['masks'][i]);check['frame']=i+1
        x=np.array(a.load(source));y=np.array(a.load(out['id']));selected=np.array(a.load(s['masks'][i]).convert('L'))>0
        check['changed_selected_pixels']=int(np.any(x!=y,axis=2)[selected].sum()) if x.shape==y.shape else 0
        checks.append(check)
    return {'pass':bool(checks) and len(checks)==len(s['frames']) and all(c['pass'] for c in checks),'frames':checks,'scope':'Exact pixels in lossless frames before MP4 compression'}

def temporal_checks(s):
    """Flag changes introduced by frame edits after accounting for source motion.

    This heuristic is evidence for inspection, not proof of temporal coherence.
    """
    import cv2
    values=[]
    for i in range(1,len(s['frames'])):
        before0=np.array(a.load(s['frames'][i-1]).convert('RGB'));before1=np.array(a.load(s['frames'][i]).convert('RGB'))
        after0=np.array(a.load(s['outputs'][i-1]['id']).convert('RGB'));after1=np.array(a.load(s['outputs'][i]['id']).convert('RGB'))
        flow=cv2.calcOpticalFlowFarneback(cv2.cvtColor(before1,cv2.COLOR_RGB2GRAY),cv2.cvtColor(before0,cv2.COLOR_RGB2GRAY),None,.5,3,19,3,5,1.2,0)
        yy,xx=np.mgrid[:before1.shape[0],:before1.shape[1]].astype(np.float32);mx=xx+flow[:,:,0];my=yy+flow[:,:,1]
        warp=lambda im:cv2.remap(im,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        prevmask=warp(np.array(a.load(s['masks'][i-1]).convert('L')))>240
        region=(np.array(a.load(s['masks'][i]).convert('L'))>240)&prevmask
        region=cv2.erode(region.astype(np.uint8),np.ones((7,7),np.uint8))>0
        # Compare the change fields rather than raw video frames, so expected camera motion is not called flicker.
        delta1=after1.astype(np.float32)-before1.astype(np.float32)
        delta0=warp(after0.astype(np.float32)-before0.astype(np.float32))
        error=float(np.abs(delta1-delta0)[region].mean()) if region.any() else None
        values.append({'pair':[i,i+1],'edit_change_error':round(error,3) if error is not None else None,'pixels':int(region.sum())})
    warning=any(v['edit_change_error'] is None or v['edit_change_error']>12 for v in values)
    return {'warning':warning,'pairs':values,'scope':'Motion-aligned edit variation heuristic, 0–255 scale; not a motion-quality guarantee'}

def review_prompt(s):
    return ('Review these video samples: ORIGINAL row 1, EDITED row 2. If there are 4 rows, rows 3 and 4 enlarge the same original and edited object boundaries. Requested edit: '+(s.get('prompt') or s['action'])+
        '. Compare each column and the object appearance across columns. Look for unchanged objects, old-color rims, halos, changed shapes and inconsistent color. '
        'Return ONLY JSON with string keys verdict (pass, retry, uncertain), reason, correction. Use retry only for a visible correctable defect, uncertain if detail is too small. '
        'You cannot assess continuous motion or audio from still frames. Do not claim that you did. Do not invent changes to unrelated objects.')

def finish_review(run,review):
    s=run['payload'];s.setdefault('reviews',[]).append(review)
    if s.get('temporal_checks',{}).get('warning') and review['verdict']=='pass':
        review.update(verdict='retry',reason=review['reason']+' However, the independent motion-aligned check found inconsistent frame edits.',correction='Keep the edited object appearance consistent across frames. Preserve the original texture, silhouette and lighting, with a uniform target color and no halo.')
    s['attempts'][-1]['review']=review
    if review['verdict']=='retry' and s['action']=='h3' and s.get('attempt',1)<s.get('max_attempts',1) and review['correction'].strip():
        s['correction']=review['correction'];s['attempt']=s.get('attempt',1)+1;s['outputs']=[];s['index']=0
        s['message']='AI found a visible defect. Trying one corrected version from the original frames; the first preview is retained.';p.save(run,'processing');return
    s['message']='AI sample review: '+review['reason']+' Play the preview to check motion and sound. Lossless pixel checks are listed below.'
    p.save(run,'complete' if review['verdict']=='pass' else 'needs_review')

def tick(run):
    s=run['payload'];folder=a.DATA/'videos'/run['id'];folder.mkdir(exist_ok=True)
    if run['state']=='new':
        if a.game_running() and (s['action']=='h3' or s.get('tracking')=='sam' or (not s.get('mask') and s['action']!='trim')):return
        if s['action']!='trim' and not s.get('mask'):
            if s.get('object','').strip():
                p.child(run,dict(model='segment',task='segment',source=s['source'],prompt=s['object'].strip(),seed=0),'selecting')
            else:
                prompt=p.plan_prompt(dict(instruction=s['prompt'],object=''))
                p.vision_child(run,s['source'],prompt,'planning',p.PLAN_SCHEMA)
            return
        command(ffmpeg()+['-protocol_whitelist','file,pipe','-i',str(path(s['video'])),'-ss',str(s['start']),'-t',str(s['duration']),'-vf',scale()+',fps='+str(s['fps']),'-frames:v',str(int(s['duration']*s['fps'])) ,str(folder/'source-%04d.png')])
        files=sorted(folder.glob('source-*.png'))
        if not files:raise ValueError('No frames were decoded.')
        s['frames']=[];s['masks']=[];previous=None;m=a.load(s['mask']).convert('L') if s.get('mask') else None;drift=[]
        for f in files:
            if cancelled(run):return
            im=Image.open(f).convert('RGBA')
            if im.size!=a.load(s['source']).size:raise ValueError('Decoded frame dimensions changed.')
            s['frames'].append(a.put_image(im,'Video frame')['id'])
            if m is not None:
                if previous is not None and s.get('tracking','flow')=='flow':
                    tracked,metrics=a.imaging.track([previous,im],m);m=tracked[-1];drift.append(metrics)
                s['masks'].append(a.put_image(m,'Tracked video selection')['id'])
            previous=im
        s['tracking_metrics']=drift;s['index']=0;s['outputs']=[];s['attempt']=1
        if s.get('tracking')=='sam' and s['action']!='trim' and len(s['frames'])>1:
            s['message']='SAM 3.1 tracking queued. Review boundaries and occlusions in the preview.'
            p.child(run,dict(model='samtrack',task='track',prompt='Track selected object',source=s['frames'][0],frames=s['frames'],mask=s['masks'][0],seed=0),'tracking');return
        if s['action']!='trim' and not selection_masks(run,s['masks']):return
        s['message']='Frames prepared. Review the tracked selection before export.';p.save(run,'processing');return
    if run['state'] in ('planning','selecting','reviewing'):
        stage=run['state'];j=a.get_job(s['child'])
        if j['state'] not in a.TERMINAL:return
        if j['state']!='complete':
            s['message']='Automatic '+stage+' '+j['state']+': '+j['message'];p.save(run,'needs_review');return
        if stage=='planning':
            try:s['plan']=p.parse_json(j['outputs'][0]['text'],p.PLAN_SCHEMA)
            except (ValueError,KeyError):s['message']='AI could not identify the object. Name it explicitly or paint a selection.';p.save(run,'needs_review');return
            obj=s['plan']['object'].strip()
            if not obj or len(obj)>140 or len(obj.split())>20:s['message']='AI could not name a precise object. Enter its name or paint a selection.';p.save(run,'needs_review');return
            s['object']=obj;p.save(run,'new');return
        if stage=='selecting':
            mask=j['outputs'][0]['id'];coverage=float(np.mean(np.array(a.load(mask).convert('L'))>127))
            if coverage<.001 or coverage>.92:s['message']='Automatic selection is empty or too broad. Name a specific object or paint a mask.';p.save(run,'needs_review');return
            s['mask']=mask;s['automatic_selection']=True;p.save(run,'new');return
        try:review=p.parse_json(j['outputs'][0]['text'],p.REVIEW_SCHEMA)
        except (ValueError,KeyError):review={'verdict':'uncertain','reason':'The AI review was malformed. The preview is saved for your inspection.','correction':''}
        finish_review(run,review);return
    if run['state']=='tracking':
        j=a.get_job(s['child'])
        if j['state'] not in a.TERMINAL:return
        if j['state']!='complete':raise ValueError('SAM tracking failed: '+j['message'])
        if len(j['outputs'])!=len(s['frames']):raise ValueError('SAM returned the wrong number of tracking masks.')
        s['masks']=[x['id'] for x in j['outputs']]
        if any(a.load(m).size!=a.load(s['source']).size for m in s['masks']):raise ValueError('Tracking mask dimensions changed.')
        if not selection_masks(run,s['masks']):return
        s['message']='SAM tracking finished. Inspect every sampled frame for drift.';p.save(run,'processing');return
    if run['state']=='editing':
        j=a.get_job(s['child'])
        if j['state'] not in a.TERMINAL:return
        if j['state']!='complete':raise ValueError('Video frame failed: '+j['message'])
        result=j['outputs'][0]
        if s.get('blend_edges'):result=p.blend_result(s['frames'][s['index']],result,s['masks'][s['index']],s.get('feather',2))
        s['outputs'].append(result);s['index']+=1;p.save(run,'processing');return
    if run['state']=='processing':
        i=s['index']
        if i<len(s['frames']):
            if s['action']=='h3':
                prompt=s['prompt']+' Preserve all unrequested content, shape, lighting and composition.'
                if s.get('object'):prompt+=' The selected object is '+s['object']+'.'
                if s.get('correction'):prompt+=' '+s['correction']
                spec=a.validate_job(a.JobSpec(model='h3',task='edit',source=s['frames'][i],mask=s['masks'][i],prompt=prompt,seed=s.get('seed',42)+s.get('attempt',1)-1,feather=s.get('feather',2)));p.child(run,spec,'editing');return
            # One frame per tick keeps CPU use and cancellation latency bounded.
            im=a.load(s['frames'][i]);out=im
            effective_mask=a.imaging.feather_mask(a.load(s['masks'][i]),s.get('feather',0)) if s['action']!='trim' else None
            if s['action']=='blur':out=a.imaging.composite_mask(im,im.filter(ImageFilter.GaussianBlur(16)),effective_mask)
            if s['action']=='recolor':
                rgb=Image.new('RGBA',im.size,s['color']);rgb=Image.blend(im,rgb,.6);out=a.imaging.composite_mask(im,rgb,effective_mask)
            s['outputs'].append(a.put_image(out,'Video processed frame'));s['index']+=1;p.save(run);return
        s['quality_checks']=frame_checks(s)
        if not s['quality_checks']['pass']:s['message']='Lossless protected-pixel verification failed. Export is stopped; the original frames are retained.';p.save(run,'needs_review');return
        if s['action']!='trim':s['temporal_checks']=temporal_checks(s)
        attempt=s.get('attempt',1)
        for i,asset in enumerate(s['outputs']):a.load(asset['id']).convert('RGB').save(folder/f'output-{i+1:04d}.png')
        if cancelled(run):return
        out=folder/f'preview-{attempt}.mp4';args=ffmpeg()+['-framerate',str(s['fps']),'-i',str(folder/'output-%04d.png')]
        if s['audio']:args+=['-protocol_whitelist','file,pipe','-ss',str(s['start']),'-i',str(path(s['video'])),'-map','0:v:0','-map','1:a:0?']
        args+=['-t',str(len(s['outputs'])/s['fps']),'-c:v','libx264','-threads','2','-preset','fast','-crf','18','-pix_fmt','yuv420p']
        if s['audio']:args+=['-c:a','aac','-b:a','128k']
        args+=['-movflags','+faststart',str(out)];command(args)
        command(ffmpeg()+['-xerror','-protocol_whitelist','file,pipe','-i',str(out),'-map','0:v:0','-map','0:a:0?','-f','null','-'])
        s['decode_verified']=True
        if cancelled(run):return
        cols=min(4,len(s['outputs']));rows=4 if s.get('masks') else 2;sheet=Image.new('RGB',(cols*256,rows*276),'#18191d');d=ImageDraw.Draw(sheet)
        indices=np.linspace(0,len(s['outputs'])-1,cols).astype(int).tolist()
        for col,index in enumerate(indices):
            for row,asset in enumerate((s['frames'][index],s['outputs'][index]['id'])):
                im=a.load(asset);im.thumbnail((256,256));sheet.paste(im,(col*256,row*276+20));d.text((col*256+5,row*276+4),('Original ' if row==0 else 'Edited ')+str(index+1),fill='white')
                if rows==4:
                    full=a.load(asset);box=a.load(s['masks'][index]).convert('L').getbbox();x0,y0,x1,y1=box;margin=max(12,round(max(x1-x0,y1-y0)*.15))
                    detail=ImageOps.contain(full.crop((max(0,x0-margin),max(0,y0-margin),min(full.width,x1+margin),min(full.height,y1+margin))),(256,256));sheet.paste(detail,(col*256,(row+2)*276+20));d.text((col*256+5,(row+2)*276+4),('Original detail ' if row==0 else 'Edited detail ')+str(index+1),fill='white')
        s['comparison']=a.put_image(sheet,'Video before / after contact sheet');s['url']='/api/pro/runs/'+run['id']+'/video?attempt='+str(attempt)
        s.setdefault('attempts',[]).append({'number':attempt,'outputs':s['outputs'],'comparison':s['comparison'],'url':s['url'],'checks':s['quality_checks'],'temporal_checks':s.get('temporal_checks'),'correction':s.get('correction','')})
        if s.get('auto_review',False) and s['action']!='trim':
            s['message']='Preview rendered. AI is checking sampled frames for visible defects.';p.vision_child(run,s['comparison']['id'],review_prompt(s),'reviewing',p.REVIEW_SCHEMA);return
        s['message']='Preview saved. Review object tracking and motion; independent H3 frame edits can flicker.';p.save(run,'complete')

@router.get('/runs/{id}/video')
def download(id:str,attempt:int=0):
    run=p.read_run(id);s=run['payload'];number=attempt or s.get('attempt',0)
    valid=[x['number'] for x in s.get('attempts',[])]
    if number and number not in valid:raise ValueError('This attempt is not ready.')
    target=a.DATA/'videos'/a.uid(id)/(f'preview-{number}.mp4' if number else 'preview.mp4')
    if run['kind']!='video' or not s.get('url') or not target.is_file():raise ValueError('Video preview is not ready.')
    return FileResponse(target,media_type='video/mp4',filename='atelier-preview.mp4')

@router.get('/runs/{id}/frames')
def export_frames(id:str,attempt:int=0):
    run=p.read_run(id);s=run['payload']
    saved=next((x for x in reversed(s.get('attempts',[])) if not attempt or x['number']==attempt),None)
    if attempt and not saved:raise ValueError('This attempt is not ready.')
    if saved:s=s|{'outputs':saved['outputs'],'attempt':saved['number'],'quality_checks':saved['checks'],'temporal_checks':saved.get('temporal_checks'),'reviews':[saved['review']] if saved.get('review') else []}
    if run['kind']!='video' or (not saved and run['state'] not in ('complete','needs_review')) or not s.get('outputs') or len(s['outputs'])!=len(s.get('frames',[])):raise ValueError('Finish the preview before exporting frames.')
    manifest={k:s.get(k) for k in ('video','start','duration','fps','action','prompt','object','tracking','blend_edges','mask_coverage','tracking_metrics','quality_checks','temporal_checks','decode_verified','reviews','attempt')}
    manifest['frames']=len(s['frames']);manifest['assets']={'original':s['frames'],'edited':[x['id'] for x in s['outputs']],'masks':s['masks']}
    if all(x.get('unblended_asset') for x in s['outputs']):manifest['assets']['before-edge-blend']=[x['unblended_asset'] for x in s['outputs']]
    snapshot=json.dumps(manifest,indent=2);stamp=hashlib.sha256(snapshot.encode()).hexdigest()[:12]
    folder=a.DATA/'videos'/a.uid(id);target=folder/(f'frames-and-masks-{s.get("attempt",0)}-{stamp}.zip')
    if not target.exists():
        temp=folder/(str(uuid.uuid4())+'.zip')
        try:
            with zipfile.ZipFile(temp,'w',zipfile.ZIP_STORED) as z:
                z.writestr('manifest.json',snapshot)
                for kind,ids in manifest['assets'].items():
                    for i,asset in enumerate(ids):z.write(a.asset_path(asset),f'{kind}/{i+1:04d}.png')
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)
    return FileResponse(target,media_type='application/zip',filename='atelier-frames-and-masks.zip')

@router.post('/runs/{id}/video-review')
def review(id:str):
    with a.LOCK:
        run=p.read_run(id);s=run['payload']
        if run['kind']!='video' or not s.get('comparison'):raise ValueError('Finish a video preview first.')
        if s.get('frame_review_job'):return a.get_job(s['frame_review_job'])
        if not a.helpers.available()['vision']:raise ValueError('Built-in vision model is missing.')
        prompt='Critique these sampled video frames. Rows 1 and 3 (if present) show originals; rows 2 and 4 show edits. Describe visible tracking drift, boundary artifacts, shape damage and differences across frames. Be critical; if a detail is too small to judge, say so. You cannot assess smooth motion or audio from still frames. Do not propose another edit unless a visible defect needs correction. Requested edit: '+(s.get('prompt') or s['action'])
        job=str(uuid.uuid4());now=time.time()
        spec=dict(model='vision',task='vision',source=s['comparison']['id'],prompt=prompt,_director_prompt=prompt,_video_review=run['id'],seed=0)
        s['frame_review_job']=job
        # Persist the review and its parent link together; repeated clicks reuse it.
        with a.db() as c:
            if c.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled')").fetchone()[0]>=30:raise ValueError('Queue is full.')
            c.execute('INSERT INTO jobs(id,spec,state,message,created,updated) VALUES (?,?,?,?,?,?)',(job,json.dumps(spec),'queued','Video frame critique queued',now,now))
            c.execute('UPDATE creative_runs SET payload=?,updated=? WHERE id=?',(json.dumps(s),now,run['id']))
        return a.get_job(job)

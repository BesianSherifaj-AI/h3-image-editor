"""Loopback studio with durable, at-most-once GPU queue."""
import os
os.environ.setdefault("OMP_NUM_THREADS","2")
os.environ.setdefault("MKL_NUM_THREADS","2")
import asyncio,base64,contextlib,hashlib,io,json,logging,sqlite3,subprocess,threading,time,uuid,zipfile
from pathlib import Path
from typing import Literal
import httpx,psutil
from PIL import Image
from fastapi import FastAPI,Request,HTTPException,UploadFile,File
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict
from . import imaging,workflows,helpers,schemas,config

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.getenv("STUDIO_DATA",str(ROOT/"data")))
ASSETS=DATA/"assets";RUNTIME=ROOT/"runtime"
for p in [DATA,ASSETS,RUNTIME/"input",RUNTIME/"output",RUNTIME/"user",ROOT/"logs",DATA/"receipts"]:p.mkdir(parents=True,exist_ok=True)
DB=DATA/"studio.sqlite"
COMFY=config.COMFY
PYTHON=config.PYTHON
COMFY_SRC=config.COMFY_SRC
LOCK=threading.RLock()
GPU_LOCK=threading.Lock()
STOP=threading.Event()
ACTIVE={"preparing","submitting","submitted","running","cancelling","uncertain"}
TERMINAL={"complete","failed","cancelled"}
status={"message":"Ready","game":False,"runtime":False}
logger=logging.getLogger("studio")
@contextlib.contextmanager
def db():
    c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        with c:yield c
    finally:c.close()
def init_db():
    with db() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,name TEXT, width INTEGER,height INTEGER,sha256 TEXT,created REAL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,spec TEXT,state TEXT,message TEXT,prompt_id TEXT,outputs TEXT DEFAULT '[]',created REAL,updated REAL,cancel INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,payload TEXT,updated REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
""")
        from . import studio_plus
        studio_plus.init(c)
def setting(key,default=None):
    with db() as c:r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return json.loads(r[0]) if r else default
def set_setting(key,value):
    with db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",(key,json.dumps(value)))
def uid(value):
    try:return str(uuid.UUID(value))
    except Exception:raise ValueError("Invalid file or project identifier.")
def asset_path(a):
    p=ASSETS/(uid(a)+".png")
    if not p.is_file():raise ValueError("Image was not found.")
    return p
def load(a):return Image.open(asset_path(a)).convert("RGBA")
def put_image(im,name="Image"):
    a=str(uuid.uuid4());p=ASSETS/(a+".png");im.save(p,"PNG")
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    with db() as c:c.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)",(a,str(name)[:150],im.width,im.height,sha,time.time()))
    return dict(id=a,name=name,width=im.width,height=im.height,sha256=sha,url="/api/assets/"+a)
def asset_info(a):
    with db() as c:r=c.execute("SELECT * FROM assets WHERE id=?",(uid(a),)).fetchone()
    if not r:raise ValueError("Image was not found.")
    return dict(r)|{"url":"/api/assets/"+a}
def update(job_id,**values):
    values["updated"]=time.time()
    with db() as c:c.execute("UPDATE jobs SET "+",".join(k+"=?" for k in values)+" WHERE id=?",[*values.values(),job_id])
def jobdict(r):
    d=dict(r);d["spec"]=json.loads(d["spec"]);d["outputs"]=json.loads(d["outputs"]);return d
def get_job(j):
    with db() as c:r=c.execute("SELECT * FROM jobs WHERE id=?",(uid(j),)).fetchone()
    if not r:raise ValueError("Job not found.")
    return jobdict(r)
def game_running():
    try:return any(any(game in (p.info["name"] or "").lower() for game in ("fortnite","rocketleague")) for p in psutil.process_iter(["name"]))
    except (psutil.Error,OSError):return True # fail closed
def api(method,path,**kwargs):
    with httpx.Client(timeout=kwargs.pop("timeout",8),trust_env=False) as c:
        r=c.request(method,COMFY+path,**kwargs);r.raise_for_status()
        return r.json() if r.content else {}
def runtime_owned():
    """Require a live listener with this studio's private output directory."""
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status=="LISTEN" and conn.laddr.port==config.COMFY_PORT:
                cmd=" ".join(psutil.Process(conn.pid).cmdline()).replace("\\","/").lower()
                return str(RUNTIME/"output").replace("\\","/").lower() in cmd and "main.py" in cmd
    except (psutil.Error,OSError):return False
    return False
def runtime_ready():
    if not runtime_owned():return False
    try:api("GET","/system_stats",timeout=2);return True
    except Exception:return False
def foreign_busy():
    for port in (8000,8188,8010,8012):
        try:
            with httpx.Client(timeout=1,trust_env=False) as c:r=c.get(f"http://127.0.0.1:{port}/queue")
            if r.status_code==200:
                q=r.json()
                if q.get("queue_running") or q.get("queue_pending"):return True
        except Exception:pass
    return False
def start_runtime():
    if config.CPU_ONLY:raise RuntimeError("CPU-only mode is enabled. GPU jobs are disabled.")
    if runtime_ready():return
    if game_running():raise RuntimeError("A game is running; GPU work is deferred.")
    # Refuse a foreign listener even if it presents a Comfy API.
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status=="LISTEN" and conn.laddr.port==config.COMFY_PORT:
            if runtime_owned():raise RuntimeError("Studio renderer is starting; try shortly.")
            raise RuntimeError("Port 8014 belongs to another service.")
    env=os.environ.copy();env.update(HF_HUB_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1",PYTHONUTF8="1")
    args=[str(PYTHON),"-s",str(COMFY_SRC/"main.py"),"--listen","127.0.0.1","--port",str(config.COMFY_PORT),"--disable-auto-launch","--base-directory",str(RUNTIME),"--user-directory",str(RUNTIME/"user"),"--input-directory",str(RUNTIME/"input"),"--output-directory",str(RUNTIME/"output"),"--extra-model-paths-config",str(config.model_paths_file()),"--preview-method","none","--disable-all-custom-nodes","--offline","--cache-none","--reserve-vram","3","--disable-dynamic-vram"]
    proc=subprocess.Popen(args,cwd=COMFY_SRC,env=env,stdout=open(ROOT/"logs"/"comfy.stdout.log","a"),stderr=open(ROOT/"logs"/"comfy.stderr.log","a"),creationflags=config.PROCESS_FLAGS)
    (RUNTIME/"launch.json").write_text(json.dumps({"pid":proc.pid,"args":args,"time":time.time()},indent=2))
    for _ in range(90):
        if STOP.wait(1):raise RuntimeError("Studio is shutting down.")
        if runtime_ready():return
        if proc.poll() is not None:raise RuntimeError("Renderer startup failed. See logs/comfy.stderr.log.")
    raise RuntimeError("Renderer startup is taking longer than expected.")
def own_queue(q):
    ids=[x[1] for x in q.get("queue_running",[])+q.get("queue_pending",[])]
    with db() as c:known={r[0] for r in c.execute("SELECT prompt_id FROM jobs WHERE prompt_id IS NOT NULL")}
    return all(i in known for i in ids)
def release_idle():
    if runtime_ready():
        q=api("GET","/queue")
        if not q.get("queue_running") and not q.get("queue_pending"):
            api("POST","/free",json={"unload_models":True,"free_memory":True})
def prepare(spec,job_id):
    source=mask=control=None;refs=[]
    if spec['model'] in ('vision','segment','samtrack','upscale'):
        im=load(spec['source']);original_size=im.size;im.thumbnail((1024,1024))
        source=f'{job_id}-source.png';im.save(RUNTIME/'input'/source)
        if spec['model']=='samtrack':
            names=[]
            for i,asset in enumerate(spec['frames']):
                name=f'{job_id}-frame{i}.png';load(asset).resize(im.size).save(RUNTIME/'input'/name);names.append(name)
            name=f'{job_id}-mask.png';load(spec['mask']).resize(im.size,Image.Resampling.NEAREST).save(RUNTIME/'input'/name)
            spec['_frames']=names;spec['_maskfile']=name;source=names[0]
        prepared=spec.copy()
        if spec['model']=='segment' and spec.get('positive_points'):
            for key in ('positive_points','negative_points'):
                prepared[key]=[{'x':min(im.width-1,round(p['x']*im.width/original_size[0])),'y':min(im.height-1,round(p['y']*im.height/original_size[1]))} for p in spec.get(key,[])]
        return helpers.build(prepared,source,job_id)
    if spec.get("source"):
        im=load(spec["source"])
        m=load(spec["mask"]).convert("L") if spec.get("mask") else None
        if m is not None and m.size!=im.size:raise ValueError("Mask dimensions do not match the source.")
        if spec["task"]=="outpaint":
            im,m=imaging.outpaint(im,spec["padding"])
        if m is not None:m=imaging.feather_mask(m,spec.get('feather',0))
        if spec["model"]=="layered":
            im.thumbnail((640,640))
        w=max(32,round(im.width/32)*32);h=max(32,round(im.height/32)*32)
        if max(w,h)>2048:raise ValueError("Resize the image to 2048 pixels or less before AI editing.")
        # Keep exact original dimensions for final protected composite.
        original=put_image(im,"Protected original") if spec["task"]=="outpaint" else asset_info(spec["source"])
        spec["_original"]=original["id"]
        if m is not None:
            spec["_mask"]=put_image(m,"Protected mask")["id"]
        if spec['model']=='h3' and m is not None and spec.get('_heal') and spec['task']!='outpaint':
            # Give local repairs enough pixels and context; stitch back before compositing.
            box=m.getbbox()
            if box:
                x0,y0,x1,y1=box;pad=max(64,round(max(x1-x0,y1-y0)*.35))
                cx=(x0+x1)//2;cy=(y0+y1)//2;cw=min(im.width,max(256,x1-x0+pad*2));ch=min(im.height,max(256,y1-y0+pad*2))
                left=max(0,min(im.width-cw,cx-cw//2));top=max(0,min(im.height-ch,cy-ch//2));crop=(left,top,left+cw,top+ch)
                if cw*ch<im.width*im.height*.85:
                    spec['_edit_crop']=list(crop);im=im.crop(crop);m=m.crop(crop)
                    w=max(256,round(im.width/32)*32);h=max(256,round(im.height/32)*32);spec['width']=w;spec['height']=h
        if spec.get('_heal'):
            scale=max(1,768/max(w,h));w=max(32,round(w*scale/32)*32);h=max(32,round(h*scale/32)*32)
        elif spec['model']=='h3':
            # Reference editing needs scene context. Tiny output latents cause smeared
            # details and geometry drift; retain the full scene at a useful resolution.
            scale=max(1,1024/max(w,h));w=max(32,round(w*scale/32)*32);h=max(32,round(h*scale/32)*32)
        im=im.resize((w,h),Image.Resampling.LANCZOS);spec["width"]=w;spec["height"]=h
        source=f"{job_id}-source.png";im.save(RUNTIME/"input"/source)
        if m is not None:
            mask=f"{job_id}-mask.png";m.resize((w,h),Image.Resampling.NEAREST).save(RUNTIME/"input"/mask)
        if spec["task"]=="control":
            control=f"{job_id}-control.png";imaging.control_image(im,spec["control_mode"]).save(RUNTIME/"input"/control)
    for i,r in enumerate(spec.get("references",[])):
        im=load(r);im.thumbnail((1536,1536));name=f"{job_id}-ref{i}.png";im.save(RUNTIME/"input"/name);refs.append(name)
    return workflows.build(spec,job_id,source,mask,control,refs)
def finish(job,hist):
    st=hist.get("status",{})
    if st.get("status_str")=="error":
        messages=st.get("messages",[])
        error=next((m[1].get("exception_message","Execution failed") for m in reversed(messages) if m[0]=="execution_error"),"Execution interrupted")
        cancelled=bool(job['cancel']) or job.get('state')=='cancelling'
        message=job['message'] if cancelled and job.get('message') else str(error)[:1500]
        update(job["id"],state="cancelled" if cancelled else "failed",message=message);return
    if not st.get("completed"):return
    if job['spec']['model']=='vision':
        texts=[str(t) for o in hist.get('outputs',{}).values() for t in o.get('text',[])]
        if not texts:raise ValueError('Vision completed without text.')
        quality=job['spec'].get('_quality_review',False)
        outputs=[{'text':'\n'.join(texts),'role':'quality_review' if quality else 'suggestion'}]
        (DATA/'receipts'/(job['id']+'.json')).write_text(json.dumps({'job':job,'history':hist,'outputs':outputs},indent=2))
        update(job['id'],state='complete',message='Quality report ready' if quality else 'Suggestion ready for review',outputs=json.dumps(outputs));return
    outs=[]
    for entry in hist.get("outputs",{}).values():
        for item in entry.get("images",[]):
            if item.get("type")!="output":continue
            path=(RUNTIME/"output"/item.get("subfolder","")/item["filename"]).resolve()
            if not path.is_relative_to((RUNTIME/"output").resolve()) or not path.is_file():raise ValueError("Unexpected renderer output path.")
            im=imaging.decode(path.read_bytes())
            if job['spec']['model'] in ('segment','samtrack'):im=im.resize(load(job['spec']['source']).size,Image.Resampling.NEAREST)
            outs.append(put_image(im,"Layer" if job["spec"]["model"]=="layered" else "Generated image"))
    if not outs:raise ValueError("Renderer completed without an image.")
    spec=job["spec"]
    if spec.get("_mask") and spec["task"]!="layers":
        raw=outs[0];edited=load(raw['id'])
        if spec.get('_edit_crop'):
            x0,y0,x1,y1=spec['_edit_crop'];full=load(spec['_original']);full.paste(edited.resize((x1-x0,y1-y0),Image.Resampling.LANCZOS),(x0,y0));edited=full
        if spec.get('_heal') or spec['task']=='remove':
            im=imaging.harmonize_repair(load(spec['_original']),edited,load(spec['_mask']))
        else:im=imaging.composite_mask(load(spec["_original"]),edited,load(spec["_mask"]))
        final=put_image(im,"Masked edit - original preserved");final["raw_asset"]=raw["id"];outs[0]=final
    if spec["model"]=="layered":
        for i,o in enumerate(outs):o["role"]="reconstruction" if i==0 else "layer";o["name"]="Reconstruction" if i==0 else f"Layer {i}";o["stack_index"]=i-1
    receipt={"job_id":job["id"],"prompt_id":job["prompt_id"],"spec":spec,"outputs":outs,"history":hist,"finished":time.time()}
    (DATA/"receipts"/(job["id"]+".json")).write_text(json.dumps(receipt,indent=2))
    update(job["id"],state="complete",message="Saved locally",outputs=json.dumps(outs))
def reconcile(job):
    if not runtime_ready():
        if job['state']!='uncertain':update(job['id'],state='uncertain',message='Renderer unavailable. Job retained without retry; restart the Studio renderer before continuing.')
        return
    pid=job["prompt_id"]
    hist=api("GET","/history/"+pid)
    if pid in hist:
        finish(job,hist[pid]);return
    q=api("GET","/queue")
    running={x[1] for x in q.get("queue_running",[])};pending={x[1] for x in q.get("queue_pending",[])}
    if job["cancel"] or game_running():
        if pid in pending:api("POST","/queue",json={"delete":[pid]})
        if pid in running:api("POST","/interrupt",json={"prompt_id":pid})
        update(job["id"],cancel=1,state="cancelling" if pid in running else "cancelled",message="Cancelled for game safety" if game_running() else "Cancellation requested")
        return
    if pid in running:update(job["id"],state="running",message="Rendering on your GPU")
    elif pid in pending:update(job["id"],state="submitted",message="Waiting in renderer queue")
    elif time.time()-job["updated"]>15:
        update(job["id"],state="uncertain",message="Renderer has no record. Not retried: inspect receipt before creating another job.")
def worker_tick():
    status["game"]=game_running();status["runtime"]=runtime_ready()
    with db() as c:rows=c.execute("SELECT * FROM jobs WHERE state NOT IN ('complete','failed','cancelled') ORDER BY created").fetchall()
    active=[jobdict(r) for r in rows if r["state"] in ACTIVE]
    if active:
        status['message']='GPU job active - '+active[0]['message']
        for job in active:
            if job["state"]=="preparing" and not job["prompt_id"]:
                update(job["id"],state="queued",message="Recovered preparation")
            elif job["prompt_id"]:reconcile(job)
        return
    if status["game"]:
        status["message"]="Game detected - GPU queue paused"
        if status["runtime"]:release_idle()
        return
    if setting("paused",False):status["message"]="Queue paused by you";return
    waiting=[jobdict(r) for r in rows if r["state"] in ("queued","deferred")]
    if not waiting:
        status["message"]="Ready - GPU idle"
        if status["runtime"] and time.time()-status.get("last_release",0)>45:
            release_idle();status["last_release"]=time.time()
        return
    job=waiting[0]
    if foreign_busy():
        update(job["id"],state="deferred",message="Waiting for another ComfyUI queue");status["message"]="Another renderer is busy";return
    if not GPU_LOCK.acquire(blocking=False):return
    try:
        start_runtime()
        q=api("GET","/queue")
        if q.get("queue_running") or q.get("queue_pending"):
            update(job["id"],state="deferred",message="Renderer already has work");return
        with LOCK:
            if get_job(job['id'])['cancel']:return
            update(job["id"],state="preparing",message="Preparing image and workflow")
        spec=job["spec"];graph=prepare(spec,job["id"])
        (DATA/"receipts"/(job["id"]+".workflow.json")).write_text(json.dumps(graph,indent=2))
        if game_running() or foreign_busy():
            update(job["id"],state="deferred",message="GPU work deferred before submission");return
        with LOCK:
            if get_job(job["id"])["cancel"]:
                update(job["id"],state="cancelled",message="Cancelled before submission");return
            prompt_id=str(uuid.uuid4())
            # Atomic with cancel; once persisted, cancellation reconciles this exact ID.
            update(job["id"],state="submitting",message="Submitting once",spec=json.dumps(spec),prompt_id=prompt_id)
        try:
            result=api("POST","/prompt",timeout=60,json={"prompt":graph,"prompt_id":prompt_id,"client_id":"ai-image-studio","extra_data":{"studio_job_id":job["id"]}})
        except httpx.HTTPStatusError as e:
            update(job["id"],state="failed",message=e.response.text[:1500]);return
        except Exception as e:
            update(job["id"],state="uncertain",message="Submission response lost; reconciling saved prompt ID. "+str(e)[:200]);return
        if result.get("prompt_id")!=prompt_id:
            update(job["id"],state="uncertain",message="Unexpected renderer prompt ID. No automatic retry.");return
        update(job["id"],state="submitted",message="Renderer accepted the image")
    finally:GPU_LOCK.release()
def worker():
    while not STOP.is_set():
        try:worker_tick()
        except Exception as e:
            logger.exception("Queue tick")
            status["message"]=str(e)[:250]
            with db() as c:r=c.execute("SELECT id FROM jobs WHERE state='preparing' ORDER BY created LIMIT 1").fetchone()
            if r:update(r[0],state="failed",message=str(e)[:1000])
        STOP.wait(2)
@contextlib.asynccontextmanager
async def lifespan(app):
    init_db();STOP.clear()
    from . import studio_pro
    studio_pro.init()
    t=threading.Thread(target=worker,daemon=True);t.start()
    pro=threading.Thread(target=studio_pro.worker,daemon=True);pro.start()
    yield
    STOP.set();t.join(timeout=4);pro.join(timeout=4)
app=FastAPI(title="AI Image Studio",lifespan=lifespan)
@app.middleware("http")
async def local_only(request:Request,call_next):
    host=request.headers.get("host","").split(":")[0]
    origin=request.headers.get("origin")
    if host not in ("127.0.0.1","localhost","testserver"):return JSONResponse({"detail":"Loopback access only."},400)
    if origin and origin not in (f"http://127.0.0.1:{config.PORT}",f"http://localhost:{config.PORT}"):return JSONResponse({"detail":"Cross-origin request blocked."},403)
    if request.method not in ("GET","HEAD","OPTIONS") and request.headers.get("sec-fetch-site")=="cross-site":return JSONResponse({"detail":"Cross-site request blocked."},403)
    if int(request.headers.get("content-length","0") or 0)>45*1024*1024:return JSONResponse({"detail":"Upload exceeds 40 MB."},413)
    response=await call_next(request)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    return response
@app.exception_handler(ValueError)
async def value_error(request,e):return JSONResponse({"detail":str(e)},400)
@app.exception_handler(KeyError)
async def missing_value(request,e):return JSONResponse({'detail':'Missing required field: '+str(e.args[0])},400)
class JobSpec(BaseModel):
    model_config=ConfigDict(extra="forbid")
    model:Literal["h3","zimage","qwen21","layered"]="h3"
    task:Literal["create","edit","remove","recolor","outpaint","control","cutout","layers"]="create"
    prompt:str=Field(min_length=1,max_length=6000)
    width:int=Field(default=1024,ge=256,le=2048)
    height:int=Field(default=1024,ge=256,le=2048)
    seed:int=Field(default=42,ge=0,le=9007199254740991)
    steps:int=Field(default=25,ge=4,le=50)
    layers:int=Field(default=2,ge=2,le=6)
    strength:float=Field(default=.8,ge=.1,le=1)
    padding:int=Field(default=128,ge=32,le=512)
    feather:int=Field(default=0,ge=0,le=32)
    source:str|None=None
    mask:str|None=None
    references:list[str]=Field(default_factory=list,max_length=3)
    control_mode:Literal["canny","gray","provided"]="canny"
@app.get("/api/health")
def health():
    live=status.copy()
    with db() as c:r=c.execute("SELECT state,message FROM jobs WHERE state NOT IN ('complete','failed','cancelled') ORDER BY CASE WHEN state IN ('queued','deferred') THEN 1 ELSE 0 END,created LIMIT 1").fetchone()
    if r:live['message']=r['message']
    return {"app":"AI Image Studio","version":"1.6.1-rc.1","status":live,"paused":setting("paused",False),"local_only":True,"cpu_only":config.CPU_ONLY}
@app.get("/api/models")
def models():
    catalog=workflows.catalog()
    with db() as c:completed=[json.loads(r[0]) for r in c.execute("SELECT spec FROM jobs WHERE state='complete'")]
    for m in catalog:m['tested_tasks']=sorted({s['task'] for s in completed if s['model']==m['id']})
    return catalog
@app.post("/api/assets")
async def upload(file:UploadFile=File(...)):
    chunks=[];total=0
    while chunk:=await file.read(1024*1024):
        total+=len(chunk)
        if total>imaging.MAX_BYTES:raise HTTPException(413,"Use an image up to 40 MB.")
        chunks.append(chunk)
    return put_image(imaging.decode(b"".join(chunks)),Path(file.filename or "Image").name)
@app.get("/api/assets")
def assets():
    with db() as c:return [dict(r)|{"url":"/api/assets/"+r["id"]} for r in c.execute("SELECT * FROM assets ORDER BY created DESC LIMIT 200")]
@app.get("/api/assets/{asset}")
def image_file(asset:str):return FileResponse(asset_path(asset),media_type="image/png")
def validate_job(spec:JobSpec):
    s=spec.model_dump();m=next(x for x in workflows.catalog() if x["id"]==s["model"])
    if not m["available"]:raise ValueError("Missing model files: "+", ".join(m["missing"]))
    if s["task"] not in m["tasks"]:raise ValueError("This model does not support this task.")
    if s["width"]%32 or s["height"]%32:raise ValueError("Dimensions must be multiples of 32.")
    if s["task"]!="create" and not s["source"]:raise ValueError("Add a source image for this task.")
    if s["model"]=="zimage" and s["task"]!="create" and not m["control_available"]:raise ValueError("ControlNet model is not installed yet.")
    if s["model"]=="zimage" and s["task"] in ("edit","remove","recolor") and not s["mask"]:raise ValueError("Paint a mask for Z-Image inpainting.")
    if s["references"] and s["model"] not in ("h3","qwen21"):raise ValueError("References require H3 or Qwen 2.1.")
    for a in [s.get("source"),s.get("mask"),*s["references"]]:
        if a:asset_path(a)
    if s['mask'] and not s['source']:raise ValueError('A mask needs a source image.')
    if s['mask'] and load(s['mask']).size!=load(s['source']).size:raise ValueError('Mask dimensions must match the source.')
    if s['source'] and s['model']!='layered' and max(load(s['source']).size)>2048:raise ValueError('Resize source to 2048 pixels or less.')
    if s["mask"] and load(s["mask"]).convert("L").getextrema()[1]==0:raise ValueError("Paint a nonempty selection first.")
    return s
@app.post("/api/jobs")
def submit(spec:JobSpec):
    s=validate_job(spec)
    j=str(uuid.uuid4());now=time.time()
    with db() as c:
        count=c.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled')").fetchone()[0]
        if count>=30:raise ValueError("Queue is full (30 jobs).")
        c.execute("INSERT INTO jobs(id,spec,state,message,created,updated) VALUES (?,?,?,?,?,?)",(j,json.dumps(s),"queued","Waiting for GPU",now,now))
    return get_job(j)
@app.get("/api/jobs")
def jobs():
    with db() as c:return [jobdict(r) for r in c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")]
@app.get("/api/jobs/{job_id}")
def job(job_id:str):return get_job(job_id)
@app.get('/api/jobs/{job_id}/export')
def export_job(job_id:str):
    j=get_job(job_id);graph_path=DATA/'receipts'/(j['id']+'.workflow.json')
    if not graph_path.exists():raise ValueError('Workflow is not prepared yet.')
    graph=json.loads(graph_path.read_text());buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('workflow.api.json',json.dumps(graph,indent=2));z.writestr('job.json',json.dumps(j,indent=2))
        z.writestr('README.txt','ComfyUI API graph and Studio finishing bundle.\n\nCopy input/ files to your ComfyUI input directory. Open workflow.api.json with ComfyUI File > Open (check source/reference connections), or submit via its API: POST {"prompt": GRAPH} to /prompt. Required model names are in the graph. Model weights are not included.\n\nThe graph reproduces the renderer stage. Studio applies protected-area compositing, optional local crop placement and repair color matching afterward. The saved final image is in outputs/. Original assets, masks and raw output are included in studio-assets/.\n\nTo reproduce the saved final image on CPU, extract the entire bundle and run: python reproduce.py\nTo finish a newly rendered PNG: python reproduce.py --raw "new-comfy-output.png" --output "finished-edit.png"\nUse the Studio Python environment, or Python with Pillow, numpy and opencv-python installed. Existing output files are never overwritten. The bundled imaging implementation preserves this export version. The replay tool is for image outputs; helper vision/selection/tracking graphs retain their own results.\n\nDirect ComfyUI execution bypasses the Studio game guard. Use Studio for guarded GPU jobs. Newly rendered results may differ due to GPU/runtime nondeterminism. The CPU replay of the included raw output can be checked against the included final output.')
        names={n['inputs']['image'] for n in graph.values() if n['class_type'] in ('LoadImage','LoadImageMask')}
        for name in names:
            path=(RUNTIME/'input'/name).resolve()
            if not path.is_relative_to((RUNTIME/'input').resolve()):raise ValueError('Unsafe workflow input path.')
            z.write(path,'input/'+path.name)
        for o in j['outputs']:
            if o.get('id'):z.write(asset_path(o['id']),'outputs/'+o['id']+'.png')
        spec=j['spec'];assets={spec.get(k) for k in ('source','mask','_original','_mask','_review_source','_review_before')}
        assets.update(spec.get('references',[]))
        for o in j['outputs']:assets.update((o.get('id'),o.get('raw_asset')))
        assets.discard(None)
        for id in sorted(assets):z.write(asset_path(id),'studio-assets/'+id+'.png')
        z.write(ROOT/'scripts/replay_workflow_output.py','reproduce.py')
        z.write(ROOT/'backend/imaging.py','atelier_imaging.py')
        receipt=DATA/'receipts'/(j['id']+'.json')
        if receipt.exists():z.write(receipt,'receipt.json')
    return Response(buf.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="comfy-workflow-bundle.zip"'})
@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id:str):
    with LOCK:
        job=get_job(job_id)
        if job["state"] in TERMINAL:return job
        if job["state"] in ("queued","deferred","preparing") and not job["prompt_id"]:
            update(job_id,cancel=1,state="cancelled",message="Cancelled before submission")
        else:update(job_id,cancel=1,message="Cancellation requested")
    return get_job(job_id)
@app.post("/api/queue")
def queue_control(value:dict):
    if type(value.get("paused")) is not bool:raise ValueError("Provide paused true or false.")
    set_setting("paused",value["paused"]);return {"paused":value["paused"]}
@app.post("/api/compose")
def compose(value:dict):
    value=schemas.Composition.model_validate(value).model_dump()
    layers=value.get("layers",[])
    if not isinstance(layers,list) or not 1<=len(layers)<=24:raise ValueError("Use 1-24 layers.")
    im=imaging.compose((int(value["width"]),int(value["height"])),layers,load)
    return put_image(im,"Layer composition")
@app.post("/api/cpu")
def cpu_edit(value:dict):
    value=schemas.CpuEdit.model_validate(value).model_dump()
    im=load(value["source"]);action=value["action"]
    if action=="resize":
        w,h=int(value["width"]),int(value["height"])
        if not 32<=w<=4096 or not 32<=h<=4096:raise ValueError("Resize dimensions must be 32-4096.")
        im=im.resize((w,h),Image.Resampling.LANCZOS)
    elif action=="cutout":
        mask=load(value["mask"]).convert("L")
        if mask.size!=im.size:raise ValueError("Mask dimensions must match.")
        im.putalpha(Image.fromarray(__import__("numpy").minimum(__import__("numpy").array(im.getchannel("A")),__import__("numpy").array(mask))))
    elif action=="recolor":
        mask=load(value["mask"]).convert("L")
        color=Image.new("RGBA",im.size,value.get("color","#7c86ff"))
        color.putalpha(im.getchannel("A"));im=imaging.composite_mask(im,color,mask)
    elif action=="remove":
        import cv2,numpy as np
        mask=load(value["mask"]).convert("L")
        if mask.size!=im.size:raise ValueError("Mask dimensions must match.")
        rgb=np.array(im.convert("RGB"));result=cv2.inpaint(rgb,np.array(mask),3,cv2.INPAINT_TELEA)
        im=Image.fromarray(result).convert("RGBA");im.putalpha(load(value["source"]).getchannel("A"))
    elif action=="outpaint":
        im,mask=imaging.outpaint(im,value.get("padding",128))
        return {"image":put_image(im,"Expanded canvas"),"mask":put_image(mask,"Border mask")}
    else:raise ValueError("Unknown CPU operation.")
    return put_image(im,action.capitalize())
@app.post("/api/track")
def track(value:dict):
    value=schemas.Tracking.model_validate(value).model_dump()
    ids=value.get("frames",[])
    if not 2<=len(ids)<=24:raise ValueError("Use 2-24 ordered frames.")
    if value.get('method')=='sam':
        if not helpers.available()['segment']:raise ValueError('SAM model is still downloading.')
        frames=[load(x) for x in ids];mask=load(value['mask'])
        if any(f.size!=frames[0].size for f in frames) or mask.size!=frames[0].size:raise ValueError('Frames and mask must have matching dimensions.')
        if max(frames[0].size)>1280:raise ValueError('Use frames up to 1280 pixels.')
        j=str(uuid.uuid4());now=time.time();spec=dict(model='samtrack',task='track',prompt='Track selected object',source=ids[0],frames=ids,mask=value['mask'],seed=0)
        with db() as c:
            if c.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled')").fetchone()[0]>=30:raise ValueError('Queue is full.')
            c.execute('INSERT INTO jobs(id,spec,state,message,created,updated) VALUES (?,?,?,?,?,?)',(j,json.dumps(spec),'queued','Waiting for GPU',now,now))
        return get_job(j)
    masks,error=imaging.track([load(x) for x in ids],load(value["mask"]))
    return {"masks":[put_image(m,f"Tracked mask {i+1}") for i,m in enumerate(masks)],"photometric_error":error,"method":"CPU Farneback optical flow; inspect each mask. Occlusions and cuts are not handled."}
@app.get("/api/projects")
def projects():
    with db() as c:return [dict(r) for r in c.execute("SELECT id,name,updated FROM projects ORDER BY updated DESC")]
@app.post("/api/projects")
def save_project(value:dict):
    value=schemas.Project.model_validate(value).model_dump()
    payload=value.get("payload",{});encoded=json.dumps(payload)
    if len(encoded)>500_000:raise ValueError("Project is too large.")
    name=str(value.get("name","Untitled"))[:120].strip() or "Untitled"
    # Validate referenced images at save time; keep arbitrary UI state bounded.
    refs=[payload.get("source"),payload.get("original"),payload.get("mask")]+[x.get("asset") for x in payload.get("layers",[])]+[x.get("id") for x in payload.get("references",[])]
    for a in refs:
        if a:asset_path(a)
    p=uid(value["id"]) if value.get("id") else str(uuid.uuid4())
    from . import studio_plus
    now=time.time()
    with LOCK,db() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT * FROM projects WHERE id=?',(p,)).fetchone()
        if old and value.get('base_updated') is not None and abs(old['updated']-value['base_updated'])>.000001:
            raise HTTPException(409,'This project changed in another tab. Use Save a copy to keep your work, then open the newer project.')
        if old:studio_plus.snapshot(c,p,old['name'],old['payload'],old['updated'])
        c.execute("INSERT OR REPLACE INTO projects VALUES (?,?,?,?)",(p,name,encoded,now))
        studio_plus.snapshot(c,p,name,encoded,now)
    return {"id":p,"name":name,"updated":now}
@app.post('/api/projects/import')
async def import_project(file:UploadFile=File(...)):
    raw=await file.read(40*1024*1024+1)
    if len(raw)>40*1024*1024:raise HTTPException(413,'Project ZIP must be at most 40 MB.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            items=z.infolist();names=[x.filename for x in items]
            if len(names)!=len(set(names)) or len(items)>64 or sum(x.file_size for x in items)>200*1024*1024:raise ValueError('Project archive exceeds limits or contains duplicates.')
            if 'project.json' not in names or z.getinfo('project.json').file_size>500_000:raise ValueError('Missing or oversized project.json.')
            project=schemas.Project.model_validate(json.loads(z.read('project.json'))).model_dump()
            payload=project['payload'];ids=schemas.project_assets(payload)
            allowed={'project.json'}|{'assets/'+uid(a)+'.png' for a in ids}
            if set(names)!=allowed:raise ValueError('Project contains missing or unexpected files.')
            decoded={};pixels=0
            for a in ids:
                im=imaging.decode(z.read('assets/'+a+'.png'));pixels+=im.width*im.height
                if pixels>48_000_000:raise ValueError('Project images exceed 48 megapixels in total.')
                decoded[a]=im
    except (zipfile.BadZipFile,KeyError,RuntimeError) as e:raise ValueError('Unreadable project ZIP or missing assets.') from e
    # Validate every member before writing anything; never extract archive paths.
    mapped={a:put_image(im,'Restored project image')['id'] for a,im in decoded.items()}
    for k in ('source','original','mask'):
        if payload[k]:payload[k]=mapped[payload[k]]
    for layer in payload['layers']:layer['asset']=mapped[layer['asset']]
    payload['references']=[asset_info(mapped[r['id']]) for r in payload['references']]
    project['id']=None
    project['name']=project['name']+' (restored)'
    return save_project(project)
@app.get("/api/projects/{project}")
def read_project(project:str):
    with db() as c:r=c.execute("SELECT * FROM projects WHERE id=?",(uid(project),)).fetchone()
    if not r:raise ValueError("Project not found.")
    return dict(r)|{"payload":json.loads(r["payload"])}
@app.get("/api/projects/{project}/export")
def export_project(project:str):
    p=read_project(project);payload=p["payload"];refs={payload.get("source"),payload.get("original"),payload.get("mask")}
    refs.update(x.get("asset") for x in payload.get("layers",[]));refs.update(x.get("id") for x in payload.get("references",[]));refs.discard(None)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json",json.dumps(p,indent=2))
        for a in refs:z.write(asset_path(a),"assets/"+a+".png")
    return Response(buf.getvalue(),media_type="application/zip",headers={"Content-Disposition":'attachment; filename="image-studio-project.zip"'})
@app.get("/api/vision")
def vision_status():
    a=helpers.available()
    return {"available":a['vision'],"segment":a['segment'],"message":"Local Qwen vision - queued with image jobs. Suggestions require your review."}
class SelectionPoint(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    x:int=Field(ge=0,le=4095)
    y:int=Field(ge=0,le=4095)
class HelperSpec(BaseModel):
    model_config=ConfigDict(extra="forbid")
    source:str
    instruction:str=Field(default="Suggest a useful edit.",min_length=1,max_length=2000)
    kind:Literal['vision','segment']='vision'
    purpose:Literal['suggest','review']='suggest'
    before:str|None=None
    positive_points:list[SelectionPoint]=Field(default_factory=list,max_length=32)
    negative_points:list[SelectionPoint]=Field(default_factory=list,max_length=32)
    threshold:float=Field(default=.5,ge=.05,le=.95,allow_inf_nan=False)
    refine_iterations:int=Field(default=2,ge=0,le=5)
@app.post('/api/vision')
def vision(value:HelperSpec):
    asset_path(value.source)
    if not helpers.available()[value.kind]:raise ValueError('Helper model is still downloading.')
    if value.kind=='segment' and len(value.instruction.split())>20:raise ValueError('Describe the object in at most 20 words.')
    if value.negative_points and not value.positive_points:raise ValueError('Add a positive selection point first.')
    if value.kind!='segment' and (value.positive_points or value.negative_points):raise ValueError('Points require object selection.')
    if value.kind!='vision' and (value.purpose=='review' or value.before):raise ValueError('Quality review requires vision.')
    if value.before and value.purpose!='review':raise ValueError('A comparison image requires quality review.')
    info=asset_info(value.source)
    if any(p.x>=info['width'] or p.y>=info['height'] for p in value.positive_points+value.negative_points):raise ValueError('Selection points must be inside the image.')
    s=dict(model=value.kind,task=value.kind,prompt=value.instruction,source=value.source,source_sha=asset_info(value.source)['sha256'],seed=0)
    if value.purpose=='review':
        if value.before:s['source']=put_image(helpers.review_pair(load(value.before),load(value.source)),'Quality review comparison')['id']
        s.update(_quality_review=True,_review_source=value.source,_review_before=value.before,_director_prompt=(
            ('The image shows labeled BEFORE and AFTER panels. Review only the AFTER result and compare it with BEFORE. ' if value.before else 'Review the supplied image. ')+
            'Act as a careful visual quality reviewer. Check requested changes, edges and halos, geometry, texture, lighting and shadows, text, and unwanted changes. '
            'Describe only visible evidence; say uncertain when you cannot tell. Do not claim pixel-exact preservation from visual inspection. '
            'Respond in short plain-text sections: Overall; Visible issues; Suggested next step. Do not return JSON or invent missing details. User goal: '+value.instruction))
    if value.kind=='segment':s.update(positive_points=[p.model_dump() for p in value.positive_points],negative_points=[p.model_dump() for p in value.negative_points],threshold=value.threshold,refine_iterations=value.refine_iterations)
    j=str(uuid.uuid4());now=time.time()
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled')").fetchone()[0]>=30:raise ValueError('Queue is full.')
        c.execute('INSERT INTO jobs(id,spec,state,message,created,updated) VALUES (?,?,?,?,?,?)',(j,json.dumps(s),'queued','Waiting for GPU',now,now))
    return get_job(j)
from . import studio_pro,studio_plus,studio_tools
app.include_router(studio_pro.router)
app.include_router(studio_plus.router)
app.include_router(studio_tools.router)
from .workspace import router as workspace_router
app.include_router(workspace_router)
app.mount("/",StaticFiles(directory=ROOT/"frontend",html=True),name="frontend")

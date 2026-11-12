"""Workspace history and bounded variations, sharing the existing guarded queue."""
import hashlib,json,time,uuid
from fastapi import APIRouter
from pydantic import BaseModel,ConfigDict,Field
from .context import app as a

router=APIRouter(prefix='/api/studio')

def init(c):
    c.executescript('''
CREATE TABLE IF NOT EXISTS project_revisions(id TEXT PRIMARY KEY,project TEXT,name TEXT,payload TEXT,created REAL);
CREATE INDEX IF NOT EXISTS project_revision_order ON project_revisions(project,created DESC);
CREATE TABLE IF NOT EXISTS variation_batches(id TEXT PRIMARY KEY,fingerprint TEXT,jobs TEXT,created REAL);
''')

def snapshot(c,project,name,payload,created):
    latest=c.execute('SELECT name,payload FROM project_revisions WHERE project=? ORDER BY created DESC LIMIT 1',(project,)).fetchone()
    if latest and latest['name']==name and latest['payload']==payload:return
    c.execute('INSERT INTO project_revisions VALUES (?,?,?,?,?)',(str(uuid.uuid4()),project,name,payload,created))

@router.get('/projects/{project}/history')
def history(project:str):
    a.read_project(project)
    with a.db() as c:
        rows=c.execute('SELECT * FROM project_revisions WHERE project=? ORDER BY created DESC LIMIT 100',(a.uid(project),)).fetchall()
    return [dict(id=r['id'],name=r['name'],created=r['created'],layers=len(json.loads(r['payload']).get('layers',[])),source=json.loads(r['payload']).get('source')) for r in rows]

@router.post('/projects/{project}/restore/{revision}')
def restore(project:str,revision:str):
    with a.db() as c:r=c.execute('SELECT * FROM project_revisions WHERE project=? AND id=?',(a.uid(project),a.uid(revision))).fetchone()
    if not r:raise ValueError('Saved revision not found.')
    # Always branch: another open tab and later saves remain intact.
    return a.save_project({'name':r['name']+' (restored revision)','payload':json.loads(r['payload'])})

class BatchSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str
    spec:dict
    count:int=Field(default=2,ge=1,le=4)

@router.post('/variations')
def variations(value:BatchSpec):
    id=a.uid(value.request_id)
    spec=a.validate_job(a.JobSpec.model_validate(value.spec))
    if spec['seed']+value.count-1>9007199254740991:raise ValueError('Choose a lower seed for these variations.')
    fingerprint=hashlib.sha256(json.dumps([spec,value.count],sort_keys=True).encode()).hexdigest()
    now=time.time()
    with a.LOCK,a.db() as c:
        c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT * FROM variation_batches WHERE id=?',(id,)).fetchone()
        if existing:
            if existing['fingerprint']!=fingerprint:raise ValueError('This request identifier was already used for different variations.')
            ids=json.loads(existing['jobs'])
        else:
            count=c.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled')").fetchone()[0]
            if count+value.count>30:raise ValueError('Not enough queue space for the complete variation set.')
            ids=[]
            for i in range(value.count):
                jid=str(uuid.uuid4());ids.append(jid)
                s=spec|{'seed':spec['seed']+i,'_batch':id,'_variation':i+1}
                c.execute('INSERT INTO jobs(id,spec,state,message,created,updated) VALUES (?,?,?,?,?,?)',(jid,json.dumps(s),'queued',f'Variation {i+1}/{value.count} · waiting for GPU',now+i*.001,now))
            c.execute('INSERT INTO variation_batches VALUES (?,?,?,?)',(id,fingerprint,json.dumps(ids),now))
    return dict(id=id,jobs=[a.get_job(j) for j in ids])

@router.get('/resources')
def resources():
    stats={}
    if a.runtime_ready():
        try:
            raw=a.api('GET','/system_stats',timeout=2)
            if hasattr(raw,'json'):raw=raw.json()
            stats={'devices':[{k:d.get(k) for k in ('name','vram_total','vram_free','torch_vram_total','torch_vram_free')} for d in raw.get('devices',[])]}
        except Exception:pass
    with a.db() as c:counts={r[0]:r[1] for r in c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state')}
    return dict(game=a.game_running(),paused=a.setting('paused',False),queue=counts,renderer_owned=a.runtime_owned(),**stats)

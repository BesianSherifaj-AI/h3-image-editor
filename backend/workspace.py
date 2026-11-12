"""Explicit reference handoff to the companion loopback video studio."""
import base64
import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from . import config
from .context import app as a

router = APIRouter(prefix='/api/workspace')

@router.get('/connections')
def connections():
    h3 = {'ready': False, 'url': config.H3_STUDIO + '/video', 'version': None}
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(config.H3_STUDIO + '/api/atelier/status')
            response.raise_for_status()
            info = response.json()
            h3.update(ready=info.get('bridge') == 1, version=info.get('version'))
    except (httpx.HTTPError, ValueError):
        pass
    return {'h3': h3, 'cpu_only': config.CPU_ONLY, 'comfy_url': config.COMFY, 'lmstudio_url': config.LMSTUDIO}

class Handoff(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str
    source: str
    title: str = Field(min_length=1, max_length=160)
    idea: str = Field(default='', max_length=20000)

@router.post('/handoff')
def handoff(spec: Handoff):
    request_id = a.uid(spec.request_id)
    path = a.asset_path(spec.source)
    if path.stat().st_size > 18 * 1024 * 1024:
        raise ValueError('Resize the composition first; video references must be at most 18 MB.')
    payload = {'request_id': request_id, 'title': spec.title, 'idea': spec.idea,
               'data_url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            bootstrap = client.get(config.H3_STUDIO + '/api/bootstrap')
            bootstrap.raise_for_status()
            token = bootstrap.json()['token']
            result = client.post(config.H3_STUDIO + '/api/atelier/reference', json=payload,
                                 headers={'X-H3-Token': token})
            if result.status_code >= 400:
                try: message = result.json().get('detail', 'Reference transfer failed.')
                except ValueError: message = 'Update and start H3 video studio to enable the reference bridge.'
                raise ValueError(str(message))
            data = result.json()
            return {'id': data['id'], 'title': data['title'], 'url': config.H3_STUDIO + '/video?project=' + a.uid(data['id'])}
    except httpx.TimeoutException as exc:
        raise ValueError('The transfer timed out. Retrying the same reference safely checks the existing draft.') from exc
    except (httpx.HTTPError, KeyError) as exc:
        raise ValueError('H3 video studio is unavailable. Start it and try this same transfer again.') from exc

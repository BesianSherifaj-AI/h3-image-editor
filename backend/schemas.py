"""Bounded request and portable project shapes."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class Layer(BaseModel):
    model_config=ConfigDict(extra='allow',allow_inf_nan=False)
    asset:str
    name:str='Image'
    visible:bool=True
    x:float=Field(default=0,ge=-8192,le=8192)
    y:float=Field(default=0,ge=-8192,le=8192)
    scale:float=Field(default=1,ge=.05,le=4)
    opacity:float=Field(default=1,ge=0,le=1)
    rotation:float=Field(default=0,ge=-360,le=360)
    blend:Literal['normal','multiply','screen','overlay','darken','lighten','difference']='normal'
    flip_x:bool=False
    flip_y:bool=False
    color:str|None=None

class Composition(BaseModel):
    width:int=Field(ge=32,le=4096)
    height:int=Field(ge=32,le=4096)
    layers:list[Layer]=Field(min_length=1,max_length=24)

class CpuEdit(BaseModel):
    source:str
    action:Literal['resize','cutout','recolor','remove','outpaint']
    mask:str|None=None
    width:int=Field(default=1024,ge=32,le=4096)
    height:int=Field(default=1024,ge=32,le=4096)
    color:str='#7c86ff'
    padding:int=Field(default=128,ge=32,le=512)

class Tracking(BaseModel):
    frames:list[str]=Field(min_length=2,max_length=24)
    mask:str
    method:Literal['cpu','sam']='cpu'

class Reference(BaseModel):
    model_config=ConfigDict(extra='allow')
    id:str

class ProjectPayload(BaseModel):
    source:str|None=None
    original:str|None=None
    mask:str|None=None
    width:int=Field(default=1024,ge=32,le=4096)
    height:int=Field(default=1024,ge=32,le=4096)
    layers:list[Layer]=Field(default_factory=list,max_length=24)
    references:list[Reference]=Field(default_factory=list,max_length=3)
    form:dict[str,str|int|float|bool]=Field(default_factory=dict)

class Project(BaseModel):
    id:str|None=None
    base_updated:float|None=None
    name:str='Untitled'
    payload:ProjectPayload=Field(default_factory=ProjectPayload)

def project_assets(payload):
    ids={payload.get(k) for k in ('source','original','mask')}
    ids.update(x['asset'] for x in payload['layers'])
    ids.update(x['id'] for x in payload['references'])
    ids.discard(None)
    return ids

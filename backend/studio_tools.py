"""Fast CPU selection, portable recipes and explicit image export options."""
import io,re,time,uuid,zipfile
from xml.etree import ElementTree as ET
from typing import Literal
import cv2
import numpy as np
from PIL import Image,ImageChops,ImageColor
from fastapi import APIRouter,Query
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from .context import app as a
from .schemas import Composition

router=APIRouter(prefix='/api/tools')

class LayerExportSpec(Composition):
    name:str=Field(default='Atelier artwork',max_length=150)

@router.post('/openraster')
def openraster(v:LayerExportSpec):
    """Portable raster layers; scale/rotation/flips are baked into each PNG."""
    layers=[layer.model_dump() for layer in v.layers]
    merged=a.imaging.compose((v.width,v.height),layers,a.load)
    xml=ET.Element('image',{'version':'0.0.6','w':str(v.width),'h':str(v.height),'name':v.name,'xres':'72','yres':'72'})
    stack=ET.SubElement(xml,'stack');buf=io.BytesIO()
    def png(im):
        data=io.BytesIO();im.save(data,'PNG',compress_level=4);return data.getvalue()
    with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        archive.writestr('mimetype',b'image/openraster',compress_type=zipfile.ZIP_STORED)
        # OpenRaster stores the topmost layer first; Atelier stores it last.
        for i,layer in reversed(list(enumerate(layers))):
            im,(x,y)=a.imaging.transformed_layer(layer,a.load,apply_opacity=False)
            path=f'data/layer{i:02}.png';archive.writestr(path,png(im))
            blend=layer.get('blend','normal');mode='src-over' if blend=='normal' else blend
            ET.SubElement(stack,'layer',{'name':layer['name'],'src':path,'x':str(x),'y':str(y),'opacity':str(layer['opacity']),'visibility':'visible' if layer['visible'] else 'hidden','composite-op':'svg:'+mode})
        archive.writestr('stack.xml',ET.tostring(xml,encoding='utf-8',xml_declaration=True))
        archive.writestr('mergedimage.png',png(merged));thumb=merged.copy();thumb.thumbnail((256,256));archive.writestr('Thumbnails/thumbnail.png',png(thumb))
    safe=re.sub(r'[^a-zA-Z0-9 _.-]','_',v.name)[:80].strip(' .') or 'atelier'
    return Response(buf.getvalue(),media_type='image/openraster',headers={'Content-Disposition':f'attachment; filename="{safe}.ora"','Cache-Control':'no-store'})

class WandSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str
    x:int=Field(ge=0,le=4095)
    y:int=Field(ge=0,le=4095)
    tolerance:int=Field(default=24,ge=0,le=100)
    contiguous:bool=True
    mask:str|None=None
    combine:Literal['replace','add','subtract','intersect']='replace'

def combine_mask(current,new,mode):
    if current.size!=new.size:raise ValueError('Selection dimensions must match.')
    if mode=='add':return ImageChops.lighter(current,new)
    if mode=='subtract':return ImageChops.subtract(current,new)
    if mode=='intersect':return ImageChops.darker(current,new)
    return new

@router.post('/wand')
def wand(v:WandSpec):
    im=a.load(v.source)
    if v.x>=im.width or v.y>=im.height:raise ValueError('Click inside the image.')
    rgba=np.array(im);rgb=rgba[:,:,:3].copy();alpha=rgba[:,:,3]
    delta=np.max(np.abs(rgb.astype(np.int16)-rgb[v.y,v.x].astype(np.int16)),axis=2)
    similar=(delta<=v.tolerance)&(np.abs(alpha.astype(np.int16)-int(alpha[v.y,v.x]))<=v.tolerance)
    # Fully transparent regions have no meaningful RGB color.
    if alpha[v.y,v.x]==0:similar=alpha==0
    if v.contiguous:
        labels=np.pad((~similar).astype(np.uint8),1,constant_values=1)
        cv2.floodFill(similar.astype(np.uint8),labels,(v.x,v.y),0,flags=4|cv2.FLOODFILL_MASK_ONLY|(2<<8))
        selected=(labels[1:-1,1:-1]==2).astype(np.uint8)*255
    else:selected=similar.astype(np.uint8)*255
    result=Image.fromarray(selected)
    if v.mask:result=combine_mask(a.load(v.mask).convert('L'),result,v.combine)
    elif v.combine in ('subtract','intersect'):raise ValueError('Start with a selection before subtracting or intersecting.')
    return a.put_image(result,'Magic wand selection')

class CombineSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    current:str
    incoming:str
    mode:Literal['replace','add','subtract','intersect']='replace'

@router.post('/selection')
def selection(v:CombineSpec):
    return a.put_image(combine_mask(a.load(v.current).convert('L'),a.load(v.incoming).convert('L'),v.mode),'Combined selection')

class ExtractSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str
    mask:str
    feather:int=Field(default=0,ge=0,le=32)

@router.post('/extract-layer')
def extract_layer(v:ExtractSpec):
    im=a.load(v.source);mask=a.load(v.mask).convert('L')
    if im.size!=mask.size:raise ValueError('Selection dimensions must match the source.')
    mask=a.imaging.feather_mask(mask,v.feather)
    alpha=ImageChops.multiply(im.getchannel('A'),mask);bounds=alpha.getbbox()
    if not bounds:raise ValueError('Select visible image content first.')
    im.putalpha(alpha)
    return {'asset':a.put_image(im.crop(bounds),'Extracted selection'),'x':bounds[0],'y':bounds[1]}

@router.get('/export/{asset}')
def export_image(asset:str,format:Literal['png','jpeg','webp']='png',quality:int=Query(92,ge=10,le=100),width:int|None=Query(None,ge=32,le=4096),height:int|None=Query(None,ge=32,le=4096),background:str='#ffffff',lossless:bool=False,name:str='atelier'):
    im=a.load(asset)
    if (width is None)!=(height is None):raise ValueError('Set both export dimensions.')
    if width is not None and (width,height)!=im.size:im=im.resize((width,height),Image.Resampling.LANCZOS)
    if format=='jpeg':
        try:color=ImageColor.getrgb(background)
        except ValueError:raise ValueError('Use a valid background color.')
        if len(color)!=3:raise ValueError('JPEG background must be opaque.')
        bg=Image.new('RGB',im.size,color);bg.paste(im,mask=im.getchannel('A'));im=bg
    data=io.BytesIO();options={'quality':quality,'subsampling':0} if format=='jpeg' else {'quality':quality,'lossless':lossless,'method':3} if format=='webp' else {'compress_level':4}
    im.save(data,format.upper(),**options)
    safe=re.sub(r'[^a-zA-Z0-9 _.-]','_',name)[:80].strip(' .') or 'atelier'
    extension='jpg' if format=='jpeg' else format
    return Response(data.getvalue(),media_type='image/'+format,headers={'Content-Disposition':f'attachment; filename="{safe}.{extension}"','Cache-Control':'no-store'})

FORM_KEYS={'model','task','prompt','size','steps','layerCount','padding','strength','controlMode','feather','variationCount'}
class RecipeSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=80)
    form:dict[str,str|int|float|bool]

@router.get('/recipes')
def recipes():return a.setting('creative_recipes',[])

@router.post('/recipes')
def save_recipe(v:RecipeSpec):
    if not v.name.strip():raise ValueError('Give the recipe a name.')
    if set(v.form)-FORM_KEYS:raise ValueError('Recipe contains unsupported settings.')
    if any(len(str(x))>6000 for x in v.form.values()):raise ValueError('Recipe setting is too long.')
    with a.LOCK:
        rows=recipes()
        if len(rows)>=100:raise ValueError('Up to 100 recipes can be saved. Remove an unused recipe first.')
        row={'id':str(uuid.uuid4()),'name':v.name.strip(),'form':v.form,'created':time.time()};rows.append(row);a.set_setting('creative_recipes',rows)
    return row

@router.delete('/recipes/{recipe}')
def delete_recipe(recipe:str):
    id=a.uid(recipe)
    with a.LOCK:a.set_setting('creative_recipes',[r for r in recipes() if r['id']!=id])
    return {'deleted':id}

"""Atelier's local stdio MCP. Only typed Studio operations; no arbitrary paths or shell."""
import json
from typing import Literal
import httpx
from mcp.server.fastmcp import FastMCP,Image

mcp=FastMCP('Atelier Image Studio',instructions='Operate the local creative studio. Inspect the image and the user request before editing. Preserve originals. Use bounded Director runs. A completed job is not proof of visual quality; inspect the result and its review. Never claim a tool ran until the API confirms it. GPU work waits while Fortnite or Rocket League runs. IDs must come from Studio tool results.')

def call(method,path,value=None):
    with httpx.Client(base_url='http://127.0.0.1:8790/api',timeout=30,trust_env=False) as c:
        r=c.request(method,path,json=value);r.raise_for_status();return r.json()

@mcp.tool()
def studio_status()->dict:
    """Get game guard, queue state, installed image models and local connections."""
    return {'health':call('GET','/health'),'models':call('GET','/models'),'connections':call('GET','/pro/connections')}

@mcp.tool()
def studio_library()->dict:
    """List saved projects and the latest 40 image assets with IDs and dimensions."""
    return {'projects':call('GET','/projects'),'assets':call('GET','/assets')[:40],'videos':call('GET','/pro/videos')}

@mcp.tool()
def inspect_image(asset_id:str)->Image:
    """Return a local Studio image for visual inspection. Requires an existing asset UUID."""
    import uuid
    asset_id=str(uuid.UUID(asset_id))
    with httpx.Client(timeout=30,trust_env=False) as c:r=c.get('http://127.0.0.1:8790/api/assets/'+asset_id);r.raise_for_status()
    return Image(data=r.content,format='png')

@mcp.tool()
def direct_edit(source:str,instruction:str,object_name:str='',mask:str|None=None,automatic:bool=False,allow_whole_image:bool=False,max_attempts:int=1,blend_edges:bool=True)->dict:
    """Plan, select, edit with MiniMax H3 and critique. Default stops for plan approval. Automatic execution requires user intent. At most two attempts; returns run ID, not a finished image."""
    return call('POST','/pro/director',{'source':source,'instruction':instruction,'object':object_name,'mask':mask,'automatic':automatic,'allow_whole_image':allow_whole_image,'max_attempts':max_attempts,'blend_edges':blend_edges})

@mcp.tool()
def creative_runs()->list:
    """Read progress, proposed plans, selection masks, output IDs, review findings and video links."""
    return call('GET','/pro/runs')

@mcp.tool()
def approve_edit(run_id:str,prompt:str)->dict:
    """Approve an awaiting-approval plan using the reviewed edit instruction."""
    import uuid
    return call('POST','/pro/runs/'+str(uuid.UUID(run_id))+'/approve',{'prompt':prompt})

@mcp.tool()
def cancel_creative_run(run_id:str)->dict:
    """Cancel one Director or video run and its current child job."""
    import uuid
    return call('POST','/pro/runs/'+str(uuid.UUID(run_id))+'/cancel',{})

@mcp.tool()
def generate_or_edit(prompt:str,model:Literal['h3','zimage','qwen21','layered']='h3',task:Literal['create','edit','remove','recolor','outpaint','control','cutout','layers']='create',source:str|None=None,mask:str|None=None,width:int=1024,height:int=1024,seed:int=42)->dict:
    """Queue one supported image operation. Use an existing source for editing and a mask to protect unselected pixels. Original images are retained. Returns job ID for polling."""
    return call('POST','/jobs',{'prompt':prompt,'model':model,'task':task,'source':source,'mask':mask,'width':width,'height':height,'seed':seed,'feather':8})

@mcp.tool()
def image_jobs()->list:
    """Read status and output IDs of the latest image jobs."""
    return call('GET','/jobs')[:30]

@mcp.tool()
def select_object_points(source:str,positive_points:list[dict],negative_points:list[dict]|None=None)->dict:
    """Queue native SAM 3.1 selection using source-image pixel coordinates [{x: int, y: int}]. Add keep points inside the object, exclude points outside it. Inspect the resulting mask before editing."""
    return call('POST','/vision',{'source':source,'kind':'segment','instruction':'Point-selected object','positive_points':positive_points,'negative_points':negative_points or []})

@mcp.tool()
def select_color_region(source:str,x:int,y:int,tolerance:int=24,contiguous:bool=True,mask:str|None=None,combine:Literal['replace','add','subtract','intersect']='replace')->dict:
    """Create a color-based selection immediately on CPU. Coordinates are source-image pixels; tolerance is 0–100. Optionally combine with an existing mask. Returns a mask asset to inspect."""
    return call('POST','/tools/wand',{'source':source,'x':x,'y':y,'tolerance':tolerance,'contiguous':contiguous,'mask':mask,'combine':combine})

@mcp.tool()
def review_image(source:str,goal:str='Check visual quality.',before:str|None=None)->dict:
    """Queue a local visual quality report, optionally comparing an original image. Reports can miss defects; review them yourself. Does not edit pixels."""
    return call('POST','/vision',{'source':source,'before':before,'instruction':goal,'purpose':'review'})

@mcp.tool()
def heal_image(source:str,mask:str,instruction:str='Remove the defect and restore natural surrounding texture and lighting.')->dict:
    """Repair a painted region using MiniMax H3, close context and lighting harmonization. Returns job ID; inspect its output."""
    return call('POST','/pro/heal',{'source':source,'mask':mask,'prompt':instruction})

@mcp.tool()
def enhance_image(source:str,scale:Literal[2,4]=2)->dict:
    """Queue SeedVR2 detail enhancement. Output is limited to 2048 pixels per side."""
    return call('POST','/pro/upscale',{'source':source,'scale':scale})

@mcp.tool()
def video_selection_frame(video_id:str,start:float=0)->dict:
    """Prepare a still from an imported video. process_video can select its object automatically from object or prompt."""
    import uuid
    if not 0<=start<=600:raise ValueError('Start must be 0–600 seconds.')
    return call('GET','/pro/videos/'+str(uuid.UUID(video_id))+'/frame?start='+str(start))

@mcp.tool()
def process_video(video_id:str,source:str,mask:str|None=None,start:float=0,duration:float=2,fps:int=4,action:Literal['trim','blur','recolor','h3']='blur',prompt:str='',color:str='#607dff',audio:bool=True,mask_grow:int=6,tracking:Literal['flow','sam']='flow',object:str='',auto_review:bool=True,max_attempts:int=1,feather:int=2,seed:int=42,blend_edges:bool=True)->dict:
    """Queue a bounded tracked clip preview. Omit mask to select automatically from object or prompt; optional AI review retries H3 at most twice, retaining attempts. H3 max 8 frames / 3 seconds; CPU max 120 frames / 12 seconds. Preserve audio optionally. Review drift and flicker."""
    return call('POST','/pro/video-runs',{'video':video_id,'source':source,'mask':mask,'start':start,'duration':duration,'fps':fps,'action':action,'prompt':prompt,'color':color,'audio':audio,'mask_grow':mask_grow,'tracking':tracking,'object':object,'auto_review':auto_review,'max_attempts':max_attempts,'feather':feather,'seed':seed,'blend_edges':blend_edges})

@mcp.tool()
def refine_selection(mask:str,grow:int=4,feather:int=0,invert:bool=False)->dict:
    """Create an editable selection mask by growing/shrinking and feathering inward. Does not alter the original mask or image."""
    return call('POST','/pro/mask',{'source':mask,'grow':grow,'feather':feather,'invert':invert})

@mcp.tool()
def image_variations(request_id:str,prompt:str,count:int=2,model:Literal['h3','qwen21','zimage']='h3',task:Literal['create','edit','remove','recolor']='create',source:str|None=None,mask:str|None=None,seed:int=42,width:int=1024,height:int=1024)->dict:
    """Queue 1–4 consecutive-seed variations atomically. Reuse the same UUID request_id and identical arguments after a lost response to avoid duplicate work."""
    return call('POST','/studio/variations',{'request_id':request_id,'count':count,'spec':{'model':model,'task':task,'prompt':prompt,'source':source,'mask':mask,'seed':seed,'width':width,'height':height,'feather':8}})

@mcp.tool()
def project_history(project_id:str)->list:
    """Read saved project revisions; this does not change the current project."""
    import uuid
    return call('GET','/studio/projects/'+str(uuid.UUID(project_id))+'/history')

@mcp.tool()
def review_video(run_id:str)->dict:
    """Ask local vision to critique a completed preview's sampled frames. Does not judge audio or continuous motion."""
    import uuid
    return call('POST','/pro/runs/'+str(uuid.UUID(run_id))+'/video-review',{})

@mcp.tool()
def pause_studio(paused:bool=True)->dict:
    """Pause or resume new Studio work. Does not terminate other applications."""
    return call('POST','/queue',{'paused':paused})

@mcp.tool()
def unload_studio_models()->dict:
    """Free idle Studio VRAM. Refuses while a renderer job is active."""
    return call('POST','/pro/release',{})

if __name__=='__main__':mcp.run(transport='stdio')

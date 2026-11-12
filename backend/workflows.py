"""Explicit native ComfyUI graphs; no third-party custom nodes."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from .config import MODEL_ROOTS
REGISTRY={
"h3":{"name":"MiniMax H3 Image","about":"Six-step generation & reference editing","unet":"minimax_h3_image_pruned_w4a8.safetensors","clip":"qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors","vae":"minimax_h3_image_vae_fp16.safetensors","tasks":["create","edit","remove","recolor","outpaint"],"steps":6},
"zimage":{"name":"Z-Image Turbo","about":"Fast creation & ControlNet inpainting","unet":"z_image_turbo_bf16.safetensors","clip":"qwen_3_4b.safetensors","vae":"ae.safetensors","tasks":["create","control","edit","remove","recolor","outpaint"],"steps":9},
"qwen21":{"name":"Qwen Image 2.1","about":"Generation, reference edits & native transparency","unet":"qwen_image_2.1_int8_convrot.safetensors","clip":"qwen3vl_8b_fp8_scaled.safetensors","vae":"qwen_image_2.1_vae_bf16.safetensors","tasks":["create","edit","remove","recolor","outpaint","cutout"],"steps":25},
"layered":{"name":"Qwen Image Layered","about":"Decompose into separate RGBA layers","unet":"qwen_image_layered_fp8mixed.safetensors","clip":"qwen_2.5_vl_7b_fp8_scaled.safetensors","vae":"qwen_image_layered_vae.safetensors","tasks":["layers"],"steps":20}
}
CONTROL="Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors"
def find_model(name,category):
    cats=[category]+(["clip"] if category=="text_encoders" else [])
    return next((root/cat/name for root in MODEL_ROOTS for cat in cats if (root/cat/name).is_file()),None)
def catalog():
    result=[]
    for key,m in REGISTRY.items():
        missing=[m[f] for f,c in [("unet","diffusion_models"),("clip","text_encoders"),("vae","vae")] if not find_model(m[f],c)]
        result.append(dict(id=key,**m,available=not missing,missing=missing,control_available=bool(find_model(CONTROL,"model_patches"))))
    return result
def node(kind,**inputs): return {"class_type":kind,"inputs":inputs}
def build(spec,job_id,source=None,mask=None,control=None,refs=None):
    key=spec["model"]; m=REGISTRY[key]; edit=source is not None; task=spec["task"]
    w,h=spec["width"],spec["height"]
    g={"1":node("UNETLoader",unet_name=m["unet"],weight_dtype="default"),
       "2":node("CLIPLoader",clip_name=m["clip"],type="minimax" if key=="h3" else "lumina2" if key=="zimage" else "qwen_image"),
       "3":node("VAELoader",vae_name=m["vae"])}
    if edit:g["10"]=node("LoadImage",image=source)
    prompt=spec["prompt"]
    if task=="remove":prompt="Remove the selected object and naturally reconstruct the background. "+prompt
    if task=="outpaint":prompt="Extend the scene naturally into the blank border, preserve the existing image. "+prompt
    if task=="cutout":prompt="Remove the background. Keep the subject on a transparent background. "+prompt
    model=["1",0]
    if key=="h3":
        p=dict(clip=["2",0],vae=["3",0],prompt=prompt,width=w,height=h)
        if edit:p["images.image_0"]=["10",0]
        for i,r in enumerate(refs or [],1 if edit else 0):
            g[str(50+i)]=node("LoadImage",image=r);p[f"images.image_{i}"]=[str(50+i),0]
        g["5"]=node("MiniMaxH3ImageConditioning",**p)
        pos=["5",0];latent=["5",1];steps=6;cfg=1;sampler="euler"
        g["6"]=node("ConditioningZeroOut",conditioning=pos);neg=["6",0]
    elif key=="qwen21":
        p=dict(clip=["2",0],vae=["3",0],prompt=prompt,negative_prompt="",resolution=0 if edit else w)
        if edit:p["images.image_1"]=["10",0]
        for i,r in enumerate(refs or [],2 if edit else 1):
            g[str(50+i)]=node("LoadImage",image=r);p[f"images.image_{i}"]=[str(50+i),0]
        g["5"]=node("TextEncodeQwenImage21",**p)
        pos=["5",0];neg=["5",1];latent=["5",2]
        if not edit and (w!=h or refs):
            g["4"]=node("EmptyFlux2LatentImage",width=w,height=h,batch_size=1);latent=["4",0]
        steps=spec.get("steps",25);cfg=1;sampler="euler"
    elif key=="layered":
        g["4"]=node("ModelSamplingAuraFlow",model=model,shift=1.0);model=["4",0]
        g["5"]=node("CLIPTextEncode",clip=["2",0],text=prompt)
        g["6"]=node("CLIPTextEncode",clip=["2",0],text="")
        g["11"]=node("VAEEncode",pixels=["10",0],vae=["3",0])
        g["12"]=node("ReferenceLatent",conditioning=["5",0],latent=["11",0])
        g["13"]=node("ReferenceLatent",conditioning=["6",0],latent=["11",0])
        g["14"]=node("EmptyQwenImageLayeredLatentImage",width=w,height=h,layers=spec.get("layers",2),batch_size=1)
        pos=["12",0];neg=["13",0];latent=["14",0];steps=spec.get("steps",20);cfg=2.5;sampler="euler"
    else:
        g["4"]=node("ModelSamplingAuraFlow",model=model,shift=3.0);model=["4",0]
        g["5"]=node("CLIPTextEncode",clip=["2",0],text=prompt)
        g["6"]=node("ConditioningZeroOut",conditioning=["5",0])
        g["14"]=node("EmptySD3LatentImage",width=w,height=h,batch_size=1)
        pos=["5",0];neg=["6",0];latent=["14",0];steps=9;cfg=1;sampler="res_multistep"
        if edit or task=="control":
            g["15"]=node("ModelPatchLoader",name=CONTROL)
            p=dict(model=model,model_patch=["15",0],vae=["3",0],strength=spec.get("strength",0.8))
            if control:g["16"]=node("LoadImage",image=control);p["image"]=["16",0]
            if edit and task!="control":
                p["inpaint_image"]=["10",0]
                if mask:g["17"]=node("LoadImageMask",image=mask,channel="red");p["mask"]=["17",0]
            g["18"]=node("ZImageFunControlnet",**p);model=["18",0];steps=8
    g["7"]=node("KSampler",model=model,positive=pos,negative=neg,latent_image=latent,seed=spec["seed"],steps=steps,cfg=cfg,sampler_name=sampler,scheduler="simple",denoise=1.0)
    samples=["7",0]
    if key=="layered":
        g["19"]=node("LatentCutToBatch",samples=samples,dim="t",slice_size=1);samples=["19",0]
    g["8"]=node("VAEDecode",samples=samples,vae=["3",0])
    g["9"]=node("SaveImage",images=["8",0],filename_prefix=f"studio/{job_id}")
    return g

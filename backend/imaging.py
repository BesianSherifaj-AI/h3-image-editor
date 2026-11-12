import io,uuid,hashlib,math
from pathlib import Path
import numpy as np
from PIL import Image,ImageOps,ImageFilter,ImageColor
import cv2
cv2.setNumThreads(2)
Image.MAX_IMAGE_PIXELS=24_000_000
MAX_BYTES=40*1024*1024
def decode(raw):
    if not raw or len(raw)>MAX_BYTES:raise ValueError("Use PNG, JPEG or WebP up to 40 MB.")
    try:
        im=Image.open(io.BytesIO(raw))
        if im.format not in ("PNG","JPEG","WEBP"):raise ValueError("Use PNG, JPEG or WebP.")
        if im.width*im.height>24_000_000 or max(im.size)>8192:raise ValueError("Image exceeds 24 megapixels or 8192 pixels per side.")
        im.load()
        return ImageOps.exif_transpose(im).convert("RGBA")
    except (Image.DecompressionBombError,Image.DecompressionBombWarning) as e:raise ValueError("Image is too large.") from e
    except Exception as e:raise ValueError("Unreadable or unsupported image.") from e
def composite_mask(original,edited,mask):
    if original.size!=mask.size:raise ValueError("Mask must match original dimensions.")
    edited=edited.convert("RGBA").resize(original.size,Image.Resampling.LANCZOS)
    return Image.composite(edited,original.convert("RGBA"),mask.convert("L"))

def feather_mask(mask,radius):
    """Blend inward only: every originally protected pixel remains protected."""
    if not radius:return mask.convert('L')
    a=np.array(mask.convert('L'))
    distance=cv2.distanceTransform((a>0).astype(np.uint8),cv2.DIST_L2,5)
    return Image.fromarray(np.minimum(a,np.clip(distance/float(radius),0,1)*255).astype(np.uint8))

def tint_preserve_light(original,color):
    """Replace color while keeping source luminance, texture, highlights and alpha."""
    rgba=np.asarray(original.convert('RGBA'),dtype=np.float32)/255
    weights=np.array([.3,.59,.11],dtype=np.float32)
    luminance=np.sum(rgba[:,:,:3]*weights,axis=2,keepdims=True)
    tint=np.array(ImageColor.getrgb(color),dtype=np.float32)/255
    rgb=tint+luminance-float(np.dot(tint,weights))
    low=rgb.min(axis=2,keepdims=True)
    rgb=np.where(low<0,luminance+(rgb-luminance)*luminance/np.maximum(luminance-low,1e-8),rgb)
    high=rgb.max(axis=2,keepdims=True)
    rgb=np.where(high>1,luminance+(rgb-luminance)*(1-luminance)/np.maximum(high-luminance,1e-8),rgb)
    result=Image.fromarray(np.clip(np.rint(rgb*255),0,255).astype(np.uint8))
    result.putalpha(original.convert('RGBA').getchannel('A'))
    return result

def repair_context_mask(target,margin,protect=None):
    """Include nearby contact shadows while excluding other selected foreground objects."""
    selected=np.array(target.convert('L'))
    expanded=cv2.dilate(selected,np.ones((margin*2+1,margin*2+1),np.uint8)) if margin else selected.copy()
    if protect is not None:
        if protect.size!=target.size:raise ValueError('Protected selection dimensions changed.')
        protected=np.array(protect.convert('L'))>127
        overlap=np.count_nonzero(protected&(selected>127))/max(1,np.count_nonzero(selected>127))
        if overlap>.05:raise ValueError('The protected object overlaps the removal target. Refine the selection before removing it.')
        protected=cv2.dilate(protected.astype(np.uint8),np.ones((5,5),np.uint8))>0
        expanded[protected]=0
    return Image.fromarray(expanded)

def harmonize_repair(original,edited,mask):
    """Match an AI repair's boundary lighting; retain original alpha and protected pixels."""
    dst=np.array(original.convert('RGB'))[:,:,::-1].copy()
    src=np.array(edited.convert('RGB').resize(original.size,Image.Resampling.LANCZOS))[:,:,::-1].copy()
    m=np.where(np.array(mask.convert('L'))>0,255,0).astype(np.uint8)
    # OpenCV requires an interior boundary. Final composition still enforces the exact mask.
    m[[0,-1],:]=0;m[:,[0,-1]]=0
    x,y,w,h=cv2.boundingRect(m)
    if w<3 or h<3:return composite_mask(original,edited,mask)
    repaired=cv2.seamlessClone(src,dst,m,(x+w//2,y+h//2),cv2.NORMAL_CLONE)
    im=Image.fromarray(repaired[:,:,::-1]);im.putalpha(original.getchannel('A'))
    return composite_mask(original,im,mask)
def outpaint(original,pad):
    pad=int(pad)
    if not 32<=pad<=512:raise ValueError("Border must be 32–512 pixels.")
    canvas=Image.new("RGBA",(original.width+2*pad,original.height+2*pad),(240,240,240,255))
    if max(canvas.size)>2048:raise ValueError("Reduce image size or border to stay within 2048 pixels.")
    canvas.paste(original,(pad,pad))
    mask=Image.new("L",canvas.size,255);mask.paste(0,(pad,pad,pad+original.width,pad+original.height))
    return canvas,mask
def blend_layer(canvas,source,xy,mode):
    """W3C source-over blending in sRGB, matching Canvas blend modes and alpha."""
    if mode=='normal':
        canvas.alpha_composite(source,xy);return
    x,y=xy;left=max(0,x);top=max(0,y);right=min(canvas.width,x+source.width);bottom=min(canvas.height,y+source.height)
    if right<=left or bottom<=top:return
    bounds=(left,top,right,bottom)
    base=np.asarray(canvas.crop(bounds),dtype=np.float32)/255
    src=np.asarray(source.crop((left-x,top-y,right-x,bottom-y)),dtype=np.float32)/255
    cb,cs=base[:,:,:3],src[:,:,:3];ab,as_=base[:,:,3:4],src[:,:,3:4]
    if mode=='multiply':mixed=cb*cs
    elif mode=='screen':mixed=1-(1-cb)*(1-cs)
    elif mode=='overlay':mixed=np.where(cb<=.5,2*cb*cs,1-2*(1-cb)*(1-cs))
    elif mode=='darken':mixed=np.minimum(cb,cs)
    elif mode=='lighten':mixed=np.maximum(cb,cs)
    elif mode=='difference':mixed=np.abs(cb-cs)
    else:raise ValueError('Unsupported layer blend mode.')
    alpha=as_+ab*(1-as_)
    premult=as_*(1-ab)*cs+as_*ab*mixed+(1-as_)*ab*cb
    rgb=np.divide(premult,alpha,out=np.zeros_like(premult),where=alpha>0)
    rgba=np.concatenate((rgb,alpha),axis=2)
    canvas.paste(Image.fromarray(np.clip(np.rint(rgba*255),0,255).astype(np.uint8)),(left,top))

def transformed_layer(layer,load,apply_opacity=True):
        im=load(layer["asset"]).copy()
        scale=float(layer.get("scale",1));opacity=float(layer.get("opacity",1))
        if not math.isfinite(scale) or not .05<=scale<=4 or not 0<=opacity<=1:raise ValueError("Invalid layer transform.")
        tw,th=max(1,round(im.width*scale)),max(1,round(im.height*scale))
        if tw*th>24_000_000:raise ValueError("Layer is too large.")
        im=im.resize((tw,th),Image.Resampling.LANCZOS)
        if layer.get('flip_x'):im=ImageOps.mirror(im)
        if layer.get('flip_y'):im=ImageOps.flip(im)
        angle=float(layer.get("rotation",0))
        if not math.isfinite(angle):raise ValueError("Invalid rotation.")
        im=im.rotate(-angle,resample=Image.Resampling.BICUBIC,expand=True)
        if layer.get("color"):
            color=layer["color"]
            tint=Image.new("RGBA",im.size,color)
            tint.putalpha(im.getchannel("A"));im=tint
        if apply_opacity:im.putalpha(im.getchannel("A").point(lambda a:round(a*opacity)))
        x,y=int(layer.get("x",0)),int(layer.get("y",0))
        if abs(x)>8192 or abs(y)>8192:raise ValueError("Layer position out of range.")
        # Match canvas preview: rotate about the scaled image's center.
        return im,(round(x+(tw-im.width)/2),round(y+(th-im.height)/2))

def compose(size,layers,load):
    w,h=size
    if not (32<=w<=4096 and 32<=h<=4096) or w*h>16_777_216:raise ValueError("Canvas must be 32–4096 pixels.")
    canvas=Image.new("RGBA",(w,h))
    for layer in layers:
        if not layer.get("visible",True):continue
        im,xy=transformed_layer(layer,load)
        blend_layer(canvas,im,xy,layer.get('blend','normal'))
    return canvas
def control_image(im,mode):
    a=np.array(im.convert("RGB"))
    if mode=="canny":a=cv2.Canny(a,90,180)
    elif mode=="gray":a=cv2.cvtColor(a,cv2.COLOR_RGB2GRAY)
    elif mode!="provided":raise ValueError("Choose canny, gray or provided guide.")
    return Image.fromarray(a).convert("RGBA")
def track(frames,mask):
    if len(frames)<2 or len(frames)>24:raise ValueError("Choose 2–24 ordered frames.")
    if any(f.size!=frames[0].size for f in frames) or mask.size!=frames[0].size:raise ValueError("Frames and mask must share dimensions.")
    if max(frames[0].size)>1280:raise ValueError("Tracking frames must be at most 1280 pixels per side.")
    result=[mask.convert("L")];conf=[]
    prev=cv2.cvtColor(np.array(frames[0].convert("RGB")),cv2.COLOR_RGB2GRAY)
    for frame in frames[1:]:
        cur=cv2.cvtColor(np.array(frame.convert("RGB")),cv2.COLOR_RGB2GRAY)
        # Backward flow maps current pixel coordinates into the previous frame.
        flow=cv2.calcOpticalFlowFarneback(cur,prev,None,.5,3,19,3,5,1.2,0)
        yy,xx=np.mgrid[:cur.shape[0],:cur.shape[1]].astype(np.float32)
        warped=cv2.remap(np.array(result[-1]),xx+flow[:,:,0],yy+flow[:,:,1],cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT)
        result.append(Image.fromarray(warped));conf.append(float(np.mean(np.abs(cur.astype(float)-cv2.remap(prev,xx+flow[:,:,0],yy+flow[:,:,1],cv2.INTER_LINEAR).astype(float)))))
        prev=cur
    return result,conf

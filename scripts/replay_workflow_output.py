"""Reapply the saved Studio finishing step to a ComfyUI output, on CPU."""
import argparse,json
from pathlib import Path
from PIL import Image
import atelier_imaging as imaging


def replay(job,load,edited):
    spec=job['spec']
    if spec.get('_mask') and spec.get('task')!='layers':
        if spec.get('_edit_crop'):
            x0,y0,x1,y1=spec['_edit_crop'];full=load(spec['_original'])
            full.paste(edited.resize((x1-x0,y1-y0),Image.Resampling.LANCZOS),(x0,y0));edited=full
        original,mask=load(spec['_original']),load(spec['_mask'])
        if spec.get('_heal') or spec.get('task')=='remove':
            return imaging.harmonize_repair(original,edited,mask)
        return imaging.composite_mask(original,edited,mask)
    return edited


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,help='A newly rendered ComfyUI PNG; defaults to the saved raw output.')
    parser.add_argument('--output',type=Path,help='Destination PNG; defaults to reproduced.png beside this script.')
    args=parser.parse_args();root=Path(__file__).resolve().parent
    job=json.loads((root/'job.json').read_text(encoding='utf-8'))
    if job['spec']['model'] in ('vision','segment','samtrack'):
        parser.error('This finishing tool is for image generation/editing outputs.')
    def load(id):
        path=(root/'studio-assets'/(id+'.png')).resolve()
        if path.parent!=(root/'studio-assets').resolve():raise ValueError('Invalid asset path.')
        return imaging.decode(path.read_bytes())
    if args.raw:edited=imaging.decode(args.raw.read_bytes())
    else:
        output=job['outputs'][0];id=output.get('raw_asset') or output['id'];edited=load(id)
    dest=args.output or root/'reproduced.png'
    if dest.exists():parser.error('Output already exists. Choose a new --output path.')
    result=replay(job,load,edited);result.save(dest,'PNG',compress_level=4)
    print(str(dest.resolve()))


if __name__=='__main__':main()

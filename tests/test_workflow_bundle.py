import io,json,subprocess,sys,tempfile,time,unittest,uuid,zipfile
from pathlib import Path
from PIL import Image,ImageDraw
from test_core import a


class WorkflowBundleTests(unittest.TestCase):
    def test_export_retains_raw_original_mask_and_replays_final_pixels(self):
        before=Image.new('RGBA',(64,48),(100,120,130,170))
        mask=Image.new('L',before.size);ImageDraw.Draw(mask).rectangle((12,8,40,32),fill=150)
        raw=Image.new('RGBA',(128,96),(20,60,210,240))
        source=a.put_image(before)['id'];mid=a.put_image(mask)['id'];rid=a.put_image(raw)['id']
        final=a.put_image(a.imaging.composite_mask(before,raw,mask));final['raw_asset']=rid
        id=str(uuid.uuid4());now=time.time()
        spec={'model':'h3','task':'edit','source':source,'mask':mid,'_original':source,'_mask':mid,'references':[]}
        with a.db() as c:c.execute('INSERT INTO jobs(id,spec,state,message,outputs,created,updated) VALUES (?,?,?,?,?,?,?)',(id,json.dumps(spec),'complete','test',json.dumps([final]),now,now))
        (a.DATA/'receipts'/(id+'.workflow.json')).write_text(json.dumps({'9':{'class_type':'SaveImage','inputs':{}}}))
        archive=a.export_job(id)
        with tempfile.TemporaryDirectory() as target,zipfile.ZipFile(io.BytesIO(archive.body)) as z:
            self.assertIsNone(z.testzip())
            for asset in [source,mid,rid,final['id']]:self.assertIn('studio-assets/'+asset+'.png',z.namelist())
            self.assertIn('reproduce.py',z.namelist());self.assertIn('atelier_imaging.py',z.namelist())
            z.extractall(target)
            run=subprocess.run([sys.executable,str(Path(target)/'reproduce.py')],capture_output=True,text=True,timeout=20)
            self.assertEqual(run.returncode,0,run.stderr)
            reproduced=Image.open(Path(target)/'reproduced.png').convert('RGBA')
            self.assertEqual(reproduced.tobytes(),a.load(final['id']).tobytes())
            again=subprocess.run([sys.executable,str(Path(target)/'reproduce.py')],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(again.returncode,0);self.assertIn('already exists',again.stderr)

    def test_replay_preserves_crop_and_repair_finishing(self):
        code=(a.ROOT/'scripts/replay_workflow_output.py').read_text()
        namespace={'__name__':'bundle_test'}
        from unittest.mock import patch
        with patch.dict(sys.modules,{'atelier_imaging':a.imaging}):exec(compile(code,'reproduce.py','exec'),namespace)
        original=Image.new('RGBA',(64,64),'gray');mask=Image.new('L',original.size);ImageDraw.Draw(mask).rectangle((15,15,35,35),fill=200)
        raw=Image.new('RGBA',(64,64),'green');assets={'original':original,'mask':mask}
        load=lambda id:assets[id].copy()
        spec={'task':'remove','_original':'original','_mask':'mask','_edit_crop':[10,10,42,42]}
        edited=original.copy();edited.paste(raw.resize((32,32),Image.Resampling.LANCZOS),(10,10))
        expected=a.imaging.harmonize_repair(original,edited,mask)
        actual=namespace['replay']({'spec':spec},load,raw)
        self.assertEqual(expected.tobytes(),actual.tobytes())


if __name__=='__main__':unittest.main()

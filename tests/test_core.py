import unittest,tempfile,os,sys,json,time,uuid,io,zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
tmp=tempfile.TemporaryDirectory();os.environ['STUDIO_DATA']=tmp.name
from backend import app as a,imaging
from fastapi.testclient import TestClient
a.init_db()
# Unit tests validate queue/workflow logic, not multi-gigabyte model weights.
# Isolated placeholder filenames keep this suite reproducible on CPU-only CI.
model_fixture=Path(tmp.name)/'model-fixtures'
a.workflows.MODEL_ROOTS=[model_fixture]
for model in a.workflows.REGISTRY.values():
    for key,category in [('unet','diffusion_models'),('clip','text_encoders'),('vae','vae')]:
        target=model_fixture/category/model[key];target.parent.mkdir(parents=True,exist_ok=True);target.touch()
for name,category in [(a.helpers.VISION,'text_encoders'),(a.helpers.SAM,'checkpoints'),(a.workflows.CONTROL,'model_patches')]:
    target=model_fixture/category/name;target.parent.mkdir(parents=True,exist_ok=True);target.touch()

class CoreTests(unittest.TestCase):
    def setUp(self):
        with a.db() as c:c.execute('DELETE FROM jobs');c.execute('DELETE FROM settings')
    def job(self,state='queued',pid=None):
        j=str(uuid.uuid4());now=time.time()
        with a.db() as c:c.execute('INSERT INTO jobs(id,spec,state,message,prompt_id,created,updated) VALUES (?,?,?,?,?,?,?)',(j,json.dumps({'model':'h3','task':'create'}),state,'',pid,now,now))
        return a.get_job(j)
    def test_mask_preserves_exact_rgba(self):
        rng=np.random.default_rng(42);raw=rng.integers(0,256,(64,64,4),dtype=np.uint8)
        original=Image.fromarray(raw);mask=Image.new('L',(64,64));ImageDraw.Draw(mask).rectangle((20,20,40,40),fill=255)
        result=np.array(imaging.composite_mask(original,Image.new('RGBA',(32,32),'red'),mask))
        np.testing.assert_array_equal(result[np.array(mask)==0],raw[np.array(mask)==0])
    def test_outpaint_preserves_center(self):
        im=Image.new('RGBA',(64,96),(10,20,30,100));canvas,mask=imaging.outpaint(im,32)
        self.assertEqual(canvas.crop((32,32,96,128)).tobytes(),im.tobytes());self.assertEqual(mask.getpixel((32,32)),0);self.assertEqual(mask.getpixel((0,0)),255)
    def test_feather_blends_inward_only(self):
        mask=Image.new('L',(64,64));ImageDraw.Draw(mask).rectangle((10,10,54,54),fill=255)
        feather=imaging.feather_mask(mask,8);a0=np.array(mask);a1=np.array(feather)
        self.assertTrue(np.all(a1[a0==0]==0));self.assertEqual(a1[32,32],255);self.assertLess(a1[10,32],40)
        raw=np.random.default_rng(4).integers(0,256,(64,64,4),dtype=np.uint8)
        result=np.array(imaging.composite_mask(Image.fromarray(raw),Image.new('RGBA',(64,64),'blue'),feather))
        np.testing.assert_array_equal(raw[a0==0],result[a0==0])
    def test_composition_rotation_center_and_alpha(self):
        im=Image.new('RGBA',(40,20),(255,0,0,128))
        out=imaging.compose((100,100),[{'asset':'x','x':30,'y':40,'rotation':90}],lambda _:im)
        self.assertEqual(out.getbbox(),(40,30,60,70));self.assertEqual(out.getpixel((50,50)),(255,0,0,128))
    def test_tracking_translation(self):
        rng=np.random.default_rng(8);arr=rng.integers(0,256,(128,128,3),dtype=np.uint8)
        f1=Image.fromarray(arr);f2=Image.new('RGB',f1.size);f2.paste(f1,(4,2))
        m=Image.new('L',f1.size);ImageDraw.Draw(m).rectangle((40,40,70,70),fill=255)
        result,_=imaging.track([f1,f2],m);b=result[1].getbbox();self.assertTrue(abs(b[0]-44)<=1 and abs(b[1]-42)<=1,b)
    def test_tint_preserves_shadow_highlight_texture_and_alpha(self):
        raw=np.random.default_rng(31).integers(0,256,(64,64,4),dtype=np.uint8)
        raw[0,0,:3]=0;raw[0,1,:3]=255
        original=Image.fromarray(raw);edited=np.array(imaging.tint_preserve_light(original,'#204984'))
        weights=np.array([.3,.59,.11])
        self.assertLessEqual(np.abs((edited[:,:,:3].astype(float)-raw[:,:,:3])@weights).max(),.51)
        np.testing.assert_array_equal(edited[:,:,3],raw[:,:,3])
        np.testing.assert_array_equal(edited[0,:2,:3],raw[0,:2,:3])
        self.assertTrue(np.any(edited[2:,:,:3]!=raw[2:,:,:3]));self.assertEqual(original.tobytes(),raw.tobytes())
    def test_bad_images_and_ids(self):
        with self.assertRaises(ValueError):imaging.decode(b'not an image')
        with self.assertRaises(ValueError):a.asset_path('../../private')
    def test_game_defers_without_runtime_start(self):
        j=self.job()
        with patch.object(a,'game_running',return_value=True),patch.object(a,'runtime_ready',return_value=False),patch.object(a,'start_runtime') as start:a.worker_tick();start.assert_not_called()
        self.assertEqual(a.get_job(j['id'])['state'],'queued')
    def test_pause_defers(self):
        self.job();a.set_setting('paused',True)
        with patch.object(a,'game_running',return_value=False),patch.object(a,'runtime_ready',return_value=False),patch.object(a,'start_runtime') as start:a.worker_tick();start.assert_not_called()
    def test_game_interruption_remains_cancelled_after_renderer_reports_error(self):
        j=self.job('running',str(uuid.uuid4()));calls=[]
        def api(method,path,**kw):
            calls.append((method,path,kw))
            return {'queue_running':[[1,j['prompt_id']]],'queue_pending':[]} if path=='/queue' else {}
        with patch.object(a,'runtime_ready',return_value=True),patch.object(a,'game_running',return_value=True),patch.object(a,'api',side_effect=api):a.reconcile(j)
        stopped=a.get_job(j['id']);self.assertEqual(stopped['state'],'cancelling');self.assertEqual(stopped['cancel'],1)
        self.assertIn(('POST','/interrupt',{'json':{'prompt_id':j['prompt_id']}}),calls)
        a.finish(stopped,{'status':{'status_str':'error','messages':[['execution_interrupted',{}]]}})
        stopped=a.get_job(j['id']);self.assertEqual(stopped['state'],'cancelled');self.assertEqual(stopped['message'],'Cancelled for game safety')
    def test_foreign_queue_not_touched(self):
        j=self.job()
        with patch.object(a,'game_running',return_value=False),patch.object(a,'runtime_ready',return_value=True),patch.object(a,'foreign_busy',return_value=True),patch.object(a,'api') as api:a.worker_tick();api.assert_not_called()
        self.assertEqual(a.get_job(j['id'])['state'],'deferred')
    def test_cancel_pending_exact_id(self):
        j=self.job('submitted',str(uuid.uuid4()));a.update(j['id'],cancel=1);j=a.get_job(j['id']);calls=[]
        def api(method,path,**kw):
            calls.append((method,path,kw));return {'queue_pending':[[0,j['prompt_id']],[1,'foreign']], 'queue_running':[]} if path=='/queue' else {}
        with patch.object(a,'runtime_ready',return_value=True),patch.object(a,'game_running',return_value=False),patch.object(a,'api',side_effect=api):a.reconcile(j)
        self.assertIn(('POST','/queue',{'json':{'delete':[j['prompt_id']]}}),calls)
    def test_lost_submission_never_retried(self):
        j=self.job('uncertain',str(uuid.uuid4()));a.update(j['id'],updated=time.time()-60)
        with patch.object(a,'runtime_ready',return_value=True),patch.object(a,'game_running',return_value=False),patch.object(a,'api',return_value={}) as api:a.worker_tick()
        self.assertFalse(any(x.args[0]=='POST' for x in api.call_args_list));self.assertEqual(a.get_job(j['id'])['state'],'uncertain')
    def test_cancel_during_prepare(self):
        j=self.job()
        def prepare(spec,jid):a.cancel(jid);return {}
        with patch.object(a,'runtime_ready',return_value=True),patch.object(a,'game_running',return_value=False),patch.object(a,'foreign_busy',return_value=False),patch.object(a,'start_runtime'),patch.object(a,'prepare',side_effect=prepare),patch.object(a,'api',return_value={}) as api:a.worker_tick()
        self.assertEqual(a.get_job(j['id'])['state'],'cancelled');self.assertFalse(any(x.args[0]=='POST' for x in api.call_args_list))
    def test_origin_and_export(self):
        client=TestClient(a.app)
        self.assertEqual(client.post('/api/queue',json={'paused':True},headers={'Origin':'https://evil.example'}).status_code,403)
        source=a.put_image(Image.new('RGBA',(32,32),'red'))['id'];ref=a.put_image(Image.new('RGBA',(32,32),'blue'))['id']
        p=a.save_project({'payload':{'source':source,'original':ref,'references':[{'id':ref}],'layers':[]}})
        res=client.get('/api/projects/'+p['id']+'/export');z=zipfile.ZipFile(io.BytesIO(res.content));self.assertIn('assets/'+ref+'.png',z.namelist());self.assertIn('project.json',z.namelist())
    def test_project_restore_remaps_and_rejects_paths(self):
        client=TestClient(a.app);asset=a.put_image(Image.new('RGBA',(32,32),(1,2,3,80)))['id']
        p=a.save_project({'name':'Original','payload':{'source':asset,'layers':[{'asset':asset,'rotation':15}],'references':[{'id':asset}]}})
        archive=client.get('/api/projects/'+p['id']+'/export').content
        restored=client.post('/api/projects/import',files={'file':('project.zip',archive,'application/zip')});self.assertEqual(restored.status_code,200)
        r=a.read_project(restored.json()['id']);self.assertNotEqual(r['id'],p['id']);self.assertNotEqual(r['payload']['source'],asset)
        self.assertEqual(a.load(r['payload']['source']).tobytes(),a.load(asset).tobytes());self.assertEqual(r['payload']['layers'][0]['rotation'],15)
        self.assertEqual(r['payload']['references'][0]['id'],r['payload']['source'])
        bad=io.BytesIO(archive)
        with zipfile.ZipFile(bad,'a') as z:z.writestr('../outside.txt','bad')
        self.assertEqual(client.post('/api/projects/import',files={'file':('bad.zip',bad.getvalue(),'application/zip')}).status_code,400)
    def test_typed_malformed_requests_are_client_errors(self):
        client=TestClient(a.app);source=a.put_image(Image.new('RGBA',(32,32),'red'))['id']
        cases=[('/cpu',{'source':source,'action':'resize','width':None}),('/compose',{'width':32,'height':32,'layers':[None]}),('/compose',{'width':32,'height':32,'layers':[{'asset':source,'scale':[]}]}),('/projects',{'payload':[]}),('/projects',{'payload':{'layers':None}}),('/track',{'frames':None,'mask':source})]
        for route,payload in cases:
            with self.subTest(route=route,payload=payload):self.assertEqual(client.post('/api'+route,json=payload).status_code,400)
    def test_h3_reference_sockets_are_contiguous(self):
        spec={'model':'h3','task':'edit','prompt':'blue','width':512,'height':512,'seed':42}
        graph=a.workflows.build(spec,'test','source.png',refs=['ref.png'])
        self.assertEqual(graph['5']['inputs']['images.image_0'],['10',0]);self.assertIn('images.image_1',graph['5']['inputs']);self.assertNotIn('images.image_2',graph['5']['inputs'])

    def test_qwen_reference_sockets_are_contiguous(self):
        spec={'model':'qwen21','task':'create','prompt':'blue','width':512,'height':512,'seed':42}
        created=a.workflows.build(spec,'test',refs=['ref.png'])
        self.assertIn('images.image_1',created['5']['inputs']);self.assertNotIn('images.image_2',created['5']['inputs'])
        edited=a.workflows.build(dict(spec,task='edit'),'test','source.png',refs=['ref.png'])
        self.assertEqual(edited['5']['inputs']['images.image_1'],['10',0]);self.assertIn('images.image_2',edited['5']['inputs'])

if __name__=='__main__':unittest.main(verbosity=2)

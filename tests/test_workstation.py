import json,time,uuid,unittest,zipfile
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
from fastapi import HTTPException
from test_core import a
from backend import studio_plus as s,studio_pro as p,video_lab as v

class WorkstationTests(unittest.TestCase):
    def setUp(self):
        p.init()
        with a.db() as c:
            for table in ('jobs','creative_runs','projects','project_revisions','variation_batches','settings','videos'):c.execute('DELETE FROM '+table)
        self.source=a.put_image(Image.new('RGBA',(256,256),'red'))['id']
        mask=Image.new('L',(256,256));ImageDraw.Draw(mask).rectangle((70,70,160,160),fill=255)
        self.mask=a.put_image(mask)['id']
        self.payload={'source':self.source,'width':256,'height':256,'layers':[{'asset':self.source}]}
    def test_history_keeps_old_save_and_restores_separate_copy(self):
        first=a.save_project({'name':'Original','payload':self.payload})
        self.payload['form']={'prompt':'Second version'}
        a.save_project({'id':first['id'],'base_updated':first['updated'],'name':'Edited','payload':self.payload})
        rows=s.history(first['id']);self.assertEqual(len(rows),2)
        restored=s.restore(first['id'],rows[-1]['id'])
        self.assertNotEqual(restored['id'],first['id']);self.assertEqual(a.read_project(first['id'])['name'],'Edited')
        self.assertEqual(a.read_project(restored['id'])['payload']['form'],{})
    def test_history_bootstraps_legacy_projects_and_deduplicates_noop(self):
        first=a.save_project({'name':'Original','payload':self.payload})
        with a.db() as c:c.execute('DELETE FROM project_revisions')
        a.save_project({'id':first['id'],'name':'Original','payload':self.payload})
        self.assertEqual(len(s.history(first['id'])),1)
    def test_stale_project_save_is_rejected_without_data_loss(self):
        first=a.save_project({'name':'Original','payload':self.payload})
        a.save_project({'id':first['id'],'base_updated':first['updated'],'name':'Newer','payload':self.payload})
        with self.assertRaises(HTTPException) as ex:a.save_project({'id':first['id'],'base_updated':first['updated'],'name':'Stale','payload':self.payload})
        self.assertEqual(ex.exception.status_code,409);self.assertEqual(a.read_project(first['id'])['name'],'Newer')
    def batch(self,count=2,request=None,**spec):
        return s.variations(s.BatchSpec(request_id=request or str(uuid.uuid4()),count=count,spec={'model':'h3','task':'create','prompt':'A pear','width':256,'height':256,'seed':42}|spec))
    def test_variations_are_queued_atomically_and_idempotently(self):
        id=str(uuid.uuid4());first=self.batch(request=id);again=self.batch(request=id)
        self.assertEqual([j['id'] for j in first['jobs']],[j['id'] for j in again['jobs']])
        self.assertEqual([j['spec']['seed'] for j in first['jobs']],[42,43]);self.assertTrue(all(j['state']=='queued' for j in first['jobs']))
        with self.assertRaisesRegex(ValueError,'different variations'):self.batch(request=id,prompt='Different prompt')
    def test_variations_refuse_partial_submission_or_seed_overflow(self):
        for _ in range(7):self.batch(count=4)
        with self.assertRaisesRegex(ValueError,'queue space'):self.batch(count=4)
        with a.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],28)
        with self.assertRaisesRegex(ValueError,'lower seed'):self.batch(seed=9007199254740991)
    def test_refined_mask_shrinks_and_feathers_only_inward(self):
        out=p.refine_mask(p.MaskSpec(source=self.mask,grow=-4,feather=5));raw=np.array(a.load(self.mask).convert('L'));changed=np.array(a.load(out['id']).convert('L'))
        self.assertTrue(np.all(changed[raw==0]==0));self.assertEqual(changed[70,100],0);self.assertEqual(changed[100,100],255)
    def video(self,**values):
        id=str(uuid.uuid4())
        with a.db() as c:c.execute('INSERT INTO videos VALUES (?,?)',(id,json.dumps({'duration':12,'previews':{self.source:0}})))
        return v.VideoSpec(video=id,source=self.source,mask=self.mask,**values)
    def test_video_rejects_empty_frame_range_and_oversized_sam_batch(self):
        with self.assertRaisesRegex(ValueError,'one frame'):v.submit(self.video(duration=.5,fps=1))
        with self.assertRaisesRegex(ValueError,'24 frames'):v.submit(self.video(duration=4,fps=8,tracking='sam'))
    def test_sam_tracking_is_durable_and_then_processes_cpu_frame(self):
        run=v.submit(self.video(duration=1,fps=2,tracking='sam',action='blur'))
        folder=a.DATA/'videos'/run['id'];folder.mkdir(exist_ok=True)
        for i in (1,2):a.load(self.source).save(folder/f'source-{i:04d}.png')
        with patch.object(v,'command'),patch.object(a,'game_running',return_value=False):v.tick(run)
        run=p.read_run(run['id']);self.assertEqual(run['state'],'tracking');job=a.get_job(run['payload']['child']);self.assertEqual(job['spec']['model'],'samtrack')
        v.tick(run)
        with a.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        a.update(job['id'],state='complete',outputs=json.dumps([a.asset_info(self.mask),a.asset_info(self.mask)]))
        v.tick(p.read_run(run['id']));run=p.read_run(run['id']);self.assertEqual(run['state'],'processing');self.assertEqual(len(run['payload']['mask_coverage']),2)
        v.tick(run);self.assertEqual(len(p.read_run(run['id'])['payload']['outputs']),1)
    def test_sam_tracking_defers_while_gaming(self):
        run=v.submit(self.video(duration=1,fps=2,tracking='sam'))
        with patch.object(a,'game_running',return_value=True),patch.object(v,'command') as command:v.tick(run);command.assert_not_called()
        self.assertEqual(p.read_run(run['id'])['state'],'new')
    def test_sam_wrong_mask_count_fails_without_editing_frames(self):
        run=p.new_run('video',{'source':self.source,'frames':[self.source]*2,'masks':[self.mask]*2},'tracking');p.child(run,{'model':'samtrack'},'tracking');run=p.read_run(run['id'])
        a.update(run['payload']['child'],state='complete',outputs=json.dumps([a.asset_info(self.mask)]))
        with self.assertRaisesRegex(ValueError,'wrong number'):v.tick(run)
    def test_h3_object_edit_keeps_scene_context_at_useful_resolution(self):
        spec=a.validate_job(a.JobSpec(model='h3',task='edit',source=self.source,mask=self.mask,prompt='Replace the object'))
        graph=a.prepare(spec,str(uuid.uuid4()))
        self.assertNotIn('_edit_crop',spec)
        self.assertEqual((spec['width'],spec['height']),(1024,1024))
        self.assertEqual(graph['5']['inputs']['width'],1024)
        self.assertEqual(a.load(spec['_original']).size,(256,256))
    def test_video_export_contains_lossless_frames_masks_and_manifest(self):
        run=p.new_run('video',{'frames':[self.source],'outputs':[a.asset_info(self.source)],'masks':[self.mask],'fps':2,'tracking':'sam'},'complete')
        folder=a.DATA/'videos'/run['id'];folder.mkdir(exist_ok=True)
        response=v.export_frames(run['id'])
        with zipfile.ZipFile(response.path) as z:
            self.assertEqual(set(z.namelist()),{'manifest.json','original/0001.png','edited/0001.png','masks/0001.png'})
            self.assertEqual(z.read('original/0001.png'),a.asset_path(self.source).read_bytes())
            self.assertEqual(json.loads(z.read('manifest.json'))['tracking'],'sam')
        self.assertEqual(v.export_frames(run['id']).path,response.path)
    def test_video_export_refuses_incomplete_run(self):
        run=p.new_run('video',{},'processing')
        with self.assertRaisesRegex(ValueError,'Finish'):v.export_frames(run['id'])
    def test_cpu_video_uses_inward_feather_setting(self):
        run=p.new_run('video',{'frames':[self.source],'masks':[self.mask],'outputs':[],'index':0,'action':'recolor','color':'#0000ff','feather':8},'processing')
        v.tick(run);out=np.array(a.load(p.read_run(run['id'])['payload']['outputs'][0]['id']))
        self.assertEqual(out[69,100,0],255)
        self.assertGreater(out[70,100,0],out[100,100,0])
    def test_director_sees_selected_area_but_preserves_original_for_edit(self):
        run=p.director(p.DirectorSpec(source=self.source,mask=self.mask,instruction='Remove the selected object.',identify_selection=True,operation='remove'))
        p.director_tick(run);run=p.read_run(run['id']);job=a.get_job(run['payload']['child'])
        self.assertNotEqual(job['spec']['source'],self.source)
        self.assertEqual(run['payload']['source'],self.source)
        self.assertIn('magenta',job['spec']['prompt'])
        run['state']='planning_ready';run['payload']['vision_result']={'prompt':'Delete everything.','object':'green pear','reason':'Selected fruit'}
        p.director_tick(run);run=p.read_run(run['id'])
        self.assertEqual(run['state'],'approved');self.assertTrue(run['payload']['plan']['prompt'].startswith('Remove the selected object.'))
        self.assertIn('green pear',run['payload']['plan']['prompt']);self.assertNotIn('Delete everything',run['payload']['plan']['prompt'])
        p.director_tick(run);run=p.read_run(run['id']);self.assertEqual(a.get_job(run['payload']['child'])['spec']['source'],self.source)
        self.assertEqual(a.get_job(run['payload']['child'])['spec']['task'],'remove')
    def test_repair_matches_background_lighting_without_changing_protected_pixels(self):
        original=Image.new('RGBA',(256,256),(180,170,155,99));edited=Image.new('RGBA',(256,256),(235,225,210,255))
        mask=a.imaging.feather_mask(a.load(self.mask),8)
        result=np.array(a.imaging.harmonize_repair(original,edited,mask));before=np.array(original);m=np.array(mask)
        np.testing.assert_array_equal(result[m==0],before[m==0])
        self.assertLessEqual(np.max(np.abs(result[100,100].astype(int)-before[100,100].astype(int))),2)
    def test_automatic_remove_stops_when_vision_cannot_identify_selection(self):
        run=p.director(p.DirectorSpec(source=self.source,mask=self.mask,instruction='Remove the selected object.',identify_selection=True))
        run['state']='planning_ready';run['payload']['vision_result']={'prompt':'Remove object','object':'','reason':'Cannot identify'}
        p.director_tick(run);self.assertEqual(p.read_run(run['id'])['state'],'needs_review')
        with a.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

if __name__=='__main__':unittest.main()

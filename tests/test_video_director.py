import json,unittest,uuid
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
from test_core import a
from backend import studio_pro as p,video_lab as v

class VideoDirectorTests(unittest.TestCase):
    def setUp(self):
        p.init()
        with a.db() as c:
            c.execute('DELETE FROM jobs');c.execute('DELETE FROM creative_runs')
        self.source=a.put_image(Image.new('RGBA',(128,128),'red'))['id']
        mask=Image.new('L',(128,128));ImageDraw.Draw(mask).rectangle((40,40,70,70),fill=255)
        self.mask=a.put_image(mask)['id'];self.video=str(uuid.uuid4())
        with a.db() as c:c.execute('INSERT INTO videos VALUES (?,?)',(self.video,json.dumps({'duration':2,'previews':{self.source:0}})))
    def run_spec(self,**kw):
        with patch.object(a.helpers,'available',return_value={'segment':True,'vision':True}):
            return v.submit(v.VideoSpec(video=self.video,source=self.source,duration=1,fps=2,**kw))
    def finish_child(self,run,outputs):a.update(run['payload']['child'],state='complete',outputs=json.dumps(outputs))
    def test_game_interruption_keeps_video_attempts_without_retry(self):
        for stage in ('planning','selecting','reviewing','tracking','editing'):
            with self.subTest(stage=stage):
                r=p.new_run('video',{'attempts':[{'number':1,'url':'saved-preview'}]})
                p.child(r,{'model':'vision'},stage);r=p.read_run(r['id'])
                a.update(r['payload']['child'],state='cancelled',message='Cancelled for game safety')
                v.tick(r);r=p.read_run(r['id'])
                self.assertEqual(r['state'],'cancelled');self.assertEqual(r['payload']['attempts'][0]['url'],'saved-preview')
    def test_unpainted_video_plans_then_selects_without_duplicate_job(self):
        r=self.run_spec(prompt='Make the red object blue',action='h3')
        with patch.object(a,'game_running',return_value=False):v.tick(r)
        r=p.read_run(r['id']);self.assertEqual(r['state'],'planning');v.tick(r)
        self.finish_child(r,[{'text':json.dumps({'prompt':'make blue','object':'red object','reason':'requested'})}]);v.tick(r)
        r=p.read_run(r['id']);self.assertEqual(r['state'],'new')
        with patch.object(a,'game_running',return_value=False):v.tick(r)
        r=p.read_run(r['id']);self.assertEqual(r['state'],'selecting');self.finish_child(r,[{'id':self.mask}]);v.tick(r)
        r=p.read_run(r['id']);self.assertTrue(r['payload']['automatic_selection']);self.assertEqual(r['payload']['mask'],self.mask)
        with a.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],2)
    def test_explicit_object_skips_vision_planning_and_game_defers(self):
        r=self.run_spec(object='red object')
        with patch.object(a,'game_running',return_value=True):v.tick(r)
        self.assertEqual(p.read_run(r['id'])['state'],'new')
        with patch.object(a,'game_running',return_value=False):v.tick(r)
        r=p.read_run(r['id']);self.assertEqual(a.get_job(r['payload']['child'])['spec']['model'],'segment')
    def test_empty_automatic_selection_stops_before_edit(self):
        r=self.run_spec(object='object')
        with patch.object(a,'game_running',return_value=False):v.tick(r)
        r=p.read_run(r['id']);empty=a.put_image(Image.new('L',(128,128)))['id'];self.finish_child(r,[{'id':empty}]);v.tick(r)
        self.assertEqual(p.read_run(r['id'])['state'],'needs_review')
    def test_margin_is_applied_after_tracking_to_every_frame(self):
        r=p.new_run('video',{'mask_grow':4});self.assertTrue(v.selection_masks(r,[self.mask,self.mask]))
        self.assertEqual(r['payload']['raw_masks'],[self.mask,self.mask])
        for mid in r['payload']['masks']:self.assertEqual(a.load(mid).getpixel((36,40))[0],255)
    def test_lost_tracking_stops_without_silent_empty_edit(self):
        empty=a.put_image(Image.new('L',(128,128)))['id'];r=p.new_run('video',{})
        self.assertFalse(v.selection_masks(r,[self.mask,empty]));self.assertEqual(p.read_run(r['id'])['state'],'needs_review')
    def test_quality_checks_detect_corruption_outside_mask(self):
        out=a.load(self.source);out.putpixel((0,0),(0,0,0,255));bad=a.put_image(out)
        checks=v.frame_checks({'frames':[self.source],'outputs':[bad],'masks':[self.mask],'action':'h3'})
        self.assertFalse(checks['pass']);self.assertEqual(checks['frames'][0]['changed_protected_pixels'],1)
    def test_retry_retains_first_attempt_and_stops_at_budget(self):
        r=p.new_run('video',{'action':'h3','max_attempts':2,'attempt':1,'attempts':[{'number':1,'url':'first'}],'outputs':[{'id':self.source}],'index':1})
        review={'verdict':'retry','reason':'halo','correction':'Remove the bright halo'};v.finish_review(r,review)
        r=p.read_run(r['id']);self.assertEqual(r['state'],'processing');self.assertEqual(r['payload']['attempts'][0]['url'],'first');self.assertEqual(r['payload']['attempt'],2)
        r['payload']['attempts'].append({'number':2});v.finish_review(r,review)
        self.assertEqual(p.read_run(r['id'])['state'],'needs_review')
    def test_cpu_review_cannot_trigger_diffusion_retry(self):
        r=p.new_run('video',{'action':'blur','max_attempts':2,'attempt':1,'attempts':[{}]});v.finish_review(r,{'verdict':'retry','reason':'weak blur','correction':'More blur'})
        self.assertEqual(p.read_run(r['id'])['state'],'needs_review')
    def test_malformed_review_preserves_preview(self):
        r=p.new_run('video',{'action':'h3','attempts':[{}],'url':'saved'});p.child(r,{'model':'vision'},'reviewing');r=p.read_run(r['id']);self.finish_child(r,[{'text':'not JSON'}]);v.tick(r)
        r=p.read_run(r['id']);self.assertEqual(r['state'],'needs_review');self.assertEqual(r['payload']['url'],'saved')
    def test_retry_uses_original_frames_and_a_new_fixed_seed(self):
        r=p.new_run('video',{'frames':[self.source],'masks':[self.mask],'outputs':[],'index':0,'action':'h3','prompt':'make blue','correction':'keep outline','attempt':2,'seed':42},'processing')
        v.tick(r);j=a.get_job(p.read_run(r['id'])['payload']['child']);self.assertEqual(j['spec']['source'],self.source);self.assertEqual(j['spec']['seed'],43);self.assertIn('keep outline',j['spec']['prompt'])
    def test_temporal_check_distinguishes_stable_edit_from_color_flicker(self):
        def edited(color):return a.put_image(a.imaging.composite_mask(a.load(self.source),Image.new('RGBA',(128,128),color),a.load(self.mask)))
        blue=edited('blue');green=edited('green')
        spec={'frames':[self.source]*2,'masks':[self.mask]*2,'outputs':[blue,blue]}
        self.assertFalse(v.temporal_checks(spec)['warning'])
        spec['outputs']=[blue,green];self.assertTrue(v.temporal_checks(spec)['warning'])
    def test_temporal_warning_cannot_be_overruled_by_optimistic_ai_pass(self):
        r=p.new_run('video',{'action':'h3','attempt':1,'max_attempts':1,'attempts':[{}],'temporal_checks':{'warning':True}})
        v.finish_review(r,{'verdict':'pass','reason':'Looks fine','correction':''})
        self.assertEqual(p.read_run(r['id'])['state'],'needs_review')
    def test_trim_does_not_require_any_ai_model(self):
        with patch.object(a.helpers,'available',return_value={'vision':False,'segment':False}):
            r=v.submit(v.VideoSpec(video=self.video,source=self.source,action='trim'))
        self.assertEqual(r['state'],'new')
    def test_retry_seed_cannot_overflow(self):
        with self.assertRaisesRegex(ValueError,'lower seed'):self.run_spec(object='red',seed=2**53-1,max_attempts=2)
        with self.assertRaisesRegex(ValueError,'lower seed'):p.director(p.DirectorSpec(source=self.source,instruction='Make blue',seed=2**53-1,max_attempts=2))
    def test_boundary_blend_retains_protected_pixels_and_unblended_asset(self):
        raw=a.put_image(Image.new('RGBA',(128,128),'blue'))
        result=p.blend_result(self.source,raw,self.mask)
        self.assertEqual(result['unblended_asset'],raw['id']);self.assertTrue(p.protected_metric(self.source,result['id'],self.mask)['pass'])
    def test_tiny_boundary_selection_never_replaces_the_whole_image(self):
        mask=Image.new('L',(128,128));mask.putpixel((60,60),255);mid=a.put_image(mask)['id']
        raw=a.put_image(Image.new('RGBA',(128,128),'blue'));result=p.blend_result(self.source,raw,mid,0)
        self.assertTrue(p.protected_metric(self.source,result['id'],mid)['pass'])
    def test_incomplete_retry_cannot_export_incomplete_frames(self):
        r=p.new_run('video',{'outputs':[{'id':self.source}],'frames':[self.source]*2},'needs_review')
        with self.assertRaisesRegex(ValueError,'Finish the preview'):v.export_frames(r['id'])
    def test_completed_attempt_exports_while_next_attempt_is_running(self):
        import zipfile
        r=p.new_run('video',{'outputs':[],'frames':[self.source],'masks':[self.mask],'attempt':2,'attempts':[{'number':1,'outputs':[{'id':self.source}],'checks':{'pass':True},'review':{'verdict':'retry'}}]},'editing')
        (a.DATA/'videos'/r['id']).mkdir(exist_ok=True)
        response=v.export_frames(r['id'],1)
        with zipfile.ZipFile(response.path) as z:
            self.assertIn('edited/0001.png',z.namelist());self.assertEqual(json.loads(z.read('manifest.json'))['attempt'],1)
        with self.assertRaisesRegex(ValueError,'not ready'):v.export_frames(r['id'],2)

if __name__=='__main__':unittest.main()

"""Real CPU video import, tracked edit, audio encode and lossless export roundtrip."""
import io, json, shutil, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from test_core import a
from backend import studio_pro as p, video_lab as v


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class VideoRoundtrip(unittest.TestCase):
    def test_selection_matches_first_sample_on_high_fps_and_fractional_seek(self):
        """A 24 fps source must seed tracking with the exact preview, even at 4 fps."""
        p.init(); a.STOP.clear()
        client = TestClient(a.app)
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / 'moving.mp4'
            v.command(v.ffmpeg() + ['-f','lavfi','-i','testsrc2=size=192x128:rate=24:duration=3',
                '-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(clip)])
            uploaded = client.post('/api/pro/videos', files={'file':('moving.mp4',clip.read_bytes(),'video/mp4')})
            uploaded.raise_for_status(); video = uploaded.json()
            for start in (0, .5, .53, 1.17):
                with self.subTest(start=start):
                    preview = client.get(f"/api/pro/videos/{video['id']}/frame",params={'start':start})
                    preview.raise_for_status(); source = preview.json()['id']
                    run = v.submit(v.VideoSpec(video=video['id'],source=source,start=start,
                        action='trim',duration=.5,fps=4,auto_review=False))
                    with patch.object(a,'start_runtime',side_effect=AssertionError('Sampling must stay on CPU')):
                        v.tick(run)
                    payload = p.read_run(run['id'])['payload']
                    self.assertEqual(len(payload['frames']),2)
                    self.assertEqual(a.load(source).tobytes(),a.load(payload['frames'][0]).tobytes(),
                        'Selection preview and tracking seed frame must be identical')

    def test_cpu_tracking_preserves_protected_pixels_and_audio(self):
        p.init(); a.STOP.clear()
        with a.db() as c:
            c.execute('DELETE FROM creative_runs')
        client = TestClient(a.app)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for i in range(8):
                im = Image.new('RGB', (256, 192), '#253347'); d = ImageDraw.Draw(im)
                d.rectangle((65+i, 55, 135+i, 125), fill='#edba46')
                for j in range(7):
                    d.line((70+i+j*9, 60, 70+i+j*9, 120), fill='#563a27', width=3)
                im.save(folder / f'{i:02d}.png')
            clip = folder / 'fixture.mp4'
            v.command(v.ffmpeg() + ['-framerate','4','-i',str(folder/'%02d.png'),
                '-f','lavfi','-i','sine=frequency=440:duration=2','-t','2',
                '-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac',str(clip)])
            upload = client.post('/api/pro/videos', files={'file':('fixture.mp4',clip.read_bytes(),'video/mp4')})
            upload.raise_for_status(); video = upload.json()
            source = client.get('/api/pro/videos/'+video['id']+'/frame').json()
            mask = Image.new('L',(256,192)); ImageDraw.Draw(mask).rectangle((65,55,135,125),fill=255)
            mask_id = a.put_image(mask, 'Test selection')['id']
            run = v.submit(v.VideoSpec(video=video['id'],source=source['id'],mask=mask_id,
                action='blur',duration=2,fps=4,tracking='flow',auto_review=False,mask_grow=2))
            with patch.object(a,'start_runtime',side_effect=AssertionError('CPU export must not start GPU')):
                for _ in range(15):
                    v.tick(run); run = p.read_run(run['id'])
                    if run['state'] in p.END: break
            self.assertEqual(run['state'],'complete',run['payload'].get('message'))
            result = run['payload']
            self.assertTrue(result['quality_checks']['pass']); self.assertTrue(result['decode_verified'])
            self.assertEqual(len(result['outputs']),8)
            self.assertTrue(all(x['changed_selected_pixels']>0 for x in result['quality_checks']['frames']))
            output = Path(v.download(run['id']).path)
            probe = json.loads(v.command(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)]))
            self.assertEqual({s['codec_type'] for s in probe['streams']},{'audio','video'})
            self.assertAlmostEqual(float(probe['format']['duration']),2,delta=.1)
            with zipfile.ZipFile(v.export_frames(run['id']).path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(json.loads(archive.read('manifest.json'))['frames'],8)
            # Keep optional evidence outside source control for manual playback.
            import os
            if os.getenv('STUDIO_TEST_EVIDENCE'):
                evidence=Path(os.environ['STUDIO_TEST_EVIDENCE']); evidence.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(output,evidence/'linux-cpu-preview.mp4')
                shutil.copyfile(a.asset_path(result['comparison']['id']),evidence/'linux-cpu-comparison.png')
                (evidence/'linux-cpu-result.json').write_text(json.dumps({'state':run['state'],'checks':result['quality_checks'],'probe':probe},indent=2))

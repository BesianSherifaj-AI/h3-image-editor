import io,unittest
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient
from test_core import a
from backend import studio_tools as t,helpers

class CreativeToolsTests(unittest.TestCase):
    def setUp(self):
        with a.db() as c:
            c.execute('DELETE FROM jobs');c.execute('DELETE FROM settings')
        im=Image.new('RGBA',(128,96),'white');d=ImageDraw.Draw(im);d.rectangle((10,10,39,39),fill='red');d.rectangle((80,10,109,39),fill='red')
        self.source=a.put_image(im)['id']
    def wand(self,**values):return a.load(t.wand(t.WandSpec(source=self.source,x=20,y=20,**values))['id']).convert('L')
    def test_wand_connected_region_does_not_select_distant_equal_color(self):
        mask=self.wand(tolerance=0);self.assertEqual(mask.getbbox(),(10,10,40,40));self.assertEqual(mask.getpixel((90,20)),0)
    def test_wand_all_matching_colors_and_subtract(self):
        mask=self.wand(tolerance=0,contiguous=False);self.assertEqual(mask.getpixel((90,20)),255)
        previous=a.put_image(mask)['id'];out=self.wand(tolerance=0,mask=previous,combine='subtract')
        self.assertEqual(out.getbbox(),(80,10,110,40))
    def test_wand_tolerance_is_relative_to_clicked_color_not_neighbor_drift(self):
        raw=np.zeros((32,128,4),dtype=np.uint8);raw[:,:,0]=np.arange(128);raw[:,:,3]=255
        id=a.put_image(Image.fromarray(raw))['id'];out=a.load(t.wand(t.WandSpec(source=id,x=0,y=0,tolerance=10))['id'])
        self.assertEqual(out.convert('L').getbbox(),(0,0,11,32))
    def test_wand_transparent_pixels_ignore_hidden_rgb(self):
        raw=np.zeros((32,32,4),dtype=np.uint8);raw[:,:,:3]=np.random.default_rng(1).integers(0,255,(32,32,3),dtype=np.uint8)
        id=a.put_image(Image.fromarray(raw))['id'];out=a.load(t.wand(t.WandSpec(source=id,x=0,y=0,tolerance=0))['id'])
        self.assertEqual(out.convert('L').getextrema(),(255,255))
    def test_wand_outside_or_subtract_without_mask_rejected(self):
        with self.assertRaisesRegex(ValueError,'inside'):t.wand(t.WandSpec(source=self.source,x=128,y=1))
        with self.assertRaisesRegex(ValueError,'Start'):self.wand(combine='subtract')
    def test_selection_combine_preserves_soft_edges(self):
        old=Image.new('L',(32,32),100);new=Image.new('L',(32,32),160)
        self.assertEqual(t.combine_mask(old,new,'add').getpixel((0,0)),160)
        self.assertEqual(t.combine_mask(new,old,'subtract').getpixel((0,0)),60)
        self.assertEqual(t.combine_mask(old,new,'intersect').getpixel((0,0)),100)
        with self.assertRaisesRegex(ValueError,'dimensions'):t.combine_mask(old,Image.new('L',(33,32)),'add')
    def test_point_selection_uses_native_sam_without_text_conditioning(self):
        value=a.HelperSpec(source=self.source,kind='segment',positive_points=[{'x':20,'y':20}],negative_points=[{'x':90,'y':20}],threshold=.7,refine_iterations=3)
        job=a.vision(value);graph=helpers.build(job['spec'],'source.png',job['id'])
        self.assertNotIn('3',graph);inputs=graph['4']['inputs'];self.assertEqual(inputs['positive_coords'],'[{"x": 20, "y": 20}]');self.assertNotIn('conditioning',inputs);self.assertEqual(inputs['threshold'],.7)
        self.assertEqual(job['spec']['source_sha'],a.asset_info(self.source)['sha256'])
    def test_point_selection_rejects_invalid_image_coordinates_or_negative_only(self):
        with self.assertRaisesRegex(ValueError,'inside'):a.vision(a.HelperSpec(source=self.source,kind='segment',positive_points=[{'x':130,'y':20}]))
        with self.assertRaisesRegex(ValueError,'positive'):a.vision(a.HelperSpec(source=self.source,kind='segment',negative_points=[{'x':20,'y':20}]))
    def test_point_coordinates_follow_resized_renderer_input_without_mutating_job(self):
        id=a.put_image(Image.new('RGB',(2048,1024),'white'))['id']
        spec={'model':'segment','source':id,'prompt':'Object','positive_points':[{'x':1500,'y':600}],'negative_points':[{'x':100,'y':100}]}
        graph=a.prepare(spec,'points-scale-test')
        self.assertEqual(graph['4']['inputs']['positive_coords'],'[{"x": 750, "y": 300}]')
        self.assertEqual(spec['positive_points'][0]['x'],1500)
    def test_quality_review_queues_labeled_pair_and_preserves_input(self):
        before=a.put_image(Image.new('RGBA',(64,96),'blue'))['id'];original=a.load(self.source).tobytes()
        job=a.vision(a.HelperSpec(source=self.source,before=before,purpose='review',instruction='Make the red object green.'))
        self.assertEqual(job['state'],'queued');self.assertTrue(job['spec']['_quality_review'])
        self.assertEqual(job['spec']['_review_source'],self.source);self.assertEqual(job['spec']['_review_before'],before)
        pair=a.load(job['spec']['source']);self.assertEqual(pair.size,(1024,544))
        graph=helpers.build(job['spec'],'pair.png',job['id']);prompt=graph['3']['inputs']['prompt']
        self.assertIn('BEFORE and AFTER',prompt);self.assertIn('Make the red object green.',prompt);self.assertIn('Do not claim pixel-exact',prompt)
        self.assertEqual(a.load(self.source).tobytes(),original)
    def test_quality_review_rejects_segmentation_and_mismatched_purpose(self):
        with self.assertRaisesRegex(ValueError,'requires vision'):a.vision(a.HelperSpec(source=self.source,kind='segment',purpose='review'))
        with self.assertRaisesRegex(ValueError,'requires quality review'):a.vision(a.HelperSpec(source=self.source,before=self.source))
    def test_export_formats_resize_and_alpha(self):
        id=a.put_image(Image.new('RGBA',(64,32),(255,0,0,0)))['id'];client=TestClient(a.app)
        for format in ('png','webp','jpeg'):
            response=client.get(f'/api/tools/export/{id}',params={'format':format,'width':128,'height':64,'background':'#00ff00','lossless':'true','name':'../../test"'})
            self.assertEqual(response.status_code,200);out=Image.open(io.BytesIO(response.content));self.assertEqual(out.size,(128,64));self.assertIn('attachment;',response.headers['content-disposition'])
            if format=='jpeg':self.assertLess(np.max(np.abs(np.array(out)[0,0].astype(int)-[0,255,0])),4)
            else:self.assertEqual(out.convert('RGBA').getpixel((0,0))[3],0)
        self.assertEqual(client.get(f'/api/tools/export/{id}?width=0&height=64').status_code,422)
        self.assertEqual(client.get(f'/api/tools/export/{id}?width=64').status_code,400)
    def test_recipes_persist_and_do_not_store_asset_ids(self):
        row=t.save_recipe(t.RecipeSpec(name='A useful recipe',form={'prompt':'Replace with a lemon','model':'h3','task':'edit'}));self.assertEqual(t.recipes()[0]['id'],row['id'])
        with self.assertRaisesRegex(ValueError,'unsupported'):t.save_recipe(t.RecipeSpec(name='Bad',form={'source':self.source}))
        t.delete_recipe(row['id']);self.assertEqual(t.recipes(),[])
    def test_extracted_layer_is_cropped_positioned_and_keeps_original_alpha(self):
        im=Image.new('RGBA',(128,96),(250,20,10,128));source=a.put_image(im)['id'];m=Image.new('L',im.size);ImageDraw.Draw(m).rectangle((20,30,49,59),fill=255)
        out=t.extract_layer(t.ExtractSpec(source=source,mask=a.put_image(m)['id']));cut=a.load(out['asset']['id'])
        self.assertEqual((out['x'],out['y'],cut.size),(20,30,(30,30)));self.assertEqual(cut.getpixel((0,0)),(250,20,10,128));self.assertEqual(a.load(source).tobytes(),im.tobytes())
    def test_extraction_rejects_empty_selection(self):
        mid=a.put_image(Image.new('L',(128,96)))['id']
        with self.assertRaisesRegex(ValueError,'visible'):t.extract_layer(t.ExtractSpec(source=self.source,mask=mid))
    def test_blend_modes_match_known_opaque_pixels(self):
        from backend.imaging import blend_layer
        expected={'multiply':(32,32,61,255),'screen':(160,160,219,255),'overlay':(64,65,183,255),'darken':(64,64,76,255),'lighten':(128,128,204,255),'difference':(64,64,128,255)}
        for mode,pixel in expected.items():
            canvas=Image.new('RGBA',(32,32),(64,128,204,255));blend_layer(canvas,Image.new('RGBA',(4,4),(128,64,76,255)),(10,12),mode)
            self.assertEqual(canvas.getpixel((10,12)),pixel,mode);self.assertEqual(canvas.getpixel((0,0)),(64,128,204,255))
    def test_blending_translucent_pixels_preserves_straight_alpha(self):
        from backend.imaging import blend_layer
        canvas=Image.new('RGBA',(32,32),(255,0,0,128));blend_layer(canvas,Image.new('RGBA',(32,32),(0,0,255,128)),(0,0),'multiply')
        self.assertEqual(canvas.getpixel((0,0)),(85,0,85,192))
        transparent=Image.new('RGBA',(32,32));blend_layer(transparent,Image.new('RGBA',(32,32),(12,80,164,128)),(0,0),'multiply')
        self.assertEqual(transparent.getpixel((0,0)),(12,80,164,128))
    def test_blend_clips_negative_or_off_canvas_positions(self):
        from backend.imaging import blend_layer
        canvas=Image.new('RGBA',(32,32),'white');layer=Image.new('RGBA',(4,4),'red');blend_layer(canvas,layer,(-2,-2),'multiply')
        self.assertEqual(canvas.getpixel((1,1)),(255,0,0,255));self.assertEqual(canvas.getpixel((2,2)),(255,255,255,255));before=canvas.tobytes()
        blend_layer(canvas,layer,(40,40),'screen');self.assertEqual(canvas.tobytes(),before)
    def test_layer_flip_both_axes_and_schema_validation(self):
        from backend import imaging,schemas
        from pydantic import ValidationError
        layer=Image.new('RGBA',(32,32));layer.putpixel((1,2),(255,0,0,255));layer.putpixel((4,8),(0,0,255,255))
        out=imaging.compose((32,32),[{'asset':'x','flip_x':True,'flip_y':True}],lambda _:layer)
        self.assertEqual(out.getpixel((30,29)),(255,0,0,255));self.assertEqual(out.getpixel((27,23)),(0,0,255,255));self.assertEqual(layer.getpixel((1,2)),(255,0,0,255))
        with self.assertRaises(ValidationError):schemas.Layer(asset=self.source,blend='unknown')
    def test_blend_and_flip_round_trip_through_project_schema(self):
        from backend.schemas import ProjectPayload
        payload=ProjectPayload(layers=[{'asset':self.source,'blend':'screen','flip_x':True,'flip_y':False}])
        layer=payload.model_dump()['layers'][0];self.assertEqual(layer['blend'],'screen');self.assertTrue(layer['flip_x']);self.assertFalse(layer['flip_y'])
    def test_openraster_archive_structure_and_layer_metadata(self):
        import zipfile
        from xml.etree import ElementTree as ET
        spec=t.LayerExportSpec(width=128,height=96,name='Artwork <&>',layers=[{'asset':self.source,'name':'Base'},{'asset':self.source,'name':'Top <&>','opacity':.4,'visible':False,'blend':'multiply','x':-5,'flip_x':True}])
        response=t.openraster(spec)
        with zipfile.ZipFile(io.BytesIO(response.body)) as z:
            self.assertIsNone(z.testzip());first=z.infolist()[0];self.assertEqual(first.filename,'mimetype');self.assertEqual(first.compress_type,zipfile.ZIP_STORED);self.assertEqual(z.read('mimetype'),b'image/openraster')
            root=ET.fromstring(z.read('stack.xml'));self.assertEqual(root.attrib['name'],'Artwork <&>');top,base=root.find('stack').findall('layer')
            self.assertEqual(top.attrib['name'],'Top <&>');self.assertEqual(top.attrib['visibility'],'hidden');self.assertEqual(top.attrib['opacity'],'0.4');self.assertEqual(top.attrib['x'],'-5');self.assertEqual(top.attrib['composite-op'],'svg:multiply')
            self.assertEqual(Image.open(io.BytesIO(z.read(top.attrib['src']))).getpixel((107,20)),(255,0,0,255))
            self.assertEqual(Image.open(io.BytesIO(z.read('mergedimage.png'))).tobytes(),a.load(self.source).tobytes())
            self.assertLessEqual(max(Image.open(io.BytesIO(z.read('Thumbnails/thumbnail.png'))).size),256)
    def test_openraster_opacity_applied_once_and_transforms_baked(self):
        import zipfile
        from xml.etree import ElementTree as ET
        source=a.put_image(Image.new('RGBA',(40,20),(255,0,0,128)))['id']
        response=t.openraster(t.LayerExportSpec(width=100,height=100,layers=[{'asset':source,'x':30,'y':40,'rotation':90,'opacity':.5}]))
        with zipfile.ZipFile(io.BytesIO(response.body)) as z:
            layer=ET.fromstring(z.read('stack.xml')).find('stack/layer');im=Image.open(io.BytesIO(z.read(layer.attrib['src'])));merged=Image.open(io.BytesIO(z.read('mergedimage.png')))
            self.assertEqual(im.size,(20,40));self.assertEqual((layer.attrib['x'],layer.attrib['y']),('40','30'));self.assertEqual(im.getpixel((10,10))[3],128);self.assertEqual(merged.getpixel((50,50))[3],64)

if __name__=='__main__':unittest.main()

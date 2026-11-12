import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from PIL import Image
from test_core import a
from backend import config, workspace, video_lab

class WorkspaceTests(unittest.TestCase):
    def test_both_games_defer_gpu_and_process_errors_fail_closed(self):
        for name in ['RocketLeague.exe','FortniteClient-Win64-Shipping.exe']:
            with patch.object(a.psutil,'process_iter',return_value=[SimpleNamespace(info={'name':name})]):
                self.assertTrue(a.game_running())
        with patch.object(a.psutil,'process_iter',side_effect=OSError('denied')):
            self.assertTrue(a.game_running())
        with patch.object(a.psutil,'process_iter',return_value=[SimpleNamespace(info={'name':'chrome.exe'})]):
            self.assertFalse(a.game_running())

    def test_cpu_only_never_starts_renderer(self):
        with patch.object(config,'CPU_ONLY',True),patch.object(a.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'CPU-only'):a.start_runtime()
            spawn.assert_not_called()

    def test_connections_do_not_expose_bootstrap_token(self):
        def handler(req):
            self.assertEqual(req.url.path,'/api/atelier/status')
            return httpx.Response(200,json={'bridge':1,'version':'test'})
        real=httpx.Client
        with patch.object(workspace.httpx,'Client',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
            result=workspace.connections()
        self.assertTrue(result['h3']['ready']);self.assertNotIn('token',str(result))

    def test_handoff_uses_token_and_preserves_request_id(self):
        source=a.put_image(Image.new('RGB',(64,64),'blue'))['id']
        request='d706df3a-7878-48d4-8ae4-6ae2526a32ca'
        def handler(req):
            if req.method=='GET':return httpx.Response(200,json={'token':'test-token'})
            import json
            body=json.loads(req.content);self.assertEqual(body['request_id'],request)
            self.assertEqual(req.headers['X-H3-Token'],'test-token')
            self.assertTrue(body['data_url'].startswith('data:image/png;base64,'))
            return httpx.Response(200,json={'id':request,'title':'Test'})
        real=httpx.Client
        with patch.object(workspace.httpx,'Client',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
            result=workspace.handoff(workspace.Handoff(request_id=request,source=source,title='Test'))
        self.assertTrue(result['url'].endswith('?project='+request))

    def test_config_rejects_external_targets_and_embedded_credentials(self):
        for value in ['https://example.com','http://user:pass@localhost:8766','http://127.0.0.1:8766/other']:
            with patch.object(config,'value',return_value=value):
                with self.assertRaises(ValueError):config.local_url('h3_url','')

    def test_video_command_is_portable(self):
        import sys
        self.assertEqual(video_lab.command([sys.executable,'-c','print("portable")']).strip(),'portable')

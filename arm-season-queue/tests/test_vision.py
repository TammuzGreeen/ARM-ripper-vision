import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np

from app.camera import Camera
from app.config import Settings
from app.controller import Controller
from app.vision import identify, VisionError
from app.menu_capture import capture_menu
from app.subtitle_analysis import analyze_subtitles
from app.disc_identity import DiscFingerprint, lookup_disc


def observation():
    return {'media_present':True, 'series':{'value':'Example series','visible_text':'Example series','image_index':0},
            'season':{'value':3,'visible_text':'3. Staffel','image_index':0}, 'disc':None, 'edition':None,
            'episodes':[{'first':1,'last':6,'title':None,'visible_text':'Episoden 1–6','image_index':0}],
            'uncertainties':[]}


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(recognition_backend='llamacpp',vision_url='http://vision.invalid:8080/v1',
                                 vision_model='test-vision-model',vision_key='private-test-key')
        self.image = np.zeros((40,60,3),dtype=np.uint8)

    def response(self, value=None, **kwargs):
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop', 'message':{
            'content':json.dumps(observation() if value is None else value)}}]}, **kwargs)

    def test_llamacpp_sends_actual_images_and_validates_printed_fields(self):
        requests=[]
        def handler(request):
            requests.append(json.loads(request.content))
            self.assertEqual(str(request.url),'http://vision.invalid:8080/v1/chat/completions')
            self.assertEqual(request.headers['authorization'],'Bearer private-test-key')
            return self.response()
        result=identify(self.settings,[self.image],transport=httpx.MockTransport(handler))
        self.assertEqual(result['season']['value'],3)
        self.assertIsNone(result['disc'])
        self.assertIsNone(result['episodes'][0]['title'])
        body=requests[0]
        self.assertTrue(body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,/9j/'))
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        self.assertIn('schema',body['response_format'])
        self.assertNotIn('private-test-key',json.dumps(body))

    def test_ollama_wire_format(self):
        settings=Settings(recognition_backend='ollama',vision_url='http://vision.invalid:11434',vision_model='vision:test')
        def handler(request):
            body=json.loads(request.content)
            self.assertEqual(request.url.path,'/api/chat')
            self.assertEqual(len(body['messages'][1]['images']),2)
            self.assertNotIn('authorization',request.headers)
            self.assertFalse(body['think'])
            return httpx.Response(200,json={'done':True,'message':{'content':json.dumps(observation())}})
        identify(settings,[self.image]*2,transport=httpx.MockTransport(handler))

    def test_empty_scene_valid_but_empty_scene_with_identity_rejected(self):
        empty={key:None for key in ('series','season','disc','edition')}
        empty.update(media_present=False,episodes=[],uncertainties=[])
        self.assertFalse(identify(self.settings,[self.image],transport=httpx.MockTransport(lambda r:self.response(empty)))['media_present'])
        empty['series']=observation()['series']
        with self.assertRaises(VisionError):
            identify(self.settings,[self.image],transport=httpx.MockTransport(lambda r:self.response(empty)))

    def test_invalid_schema_ranges_image_refs_and_extra_commands_rejected(self):
        cases=[]
        wrong=observation();wrong['season']['value']='3';cases.append(wrong)
        wrong=observation();wrong['series']['image_index']=2;cases.append(wrong)
        wrong=observation();wrong['episodes'][0]['last']=0;cases.append(wrong)
        wrong=observation();wrong['start_ripping']=True;cases.append(wrong)
        wrong=observation();wrong['series']['visible_text']='';cases.append(wrong)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(VisionError):
                identify(self.settings,[self.image],transport=httpx.MockTransport(lambda r:self.response(value)))

    def test_server_errors_redirects_and_truncation_are_sanitized(self):
        responses=[httpx.Response(401,text='private-test-key'),httpx.Response(302,headers={'location':'http://elsewhere.invalid'}),
                   httpx.Response(200,text='private-test-key'),
                   httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'private-test-key'}}]}),
                   httpx.Response(200,content=b'x'*262145)]
        for response in responses:
            with self.subTest(response=response),self.assertRaises(VisionError) as caught:
                identify(self.settings,[self.image],transport=httpx.MockTransport(lambda r:response))
            self.assertNotIn('private-test-key',str(caught.exception))

    def test_timeout_has_clear_sanitized_message(self):
        def handler(request): raise httpx.ReadTimeout('private-test-key',request=request)
        with self.assertRaisesRegex(VisionError,'timed out'):
            identify(self.settings,[self.image],transport=httpx.MockTransport(handler))

    def test_no_more_than_three_images(self):
        for images in ([],[self.image]*4):
            with self.assertRaises(VisionError): identify(self.settings,images)

    def test_missing_projector_has_actionable_error(self):
        response=httpx.Response(500,json={'error':{'message':'image input is not supported - private server detail'}})
        with self.assertRaisesRegex(VisionError,'matching vision projector') as caught:
            identify(self.settings,[self.image],transport=httpx.MockTransport(lambda r:response))
        self.assertNotIn('private server detail',str(caught.exception))

    def test_configuration_requires_explicit_valid_server_and_model(self):
        for kwargs in ({'vision_url':''},{'vision_model':''},{'vision_url':'http://user:secret@server'},
                       {'vision_url':'file:///tmp/server'},{'vision_timeout':0}):
            values={'recognition_backend':'llamacpp','vision_url':'http://vision.invalid/v1','vision_model':'test'}
            values.update(kwargs)
            with self.assertRaises(ValueError): Settings(**values)

    def test_camera_handoff_keeps_evidence_and_cannot_pair_or_mutate_masterlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(state=Path(tmp),arm_url='',recognition_backend='llamacpp',
                              vision_url='http://vision.invalid/v1',vision_model='test')
            controller=Controller(settings)
            camera=Camera(settings,controller.begin_event,controller.recognition_done,controller.release_event)
            try:
                event=camera.begin('camera')
                camera.release(event)
                with patch('app.camera.identify',return_value=observation()): camera.process(event,[self.image])
                row=controller.db.rows('SELECT * FROM events WHERE id=?',(event,))[0]
                body=json.loads(row['body'])
                self.assertEqual(body['source'],'vision_test')
                self.assertEqual(row['released'],0)
                self.assertFalse(body['result']['accepted'])
                self.assertTrue(body['result']['test_only'])
                self.assertEqual(body['result']['observation'],observation())
                self.assertTrue((Path(tmp)/'evidence'/event/'0.jpg').exists())
                controller.db.pair_insertion(42,'insertion',180)
                self.assertIsNone(controller.db.rows('SELECT job FROM events WHERE id=?',(event,))[0]['job'])
                self.assertEqual(controller.masters(),[])
                event=camera.begin('camera')
                with patch('app.camera.identify',side_effect=VisionError('Vision request timed out')):
                    camera.process(event,[self.image])
                body=json.loads(controller.db.rows('SELECT body FROM events WHERE id=?',(event,))[0]['body'])
                self.assertEqual(len(body['result']['frames']),1)
                self.assertIn('timed out',camera.status['message'])
            finally:
                camera.pool.shutdown(wait=True)
                controller.validation.shutdown(wait=True)

    def test_planned_sources_fail_explicitly_without_opening_files(self):
        for function in (capture_menu,analyze_subtitles):
            with self.assertRaises(NotImplementedError): function(Path('/nonexistent/example'))
        with self.assertRaises(NotImplementedError): lookup_disc(DiscFingerprint('example-v1','example'))

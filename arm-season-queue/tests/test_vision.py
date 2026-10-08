import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import cv2
import numpy as np

from app.camera import Camera
from app.config import Settings
from app.controller import Controller
from app.vision import identify, transcribe_visible_text, VisionError
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
                                 vision_model='test-vision-model',vision_key='test-token')
        self.image = np.zeros((40,60,3),dtype=np.uint8)

    def response(self, value=None, **kwargs):
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop', 'message':{
            'content':json.dumps(observation() if value is None else value)}}]}, **kwargs)

    def test_llamacpp_sends_actual_images_and_validates_printed_fields(self):
        requests=[]
        def handler(request):
            requests.append(json.loads(request.content))
            self.assertEqual(str(request.url),'http://vision.invalid:8080/v1/chat/completions')
            self.assertEqual(request.headers['authorization'],'Bearer test-token')
            return self.response()
        result=identify(self.settings,[self.image],transport=httpx.MockTransport(handler))
        self.assertEqual(result['season']['value'],3)
        self.assertIsNone(result['disc'])
        self.assertIsNone(result['episodes'][0]['title'])
        body=requests[0]
        self.assertTrue(body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,/9j/'))
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        self.assertIn('schema',body['response_format'])
        self.assertNotIn('test-token',json.dumps(body))

    def test_ollama_wire_format(self):
        settings=Settings(recognition_backend='ollama',vision_url='http://vision.invalid:11434',vision_model='vision:test')
        def handler(request):
            body=json.loads(request.content)
            self.assertEqual(request.url.path,'/api/chat')
            self.assertEqual(len(body['messages'][1]['images']),2)
            self.assertNotIn('authorization',request.headers)
            self.assertFalse(body['think'])
            self.assertIn('high-contrast label block',body['messages'][0]['content'])
            self.assertIn('including Spanish',body['messages'][0]['content'])
            return httpx.Response(200,json={'done':True,'message':{'content':json.dumps(observation())}})
        identify(settings,[self.image]*2,transport=httpx.MockTransport(handler))

    def test_short_ollama_transcription_reserves_context_for_high_resolution_images(self):
        settings=Settings(recognition_backend='ollama',vision_url='http://vision.invalid:11434',vision_model='qwen3-vl:4b')
        transmitted=[]
        raw_response=[]
        def handler(request):
            body=json.loads(request.content)
            self.assertEqual(request.url.path,'/api/chat')
            self.assertFalse(body['think'])
            self.assertEqual(body['options']['num_ctx'],4096)
            self.assertEqual(body['options']['num_predict'],settings.vision_short_num_predict)
            self.assertIn('Preserve the original language and line breaks',body['messages'][0]['content'])
            self.assertIn('Do not infer missing text',body['messages'][0]['content'])
            self.assertEqual(len(body['messages'][0]['images']),1)
            encoded=base64.b64decode(body['messages'][0]['images'][0])
            transmitted.append(encoded)
            reduced=cv2.imdecode(np.frombuffer(encoded,dtype=np.uint8),cv2.IMREAD_COLOR)
            self.assertLessEqual(max(reduced.shape[:2]),1024)
            response=httpx.Response(200,json={'done':True,'done_reason':'stop',
                                              'message':{'content':'Disc 2 · Episoden 1–6'}})
            raw_response.append(response.content)
            return response
        diagnostics={}
        large=np.zeros((2000,3000,3),dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            save_path=Path(tmp)/'qwen-input.jpg'
            request_path=Path(tmp)/'qwen-request.json'
            response_path=Path(tmp)/'qwen-response.json'
            text=transcribe_visible_text(settings,[large],transport=httpx.MockTransport(handler),
                                          diagnostics=diagnostics,save_input_path=save_path,
                                          save_request_path=request_path,save_response_path=response_path)
            self.assertEqual(save_path.read_bytes(),transmitted[0])
            self.assertEqual(save_path.stat().st_mode & 0o777,0o600)
            self.assertEqual(json.loads(request_path.read_bytes())['think'],False)
            self.assertEqual(request_path.stat().st_mode & 0o777,0o600)
            self.assertEqual(response_path.read_bytes(),raw_response[0])
            self.assertEqual(response_path.stat().st_mode & 0o777,0o600)
        self.assertEqual(text,'Disc 2 · Episoden 1–6')
        self.assertEqual(diagnostics['kind'],'success')

    def test_short_transcription_accepts_bounded_higher_output_budget(self):
        settings=Settings(recognition_backend='ollama',vision_url='http://vision.invalid:11434',
                          vision_model='qwen3-vl:4b',vision_short_num_predict=1536,
                          vision_short_timeout=480)
        def handler(request):
            body=json.loads(request.content)
            self.assertEqual(body['options']['num_predict'],1536)
            self.assertEqual(body['options']['num_ctx'],4096)
            self.assertEqual(request.extensions['timeout']['read'],480)
            return httpx.Response(200,json={'done':True,'done_reason':'stop','message':{'content':'text'}})
        self.assertEqual(transcribe_visible_text(settings,[self.image],transport=httpx.MockTransport(handler)),'text')

    def test_short_transcription_keeps_output_limit_failure_distinct(self):
        settings=Settings(recognition_backend='ollama',vision_url='http://vision.invalid:11434',vision_model='qwen3-vl:4b')
        response=httpx.Response(200,json={'done':True,'done_reason':'length',
                                         'prompt_eval_count':1000,'eval_count':768,
                                         'message':{'content':'partial text'}})
        with self.assertRaises(VisionError) as caught:
            transcribe_visible_text(settings,[self.image],transport=httpx.MockTransport(lambda r:response))
        self.assertEqual(caught.exception.kind,'incomplete_inference')
        self.assertEqual(caught.exception.diagnostic()['eval_count'],768)

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
        responses=[httpx.Response(401,text='test-token'),httpx.Response(302,headers={'location':'http://elsewhere.invalid'}),
                   httpx.Response(200,text='test-token'),
                   httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'test-token'}}]}),
                   httpx.Response(200,content=b'x'*262145)]
        for response in responses:
            with self.subTest(response=response),self.assertRaises(VisionError) as caught:
                identify(self.settings,[self.image],transport=httpx.MockTransport(lambda r:response))
            self.assertNotIn('test-token',str(caught.exception))

    def test_timeout_has_clear_sanitized_message(self):
        def handler(request): raise httpx.ReadTimeout('test-token',request=request)
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
        with self.assertRaisesRegex(ValueError,'CAMERA_ROTATION'):
            Settings(camera_rotation=90)

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

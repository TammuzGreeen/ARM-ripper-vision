"""Real local FFmpeg/ffprobe validation of generated media; never a simulated ARM API."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.handover import inspect_file, publish_json, publish_manifest


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg tools not installed')
class MediaTests(unittest.TestCase):
    def test_real_generated_media_to_manifest_copy_and_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media';media.mkdir();source=media/'test.mkv'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=25',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','2','-c:v','ffv1','-c:a','pcm_s16le',
                '-metadata:s:a:0','language=eng',str(source)],check=True,timeout=60)
            inventory={'video_codec':'ffv1','width':320,'height':240,'fps':25,'chapters':0,
                'audio':['eng'],'subtitles':[],'audio_channels':[1],'complete':True,'evidence':'Generated test source'}
            checked=inspect_file(source,inventory,2)
            self.assertEqual(checked['validation']['decode'],'passed')
            manifest={'schema_version':1,'status':'ready','errors':[],'arm_status':'success','arm_job_id':123,
                'batch_id':'local-media-test','source_inventory':inventory,
                'outputs':[dict(checked,media_relative_path='test.mkv',destination='tv/Test/Season 01/Test S01E01.mkv',scan_duration=2)]}
            handover=root/'handover';path=handover/'ready'/'123.json';publish_json(path,manifest)
            ack=publish_manifest(path,media,root/'library',handover)
            self.assertEqual(ack['status'],'published')
            self.assertEqual(publish_manifest(path,media,root/'library',handover),ack)
            self.assertTrue(source.exists())
            self.assertEqual(json.loads((handover/'acks'/'123.json').read_text())['manifest_sha256'],ack['manifest_sha256'])
            source.write_bytes(b'truncated')
            with self.assertRaises((ValueError,subprocess.CalledProcessError)):
                publish_manifest(path,media,root/'library',handover)


if __name__=='__main__':unittest.main()

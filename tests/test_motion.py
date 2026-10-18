import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from aiohttp import CookieJar, FormData
from aiohttp.test_utils import TestClient, TestServer

from shaniu.config import ROOT, Settings
from shaniu.mcp.common import ToolError
from shaniu.motion import MotionTasks, parse_srt, plan
from shaniu.server import SERVICE, create_app
from shaniu.storage import Store


class MotionPlanTests(unittest.TestCase):
    def test_cues_preserve_sentence_times_and_literal_content(self):
        result = parse_srt('1\r\n00:00:01,200 --> 00:00:03,400\r\n<b>雪豹</b>\r\n高山动物\r\n\r\n2\r\n00:00:09.000 --> 00:00:12.000\r\n保护栖息地。')
        self.assertEqual(result[0]['t0'],1.2)
        self.assertEqual(result[0]['zh'],'<b>雪豹</b>\n高山动物')
        cards=plan('雪豹',result)
        self.assertEqual(cards[1]['text'],'保护栖息地。')
        self.assertEqual(cards[1]['t0'],9.)
        self.assertEqual(plan('主题',[]),[dict(t0=0.,t1=4.,text='主题',kind='title')])
        for text in ['1\n00:00:03,000 --> 00:00:02,000\n错误','1\nnot a timestamp --> invalid\n错误']:
            with self.assertRaises(ToolError):
                parse_srt(text)

    def test_vendored_sources_match_provenance_receipt(self):
        receipt=json.loads((ROOT/'vendor/adu-motion/SOURCE.json').read_text(encoding='utf-8'))
        for record in receipt['files']:
            self.assertEqual(hashlib.sha256((ROOT/'vendor/adu-motion'/record['file']).read_bytes()).hexdigest(),record['sha256'])

    def test_restart_preserves_baseline_and_fails_interrupted_enhancement(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'test.db')
            tid=str(uuid4()); store.save_external_task('owner',tid,'video','测试')
            motion=MotionTasks(Settings(),store,None)
            with store.connect() as db:
                db.execute('INSERT INTO motion_jobs VALUES (?,?,?,?)',('job','owner',tid,json.dumps(dict(status='processing',progress=45))))
            restarted=MotionTasks(Settings(),store,None)
            self.assertEqual(restarted.latest('owner',tid)['status'],'failed')
            self.assertIsNone(restarted.latest('other',tid))
            self.assertIsNotNone(store.external_task('owner',tid))


class MotionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Settings(database=Path(self.temp.name)/'test.db',moneyprinter_enabled=True))
        self.client=TestClient(TestServer(self.app),cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server(); await self.client.get('/api/health')
        self.service=self.app[SERVICE]
        self.owner=next(c.value for c in self.client.session.cookie_jar if c.key=='shaniu_owner')
        self.cid=self.service.store.create(self.owner,'cool')['id']
        mid=self.service.store.add_message(self.cid,str(uuid4()),'assistant','真实任务')
        self.tid=str(uuid4())
        self.service.store.save_external_task(self.owner,self.tid,'video','雪豹')
        self.service.store.link_external_task(self.owner,self.tid,mid)
        self.ready=True
        self.subtitle=True
        async def request(method,path,payload=None):
            return dict(status=200,data=dict(state=1 if self.ready else 4,progress=100 if self.ready else 40,
                videos=[f'/tasks/{self.tid}/final.mp4'],combined_videos=[f'/tasks/{self.tid}/combined.mp4'],
                audio_file=f'/tasks/{self.tid}/audio.mp3',subtitle_path=f'/tasks/{self.tid}/subtitle.srt' if self.subtitle else []))
        self.service.moneyprinter.request=request
        self.wait=asyncio.Event()
        async def run(*args): await self.wait.wait()
        self.service.moneyprinter.motion.run=run
        self.headers={'X-Shaniu-Client':'web'}
        self.url='/api/agent/tools/moneyprinter_enhance'
        self.args=dict(task_id=self.tid,enabled_tool_groups=['moneyprinter'])

    async def asyncTearDown(self):
        self.wait.set()
        await self.client.close()
        self.temp.cleanup()

    async def test_selection_owner_duplicate_and_options(self):
        self.assertEqual((await self.client.post(self.url,json=dict(task_id=self.tid),headers=self.headers)).status,403)
        for extra in [dict(style='unknown'),dict(sound_effects='yes'),dict(music_id='../escape')]:
            self.assertEqual((await self.client.post(self.url,json={**self.args,**extra},headers=self.headers)).status,400)
        self.ready=False
        self.assertEqual((await self.client.post(self.url,json=self.args,headers=self.headers)).status,400)
        self.ready=True; self.subtitle=False
        self.assertEqual((await self.client.post(self.url,json=self.args,headers=self.headers)).status,400)
        self.subtitle=True
        response=await self.client.post(self.url,json=self.args,headers=self.headers)
        self.assertEqual(response.status,200)
        job=(await response.json())['enhancement']['job_id']
        again=await (await self.client.post(self.url,json=self.args,headers=self.headers)).json()
        self.assertEqual(again['enhancement']['job_id'],job)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.post(self.url,json=self.args,headers=self.headers)).status,400)

    async def test_enhancement_updates_despite_terminal_baseline_cache_and_owned_files(self):
        listing=f'/api/conversations/{self.cid}/media-tasks'
        await self.client.get(listing)  # Terminal baseline is cached before submit.
        job=(await (await self.client.post(self.url,json=self.args,headers=self.headers)).json())['enhancement']['job_id']
        motion=self.service.moneyprinter.motion
        motion.update(job,status='processing',progress=64,stage='渲染动效')
        task=(await (await self.client.get(listing)).json())['tasks'][0]
        self.assertEqual(task['status'],'completed')
        self.assertEqual(task['enhancement']['progress'],64)
        directory=motion.root/job; directory.mkdir()
        for name in ['enhanced.mp4','project.zip','report.json']: (directory/name).write_bytes(b'0123456789'*10)
        motion.update(job,status='completed',progress=100)
        task=(await (await self.client.get(listing)).json())['tasks'][0]
        video=task['enhancement']['video_url']
        response=await self.client.get(video,headers={'Range':'bytes=0-9'})
        self.assertEqual(response.status,206)
        self.assertEqual(await response.read(),b'0123456789')
        response=await self.client.get(task['enhancement']['project_url']+'?download=1')
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual((await self.client.get(video.replace('enhanced.mp4','worker.log'))).status,404)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get(video)).status,404)

    async def test_music_upload_is_owned_and_rejects_empty_or_wrong_type(self):
        form=FormData(); form.add_field('file',b'RIFFaudio',filename='music.wav',content_type='audio/wav')
        response=await self.client.post('/api/motion/music',data=form,headers=self.headers)
        self.assertEqual(response.status,200)
        music_id=(await response.json())['music_id']
        self.assertEqual(self.service.moneyprinter.motion.music(self.owner,music_id).read_bytes(),b'RIFFaudio')
        with self.assertRaises(ToolError): self.service.moneyprinter.motion.music('other',music_id)
        for filename,data in [('bad.exe',b'bad'),('empty.wav',b'')]:
            form=FormData(); form.add_field('file',data,filename=filename,content_type='audio/wav')
            self.assertEqual((await self.client.post('/api/motion/music',data=form,headers=self.headers)).status,400)

import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import sys
import unittest
from uuid import uuid4

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from shaniu.config import ROOT, Settings
from shaniu.media_post import PostTasks, options
from shaniu.mcp.common import ToolError
from shaniu.server import SERVICE, create_app
from shaniu.storage import Store


class PostValidationTests(unittest.TestCase):
    def test_invalid_options_and_unchanged_vendor_receipts(self):
        for extra in [dict(operation='shell'),dict(start=True),dict(start=float('nan')),dict(start=3,end=2),dict(command='evil'),dict(subtitles='yes'),dict(crf=True),dict(subtitles=True),dict(mode='smart')]:
            with self.subTest(extra=extra), self.assertRaises(ToolError):
                options(dict(operation='cut',start=0,end=2) | extra)
        receipt=json.loads((ROOT/'vendor/easel-media/SOURCE.json').read_text())
        for entry in receipt['files']:
            self.assertEqual(hashlib.sha256((ROOT/'vendor/easel-media'/entry['file']).read_bytes()).hexdigest(),entry['sha256'])

    def test_restart_fails_pending_job_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.db'); tid=str(uuid4()); store.save_external_task('alice',tid,'video','测试')
            PostTasks(Settings(),store,None)
            with store.connect() as db:
                db.execute('INSERT INTO media_post_jobs VALUES (?,?,?,?)',('job','alice',tid,json.dumps(dict(status='processing',progress=50))))
            jobs=PostTasks(Settings(),store,None)
            self.assertEqual(jobs.list('alice',tid)[0]['status'],'failed')
            self.assertEqual(jobs.list('bob',tid),[])
            self.assertIsNotNone(store.external_task('alice',tid))

    def test_ass_roundtrip_and_cut_subtitles_are_rebased(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from media_post_worker import vendor, window
        subtitles=vendor('subtitle_ops')
        cues=[dict(idx=1,start=.5,end=2.5,text='大熊猫，生活在中国。'),dict(idx=2,start=3,end=5,text='保护栖息地。')]
        output=subtitles._render_ass_mono(cues,'Microsoft YaHei')
        parsed=subtitles._parse_ass(output)
        self.assertEqual([(c['start'],c['end'],c['text']) for c in parsed],[(c['start'],c['end'],c['text']) for c in cues])
        clipped=window(cues,1,4)
        self.assertEqual([(c['start'],c['end']) for c in clipped],[(0,1.5),(2,3)])


class PostAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Settings(database=Path(self.temp.name)/'test.db',moneyprinter_enabled=True))
        self.client=TestClient(TestServer(self.app),cookie_jar=CookieJar(unsafe=True)); await self.client.start_server(); await self.client.get('/api/health')
        self.service=self.app[SERVICE]
        self.owner=next(c.value for c in self.client.session.cookie_jar if c.key=='shaniu_owner')
        self.cid=self.service.store.create(self.owner,'cool')['id']
        mid=self.service.store.add_message(self.cid,str(uuid4()),'assistant','真实任务')
        self.tid=str(uuid4()); self.service.store.save_external_task(self.owner,self.tid,'video','测试')
        self.service.store.link_external_task(self.owner,self.tid,mid)
        self.ready=True; self.subtitle=True
        async def request(*args):
            return dict(status=200,data=dict(state=1 if self.ready else 4,progress=100,videos=[f'/tasks/{self.tid}/final.mp4'],
                combined_videos=[f'/tasks/{self.tid}/base.mp4'],audio_file=f'/tasks/{self.tid}/audio.mp3',subtitle_path=f'/tasks/{self.tid}/subtitle.srt' if self.subtitle else []))
        self.service.moneyprinter.request=request
        self.wait=asyncio.Event()
        async def run(*args): await self.wait.wait()
        self.service.moneyprinter.post.run=run
        self.url='/api/agent/tools/media_postprocess'; self.headers={'X-Shaniu-Client':'web'}
        self.args=dict(task_id=self.tid,operation='cut',start=0,end=3,enabled_tool_groups=['media_post'])

    async def asyncTearDown(self):
        self.wait.set(); await self.client.close(); self.temp.cleanup()

    async def test_selection_owner_duplicate_and_missing_inputs(self):
        self.assertEqual((await self.client.post(self.url,json={**self.args,'enabled_tool_groups':[]},headers=self.headers)).status,403)
        self.ready=False
        self.assertEqual((await self.client.post(self.url,json=self.args,headers=self.headers)).status,400)
        self.ready=True; self.subtitle=False
        self.assertEqual((await self.client.post(self.url,json={**self.args,'operation':'subtitle'},headers=self.headers)).status,400)
        self.subtitle=True
        response=await self.client.post(self.url,json=self.args,headers=self.headers)
        self.assertEqual(response.status,200)
        first=(await response.json())['postprocess']['job_id']
        repeated=await (await self.client.post(self.url,json=self.args,headers=self.headers)).json()
        self.assertEqual(first,repeated['postprocess']['job_id'])
        self.assertEqual((await self.client.post(self.url,json={**self.args,'end':2},headers=self.headers)).status,400)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.post(self.url,json=self.args,headers=self.headers)).status,400)

    async def test_terminal_base_cache_overlays_progress_and_serves_only_owned_results(self):
        listing=f'/api/conversations/{self.cid}/media-tasks'
        await self.client.get(listing)
        job=(await (await self.client.post(self.url,json=self.args,headers=self.headers)).json())['postprocess']['job_id']
        post=self.service.moneyprinter.post
        post.update(job,status='processing',progress=55)
        task=(await (await self.client.get(listing)).json())['tasks'][0]
        self.assertEqual(task['postprocess'][0]['progress'],55)
        directory=post.root/job; directory.mkdir()
        (directory/'result.mp4').write_bytes(b'0123456789'*10)
        post.update(job,status='completed',progress=100,files=['result.mp4'])
        task=(await (await self.client.get(listing)).json())['tasks'][0]
        url=task['postprocess'][0]['artifacts'][0]['url']
        response=await self.client.get(url,headers={'Range':'bytes=0-9'})
        self.assertEqual(response.status,206); self.assertEqual(await response.read(),b'0123456789')
        response=await self.client.get(url+'?download=1'); self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual((await self.client.get(url.replace('result.mp4','worker.log'))).status,404)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get(url)).status,404)

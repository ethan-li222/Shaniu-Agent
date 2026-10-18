"""Render a portable Shaniu motion project using local Chrome and FFmpeg.

Requires aiohttp, av, numpy, imageio-ffmpeg. No paid model or external assets.
"""
import asyncio
import base64
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

import aiohttp
import av
import imageio_ffmpeg
import numpy as np


def emit(progress, stage):
    print(json.dumps(dict(progress=progress, stage=stage), ensure_ascii=False), flush=True)


def probe(path):
    with av.open(str(path)) as container:
        streams = container.streams
        video = next(iter(streams.video), None)
        audio = next(iter(streams.audio), None)
        stream = video or audio
        duration = float(stream.duration*stream.time_base) if stream and stream.duration else float(container.duration or 0)/av.time_base
        return dict(duration=duration, width=video.width if video else 0, height=video.height if video else 0,
                    audio=audio is not None, frames=video.frames if video else 0)


def command(args, timeout=240):
    result = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[-2000:])


async def render(project):
    config = json.loads((project / 'project.json').read_text(encoding='utf-8'))
    source, voice = probe(project/'base.mp4'), probe(project/'voice.audio')
    duration = source['duration']
    if not 0 < duration <= 600 or not source['width'] or not source['height'] or not voice['audio']:
        raise ValueError('Input must be a video with independent narration, at most 10 minutes')
    if abs(voice['duration']-duration) > .25:
        raise ValueError('Narration and video duration differ; align inputs first')
    if config.get('music') and probe(project/'music.audio')['duration']+.02 < duration:
        raise ValueError('Music must cover the full video; no implicit looping')
    fps = config.get('fps', 24)
    if fps not in {12, 24, 25, 30}:
        raise ValueError('Unsupported frame rate')
    frame_count = math.ceil(duration*fps)
    output_duration = frame_count/fps
    ratio = source['width']/source['height']
    if ratio >= 1:
        width = 1280 if ratio > 1.1 else 720
        height = round(width/ratio/2)*2
    else:
        height, width = 1280, round(1280*ratio/2)*2
    if min(width, height) < 180:
        raise ValueError('Unsupported source aspect ratio')
    data = dict(width=width, height=height, end=duration, style=config['style'], subtitles=config['subtitles'],
                subtitleStyle=dict(zhSize=30 if width<1000 else 32, enSize=22), subtitlePosition=dict(bottomRatio=.12,leftRatio=.07,rightRatio=.07))
    payload = 'const CONFIG='+json.dumps(data, ensure_ascii=False)+';\nconst SUBS='+json.dumps(config['cues'], ensure_ascii=False)+';\nconst CARDS='+json.dumps(config['cards'], ensure_ascii=False)+';'
    (project/'data.js').write_text(payload.replace('<', '\\u003c'), encoding='utf-8')
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    chrome = os.getenv('CHROME_PATH') or next((str(p) for p in [Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'), Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'), Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')] if p.is_file()), None) or shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:
        raise ValueError('Local Chrome/Edge not found; configure CHROME_PATH')
    import audiolib
    audiolib.N = round(output_duration*audiolib.SR)
    events = [dict(t=c['t0'], type='card', eventId='card-'+str(i), g=.16) for i,c in enumerate(config['cards']) if c['t0']+.5 < duration] if config['sound_effects'] else []
    audiolib.write_wav(str(project/'sfx.wav'), audiolib.render_sfx(events))
    emit(15, '合成音效与准备画面')
    browser = encoder = None
    try:
        with tempfile.TemporaryDirectory(prefix='shaniu-motion-chrome-') as profile:
            browser = await asyncio.create_subprocess_exec(chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--allow-file-access-from-files', '--force-color-profile=srgb', '--remote-debugging-port=0', '--user-data-dir='+profile, 'about:blank', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port_file = Path(profile)/'DevToolsActivePort'
            for _ in range(150):
                if port_file.exists():
                    break
                if browser.returncode is not None:
                    raise RuntimeError('Browser stopped')
                await asyncio.sleep(.1)
            port = int(port_file.read_text().splitlines()[0])
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as client:
                async with client.get(f'http://127.0.0.1:{port}/json') as response:
                    tabs = await response.json()
                tab = next(t for t in tabs if t['type']=='page')
                async with client.ws_connect(tab['webSocketDebuggerUrl']) as ws:
                    serial = 0
                    async def cdp(method, params=None):
                        nonlocal serial
                        serial += 1
                        await ws.send_json(dict(id=serial, method=method, params=params or {}))
                        while True:
                            packet = await asyncio.wait_for(ws.receive_json(), 45)
                            if packet.get('method') == 'Runtime.exceptionThrown':
                                raise RuntimeError('Browser script failed')
                            if packet.get('id') == serial:
                                if 'error' in packet:
                                    raise RuntimeError(packet['error']['message'])
                                return packet.get('result', {})
                    async def evaluate(expression):
                        value = await cdp('Runtime.evaluate', dict(expression=expression, awaitPromise=True, returnByValue=True))
                        if 'exceptionDetails' in value:
                            raise RuntimeError(value['exceptionDetails'].get('text','Scene rendering failed'))
                        return value.get('result', {}).get('value')
                    await cdp('Runtime.enable')
                    await cdp('Emulation.setDeviceMetricsOverride', dict(width=width,height=height,deviceScaleFactor=1,mobile=False))
                    await cdp('Page.navigate', dict(url=(project/'index.html').as_uri()))
                    for _ in range(100):
                        if await evaluate('typeof window.renderAt === "function"'):
                            break
                        await asyncio.sleep(.1)
                    await evaluate('window.READY.then(()=>true)')
                    with (project/'encoding.log').open('wb') as error_log:
                        encoder = await asyncio.create_subprocess_exec(ffmpeg, '-y','-v','error','-f','image2pipe','-framerate',str(fps),'-c:v','png','-i','pipe:0','-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',str(project/'silent.mp4'),stdin=asyncio.subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=error_log)
                        for index in range(frame_count):
                            await evaluate(f'window.renderAt({min(index/fps,duration-.001)})')
                            shot = await cdp('Page.captureScreenshot', dict(format='png',captureBeyondViewport=False))
                            encoder.stdin.write(base64.b64decode(shot['data']))
                            await encoder.stdin.drain()
                            if index == 0:
                                (project/'preview.png').write_bytes(base64.b64decode(shot['data']))
                            if index%max(1,fps*2) == 0:
                                emit(20+round(60*index/frame_count), '逐帧渲染动效')
                        encoder.stdin.close()
                        if await encoder.wait():
                            raise RuntimeError('Video encoding failed')
                    await cdp('Browser.close')
            await browser.wait()
            browser = None
    finally:
        for process in (encoder,browser):
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
    emit(85, '混合旁白、配乐与音效')
    inputs = ['-i',project/'silent.mp4','-i',project/'voice.audio','-i',project/'sfx.wav']
    normalize = '[1:a]apad,highpass=f=70,acompressor=threshold=-20dB:ratio=2.5:attack=5:release=120,loudnorm=I=-16:TP=-1.5:LRA=9'
    if config.get('music'):
        inputs += ['-i',project/'music.audio']
        graph = normalize+',asplit=2[v][sc];[3:a]volume=0.18[b];[b][sc]sidechaincompress=threshold=0.05:ratio=3:attack=30:release=450[bg];[v][2:a][bg]amix=inputs=3:normalize=0:duration=longest'
    else:
        graph = normalize+'[v];[v][2:a]amix=inputs=2:normalize=0:duration=longest'
    graph += f',atrim=0:{output_duration},alimiter=limit=0.92:level=false,loudnorm=I=-15.5:TP=-1.2:LRA=9,aresample=48000[out]'
    command([ffmpeg,'-y','-v','error',*inputs,'-filter_complex',graph,'-map','0:v:0','-map','[out]','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(output_duration),'-movflags','+faststart',project/'enhanced.mp4'])
    emit(94, '检查完整解码、时长与帧数')
    command([ffmpeg,'-v','error','-xerror','-i',project/'enhanced.mp4','-f','null','-'])
    final = probe(project/'enhanced.mp4')
    if not final['audio'] or abs(final['duration']-output_duration)>1/fps+.02 or final['frames'] != frame_count:
        raise ValueError('Output timing or frame count validation failed')
    measurement = subprocess.run([ffmpeg,'-hide_banner','-i',str(project/'enhanced.mp4'),'-af','loudnorm=I=-15.5:TP=-1.2:LRA=9:print_format=json','-f','null','-'],capture_output=True,timeout=180)
    if measurement.returncode:
        raise ValueError('Final audio measurement failed')
    diagnostic = measurement.stderr.decode('utf-8', errors='replace')
    import re
    blocks = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', diagnostic, re.S)
    loudness = json.loads(blocks[-1]) if blocks else {}
    report = dict(source='Adu 3.14.0-rc.1 primitives/captions/SFX; Shaniu responsive composition and CDP renderer', input=source, output=final, fps=fps, frame_count=frame_count, voiceRetimed=False, subtitleClock='original sentence SRT', cards=len(config['cards']), soundEffects=len(events), backgroundMusic=bool(config.get('music')), completeDecode=True, loudness=loudness, humanReview=False)
    (project/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),encoding='utf-8')
    (project/'README.txt').write_text('Shaniu motion project. Edit project.json and run: python render.py <project-directory>\nRequires aiohttp, av, numpy, imageio-ffmpeg and local Chrome. Original source license and notices are included.\nThe existing enhanced.mp4 is preserved in your downloaded video; this command regenerates project output.\n',encoding='utf-8')
    with zipfile.ZipFile(project/'project.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in project.iterdir():
            if path.is_file() and path.name not in {'project.zip','enhanced.mp4','silent.mp4','worker.log','encoding.log'}:
                archive.write(path,path.name)
    emit(100, '增强成品与可编辑工程已完成')


if __name__ == '__main__':
    asyncio.run(render(Path(sys.argv[1]).resolve()))

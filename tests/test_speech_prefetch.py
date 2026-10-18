import asyncio
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from speech_prefetch import SpeechPrefetch


class Result:
    def __init__(self, value):
        self.value, self.closed = value, False

    def close(self):
        self.closed = True


class PrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_speech_is_prepared_before_current_is_consumed(self):
        inputs = asyncio.Queue()
        results = []
        def prepare(item):
            result = Result(item)
            results.append(result)
            return result
        pipeline = SpeechPrefetch(inputs, prepare, lambda item: True)
        try:
            inputs.put_nowait('first')
            inputs.put_nowait('second')
            for _ in range(100):
                if pipeline.ready.qsize() == 2:
                    break
                await asyncio.sleep(.005)
            self.assertEqual(pipeline.ready.qsize(), 2)
            self.assertEqual([r.value for r in results], ['first', 'second'])
        finally:
            await pipeline.close()
        self.assertTrue(all(r.closed for r in results))

    async def test_interrupt_discards_and_cleans_inflight_speech(self):
        inputs, release, started = asyncio.Queue(), threading.Event(), threading.Event()
        active = {'value': True}
        result = Result('cancelled')
        def prepare(item):
            started.set()
            release.wait(2)
            return result
        pipeline = SpeechPrefetch(inputs, prepare, lambda item: active['value'])
        inputs.put_nowait('first')
        await asyncio.to_thread(started.wait, 1)
        active['value'] = False
        release.set()
        await pipeline.close()
        self.assertTrue(result.closed)
        self.assertTrue(pipeline.ready.empty())

    async def test_preparation_error_reaches_consumer(self):
        inputs = asyncio.Queue()
        def prepare(item):
            raise RuntimeError('test speech failure')
        pipeline = SpeechPrefetch(inputs, prepare, lambda item: True)
        try:
            inputs.put_nowait('first')
            item, result, error = await asyncio.wait_for(pipeline.ready.get(), 1)
            self.assertEqual(item, 'first')
            self.assertIsNone(result)
            self.assertIsInstance(error, RuntimeError)
        finally:
            await pipeline.close()

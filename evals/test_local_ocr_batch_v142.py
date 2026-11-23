"""Proof of executable-script OCR path: no Agent vision tool calls required."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'evals'))
from core import prepare_run, claim_ocr, read_json, status_run, ingest_batch
from ocr_local_batch import run_local_batch, text_from_rapidocr
from helpers import make_profile, create_test_image


class _Result:
    def __init__(self, lines): self.txts = lines


class _Engine:
    def __init__(self, text='招聘岗位\n熟练掌握Python\n要求3年以上经验'):
        self.calls=[]; self.text=text
    def __call__(self, path):
        self.calls.append(path)
        return _Result(self.text.splitlines())


class LocalBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.images=[]
        for i in range(2):
            p=self.base / f'jd{i}.png'
            create_test_image(p, size=(350+i*20, 650+i*20))
            self.images.append(p)
        self.profile = self.base / 'profile.json'
        self.profile.write_text(json.dumps(make_profile(), ensure_ascii=False),encoding='utf-8')
        self.run = self.base / 'run'
        self.manifest=prepare_run(list(map(str,self.images)), str(self.profile), str(self.run))

    def test_local_batch_saves_every_image_once_and_no_retries(self):
        engine=_Engine()
        result=run_local_batch(self.run,engine=engine)
        self.assertEqual(len(result['ok']),2)
        self.assertEqual(len(engine.calls),2)
        self.assertEqual(status_run(self.run)['counts']['needs_jd'],2)
        self.assertEqual(run_local_batch(self.run,engine=engine)['selected'],0)
        self.assertEqual(len(engine.calls),2)
        for x in self.manifest['items']:
            self.assertTrue(Path(x['ocr_path']).exists())
            self.assertIn('熟练掌握Python',Path(x['ocr_path']).read_text('utf8'))
            self.assertEqual(read_json(self.run/'ocr_attempts'/f"{x['item_id']}-whole_first.json")['state'],'saved')

    def test_originally_claimed_image_is_blocked_but_others_continue(self):
        claim_ocr(self.run,'IMG-001')
        e=_Engine()
        out=run_local_batch(self.run,engine=e)
        self.assertEqual(out['ok'],['IMG-002'])
        self.assertEqual(out['blocked'][0]['item_id'],'IMG-001')
        self.assertEqual(len(e.calls),1)
        self.assertEqual(status_run(self.run)['counts']['ocr_inflight'],1)
        # recover previously obtained OCR without calling engine
        recovered=self.base/'recover.json'
        recovered.write_text(json.dumps({'results':[{'item_id':'IMG-001','ocr_text':'从既有工具日志原样复制'}]},ensure_ascii=False),encoding='utf8')
        self.assertEqual(ingest_batch(self.run,recovered)['accepted'],1)
        self.assertEqual(status_run(self.run)['counts']['needs_jd'],2)

    def test_empty_output_fails_closed(self):
        e=_Engine('')
        out=run_local_batch(self.run,engine=e,max_items=1)
        self.assertEqual(len(e.calls),1)
        self.assertEqual(out['failed'][0]['item_id'],'IMG-001')
        self.assertEqual(status_run(self.run)['counts']['ocr_failed'],1)
        out=run_local_batch(self.run,engine=e)
        self.assertEqual(len(e.calls),2)  # IMG-002 only; IMG-001 not retried
        self.assertEqual(len(out['blocked']),1)
        self.assertEqual(len(out['failed']),1)

    def test_dependency_error_before_any_claim(self):
        with patch('ocr_local_batch.make_rapidocr_engine',side_effect=RuntimeError('请安装')):
            with self.assertRaisesRegex(RuntimeError,'请安装'):
                run_local_batch(self.run)
        self.assertEqual(status_run(self.run)['counts']['needs_ocr'],2)

    def test_ocr_result_validation(self):
        self.assertEqual(text_from_rapidocr(_Result(['a','b','c'])), 'a\nb\nc')
        with self.assertRaises(ValueError): text_from_rapidocr(object())

if __name__=='__main__': unittest.main()

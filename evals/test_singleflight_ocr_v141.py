"""合成测试：OCR同图单飞、失败切片以及用户画像评分兼容。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'evals'))
from core import (prepare_run, claim_ocr, fail_ocr, status_run, ingest_batch,
                  activate_slice_fallback, read_json)
from helpers import create_test_image, make_profile, ocr_for

class SingleFlightOCRTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.image=self.root/'large.png'
        create_test_image(self.image,size=(350,3500))
        self.profile=self.root/'p.json'
        self.profile.write_text(json.dumps(make_profile(),ensure_ascii=False),encoding='utf8')
        self.run=self.root/'run'
        self.manifest=prepare_run([str(self.image)],str(self.profile),str(self.run))
        self.item=self.manifest['items'][0]
        self.item_id=self.item['item_id']
    def tearDown(self): self.temp.cleanup()

    def test_claim_only_once_and_status_blocks_inflight(self):
        result=claim_ocr(self.run,self.item_id)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['strategy'],'whole_first')
        self.assertEqual(result['source_paths'],[self.item['source_path']])
        with self.assertRaisesRegex(ValueError,'已领取OCR调用'):
            claim_ocr(self.run,self.item_id)
        st=status_run(self.run)
        self.assertEqual(st['counts']['ocr_inflight'],1)
        self.assertEqual(st['pending'],[])
        self.assertEqual(st['blocked_ocr'][0]['item_id'],self.item_id)

    def test_successful_ingest_marks_saved_and_does_not_need_ocr(self):
        claim_ocr(self.run,self.item_id)
        batch=self.root/'batch.json'
        batch.write_text(json.dumps({'results':[{'item_id':self.item_id,'ocr_text':ocr_for('agent')}]},ensure_ascii=False),encoding='utf8')
        self.assertEqual(ingest_batch(self.run,batch)['accepted'],1)
        self.assertEqual(status_run(self.run)['counts']['needs_jd'],1)
        attempt=read_json(self.run/'ocr_attempts'/f'{self.item_id}-whole_first.json')
        self.assertEqual(attempt['state'],'saved')
        with self.assertRaisesRegex(ValueError,'已保存'):
            claim_ocr(self.run,self.item_id)
        with self.assertRaisesRegex(ValueError,'已入库'):
            fail_ocr(self.run,self.item_id,'EMPTY_OUTPUT_FALLBACK')

    def test_failure_requires_new_input_strategy(self):
        claim_ocr(self.run,self.item_id)
        self.assertEqual(fail_ocr(self.run,self.item_id,'EMPTY_OUTPUT_FALLBACK')['state'],'failed')
        self.assertEqual(status_run(self.run)['counts']['ocr_failed'],1)
        with self.assertRaisesRegex(ValueError,'已领取OCR调用'):
            claim_ocr(self.run,self.item_id)
        self.assertGreater(activate_slice_fallback(self.run,self.item_id,'整图无有效结果')['slice_count'],1)
        self.assertEqual(status_run(self.run)['counts']['needs_ocr'],1)
        claim=claim_ocr(self.run,self.item_id)
        self.assertEqual(claim['strategy'],'sliced_fallback')
        self.assertGreater(len(claim['source_paths']),1)
        with self.assertRaisesRegex(ValueError,'已领取OCR调用'):
            claim_ocr(self.run,self.item_id)
        fail_ocr(self.run,self.item_id,'切片不可辨识')
        self.assertEqual(status_run(self.run)['counts']['ocr_failed'],1)
        with self.assertRaisesRegex(ValueError,'已执行过切片回退'):
            activate_slice_fallback(self.run,self.item_id,'again')

    def test_cannot_slice_without_recorded_failure(self):
        claim_ocr(self.run,self.item_id)
        with self.assertRaisesRegex(ValueError,'未记录失败'):
            activate_slice_fallback(self.run,self.item_id,'保险切片')

    def test_duplicate_concurrent_claim_atomic(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self._try_claim(), range(2)))
        self.assertCountEqual(results,['ok','denied'])
    def _try_claim(self):
        try:
            claim_ocr(self.run,self.item_id)
            return 'ok'
        except ValueError:
            return 'denied'

    def test_rebuild_resets_per_run_claim_without_changing_scoring(self):
        claim_ocr(self.run,self.item_id)
        prepare_run([str(self.image)],str(self.profile),str(self.run),rebuild=True)
        self.assertTrue(claim_ocr(self.run,self.item_id)['allowed'])

if __name__=='__main__': unittest.main()

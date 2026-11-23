"""v1.1 增量能力，全部使用人工合成测试数据；不代表真实模型性能。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'evals'))
from core import prepare_run, finalize_run, check_run, status_run, ingest_batch, record_metrics, read_json
from helpers import create_test_image, make_profile, ocr_for, jd_for, assessment_for, write_layers

class PerformanceV11Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.profile = make_profile()
        self.profile_path = self.base / 'profile.json'
        self.profile_path.write_text(json.dumps(self.profile, ensure_ascii=False), encoding='utf8')
    def tearDown(self):
        self.temp.cleanup()

    def test_reuse_does_not_crop_long_image(self):
        img = self.base / 'long.png'
        create_test_image(img, size=(500,3500))
        run = self.base / 'run'
        m=prepare_run([str(img)], self.profile_path, run)
        self.assertEqual(m['items'][0]['slices'], [m['items'][0]['source_path']])
        write_layers(m,self.profile)
        self.assertEqual(finalize_run(run)['excel_rows'],1)
        with patch('core.make_image_slices', side_effect=AssertionError('unexpected re-crop')):
            m2=prepare_run([str(img)], self.profile_path, run)
        self.assertEqual(m2['items'][0]['status'], 'ready')
        self.assertEqual(m2['items'][0]['slices'], [])
        self.assertEqual(finalize_run(run)['excel_rows'],1)

    def test_original_order_and_duplicate_rows(self):
        folder = self.base/'images'; folder.mkdir()
        for fname,c in [('10.png',(240,230,230)),('2.png',(230,240,230)),('1.png',(230,230,240))]:
            create_test_image(folder/fname,color=c,size=(300,400))
        (folder/'11.png').write_bytes((folder/'1.png').read_bytes())
        run=self.base/'run'
        m=prepare_run([str(folder)], self.profile_path, run)
        self.assertEqual([i['source_file'] for i in m['items']], ['1.png','2.png','10.png','11.png'])
        write_layers(m,self.profile)
        summary=finalize_run(run,sort_mode='input',include_duplicates=True)
        self.assertEqual(summary['excel_rows'],4)
        self.assertEqual(summary['excel_validation']['input_indices'],[1,2,3,4])
        self.assertEqual(check_run(run)['status'],'passed')

    def test_user_order_file(self):
        images=self.base/'input'; images.mkdir()
        for fname in ('a.png','b.png'):
            create_test_image(images/fname,size=(200,300))
        order=self.base/'order.txt'; order.write_text('input/b.png\ninput/a.png\n',encoding='utf8')
        m=prepare_run([str(images)],self.profile_path,self.base/'ordered',order_file=order)
        self.assertEqual([i['source_file'] for i in m['items']],['b.png','a.png'])

    def test_batch_ingest_rejects_invalid_without_poisoning_good(self):
        img=self.base/'agent.png'; create_test_image(img,size=(300,400))
        run=self.base/'run'; m=prepare_run([str(img)],self.profile_path,run)
        item=m['items'][0]
        self.assertEqual(status_run(run)['counts']['needs_ocr'],1)
        good={'item_id': item['item_id'],'ocr_text': ocr_for('agent'),
              'jd':jd_for(item,'agent'),'assessment':assessment_for(item,self.profile,'agent')}
        bad={**good,'jd':{**good['jd'],'sha256':'a'*64}}
        batch=self.base/'bad.json';batch.write_text(json.dumps([bad],ensure_ascii=False),encoding='utf8')
        result=ingest_batch(run,batch)
        self.assertEqual(result['rejected'],1)
        self.assertFalse(Path(item['ocr_path']).exists())
        self.assertFalse(Path(item['assessment_path']).exists())
        batch.write_text(json.dumps({'results':[good]},ensure_ascii=False),encoding='utf8')
        result=ingest_batch(run,batch)
        self.assertEqual(result['accepted'],1)
        self.assertEqual(status_run(run)['counts']['needs_validation'],1)
        s=finalize_run(run,sort_mode='input')
        self.assertEqual(s['excel_rows'],1)
        self.assertEqual(status_run(run)['counts']['validated'],1)
        metrics=record_metrics(run,input_tokens=900,output_tokens=100,confirmations=1,active_seconds=12,credits=2.5)
        self.assertGreaterEqual(metrics['wall_seconds'],0)
        self.assertEqual(read_json(run/'performance.json')['measurements'][0]['input_tokens'],900)

    def test_ocr_only_ingest_resume(self):
        img=self.base/'agent.png'; create_test_image(img,size=(200,300))
        run=self.base/'run';m=prepare_run([str(img)],self.profile_path,run)
        item=m['items'][0]
        source=self.base/'part.jsonl';source.write_text(json.dumps({'item_id':item['item_id'],'ocr_text':ocr_for('agent')},ensure_ascii=False)+'\n',encoding='utf8')
        self.assertEqual(ingest_batch(run,source)['accepted'],1)
        self.assertEqual(status_run(run)['counts']['needs_jd'],1)
        source.write_text(json.dumps({'item_id':item['item_id'],'jd':jd_for(item,'agent'),'assessment':assessment_for(item,self.profile,'agent')},ensure_ascii=False)+'\n',encoding='utf8')
        self.assertEqual(ingest_batch(run,source)['accepted'],1)
        self.assertEqual(status_run(run)['counts']['needs_validation'],1)
        self.assertEqual(finalize_run(run)['excel_rows'],1)

if __name__=='__main__': unittest.main()

"""V3 测试：年限正文优先、可解释证据、跨画像缓存与整图回退。全为合成数据。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'evals'))
from core import validate_schema, validate_jd_evidence, prepare_run, activate_slice_fallback, read_json
from helpers import jd_for, ocr_for, make_profile, create_test_image

class Version3RulesTests(unittest.TestCase):
    def setUp(self):
        self.item = {'source_file':'test.png', 'sha256':'a'*64,'ocr_path':'IMG-001.txt'}
        self.jd = jd_for(self.item,'agent')
        self.ocr = ocr_for('agent')

    def set_conflict(self, heading, detail, level, effective, conflict=True):
        self.ocr += f'\n招聘栏：{heading}\n任职要求：{detail}\n'
        self.jd['experience'] = effective
        self.jd['experience_resolution'] = {
            'listing_experience': heading, 'detail_experience': detail,
            'detail_requirement_level':level,'effective_experience':effective,'conflict':conflict,
            'listing_evidence':[heading], 'detail_evidence':[detail],
            'resolution_reason':'TEST DATA ONLY'
        }
        self.jd['field_evidence']['experience'] = [detail if level=='required' else heading]

    def test_detail_required_precedes_listing_no_limit(self):
        self.set_conflict('经验不限','3年以上后端开发经验','required','3年以上后端开发经验')
        validate_schema(self.jd, 'jd')
        validate_jd_evidence(self.jd,self.ocr)

    def test_detail_required_precedes_listing_range(self):
        self.set_conflict('1–3年','5年以上平台开发经验','required','5年以上平台开发经验')
        validate_jd_evidence(self.jd,self.ocr)

    def test_reject_using_listing_when_detail_required(self):
        self.set_conflict('经验不限','3年以上相关经验','required','经验不限')
        with self.assertRaisesRegex(ValueError,'JD正文明确年限'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_preferred_does_not_create_hard_requirement(self):
        self.set_conflict('经验不限','3年以上经验优先','preferred','经验不限')
        validate_jd_evidence(self.jd,self.ocr)
        self.jd['experience_resolution']['effective_experience']='3年以上经验优先'
        self.jd['experience']='3年以上经验优先'
        with self.assertRaisesRegex(ValueError,'优先经验不得'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_conflicting_requirements_flagged(self):
        self.set_conflict('经验不限','要求3年以上同时要求应届生','conflicting','待确认',conflict=True)
        self.jd['field_evidence']['experience']=[]
        validate_jd_evidence(self.jd,self.ocr)
        self.jd['experience_resolution']['conflict']=False
        with self.assertRaisesRegex(ValueError,'标记冲突'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_missing_new_contract_rejected(self):
        del self.jd['experience_resolution']
        with self.assertRaises(Exception):
            validate_schema(self.jd,'jd')

    def test_no_synthetic_evidence(self):
        self.set_conflict('经验不限','3年以上后端开发经验','required','3年以上后端开发经验')
        self.jd['experience_resolution']['detail_evidence']=['并不存在的3年经验要求']
        with self.assertRaisesRegex(ValueError,'不是 OCR 原文子串'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_new_run_default_whole_and_repeated_fallback_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            img=root/'large.png'
            create_test_image(img,size=(400,4500))
            profile=root/'profile.json';profile.write_text(json.dumps(make_profile(),ensure_ascii=False))
            run=root/'run'
            result=prepare_run([str(img)],str(profile),str(run))
            item=result['items'][0]
            self.assertEqual(item['slices'],[item['source_path']])
            fallback=activate_slice_fallback(run,item['item_id'],'整图正文被截断')
            self.assertGreater(fallback['slice_count'],1)
            with self.assertRaisesRegex(ValueError,'已执行过'):
                activate_slice_fallback(run,item['item_id'],'整图正文被截断')

if __name__=='__main__':
    unittest.main()

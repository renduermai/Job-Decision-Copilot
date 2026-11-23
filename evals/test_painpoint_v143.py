"""V1.4.3 卡点输出不污染既有评分快照，合成数据验证证据防护。"""
import json
import html
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'evals')]
from core import prepare_run, finalize_run, decide_score, validate_schema, read_json, ASSESSMENT_CONTRACT_VERSION
from painpoints import format_pain_point, MISSING_PROBLEM, MISSING_SOLUTION
from helpers import create_test_image, make_profile, assessment_for, jd_for, ocr_for
from skill_insights import create_insights, render_chat_report


class PainPointV143Tests(unittest.TestCase):
    def setUp(self):
        self.ocr = ocr_for('agent')
        self.profile = make_profile()
        self.profile['background'] += '；做过Text-to-SQL和RAG项目'
        self.a = {'problem': '现有Agent输出质量波动，缺少一致评测',
                  'jd_evidence_quotes': ['开发Agent应用与RAG评测'],
                  'solution': '利用Text-to-SQL的Bad Case归因，建立验证集与回归流程',
                  'solution_basis':'candidate_experience',
                  'candidate_evidence_quote':'Text-to-SQL'}

    def test_candidate_experience_verified(self):
        r = format_pain_point(self.a, self.ocr, self.profile)
        self.assertEqual(r['status'], 'ok')
        self.assertIn('【依据JD推断】', r['problem'])
        self.assertIn('【已有经历可迁移】', r['solution'])
        self.assertIn('Text-to-SQL', r['solution'])

    def test_same_jd_other_profile_not_auto_assumed(self):
        r = format_pain_point(self.a, self.ocr, make_profile())
        self.assertIn('未在已确认画像中找到', r['solution'])
        self.assertEqual(r['status'], 'partial')

    def test_no_hardcoded_user_skills_from_preferences(self):
        profile = make_profile()
        profile['priority_conditions'] = ['Text-to-SQL']
        r = format_pain_point(self.a, self.ocr, profile)
        self.assertEqual(r['status'], 'partial')

    def test_jd_quote_must_be_exact(self):
        bad = {**self.a, 'jd_evidence_quotes':['不是OCR原文的虚构事实']}
        r = format_pain_point(bad, self.ocr, self.profile)
        self.assertEqual(r['problem'], MISSING_PROBLEM)
        self.assertEqual(r['solution'], MISSING_SOLUTION)

    def test_employer_expectation_no_fabricated_skills(self):
        a = {**self.a, 'solution_basis':'employer_expectation', 'candidate_evidence_quote':'',
             'solution':'根据岗位要求建设RAG评测脚本与错误分类'}
        r = format_pain_point(a, self.ocr, make_profile())
        self.assertEqual(r['status'],'ok')
        self.assertIn('【岗位期待的解决方向】',r['solution'])
        self.assertNotIn('已有经历可迁移',r['solution'])

    def test_missing_enrichment_doesnt_halt_or_change_score(self):
        item = {'sha256':'a'*64, 'source_file':'agent.png', 'ocr_path':'/tmp/IMG-001.txt'}
        profile = make_profile()
        a=assessment_for(item,profile,'agent')
        jd=jd_for(item,'agent')
        score_before = decide_score(a,profile,self.ocr,jd)['recommendation_score']
        a['pain_point_analysis'] = {**self.a, 'jd_evidence_quotes':['伪造内容']}
        validate_schema(a, 'assessment')
        score_after = decide_score(a,profile,self.ocr,jd)['recommendation_score']
        self.assertEqual(score_before,score_after)
        self.assertEqual(score_before,4)

    def test_assessment_schema_extension_remains_optional(self):
        item={'sha256':'a'*64, 'source_file':'agent.png', 'ocr_path':'/tmp/IMG-001.txt'}
        a=assessment_for(item,make_profile(),'agent')
        validate_schema(a,'assessment')
        a['pain_point_analysis']=self.a
        validate_schema(a,'assessment')
        self.assertIn('painpoint-v4', ASSESSMENT_CONTRACT_VERSION)

    def test_pipeline_chat_excel_with_extra_columns(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            src=base/'IMG-001.png'
            create_test_image(src)
            profile_path=base/'profile.json'
            profile_path.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf-8')
            run=base/'run'
            manifest=prepare_run([str(src)],str(profile_path),str(run))
            item=manifest['items'][0]
            Path(item['ocr_path']).write_text(self.ocr,encoding='utf-8')
            Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf-8')
            a=assessment_for(item,self.profile,'agent')
            a['pain_point_analysis']=self.a
            Path(item['assessment_path']).write_text(json.dumps(a,ensure_ascii=False),encoding='utf-8')
            summary=finalize_run(run)
            self.assertEqual(summary['excel_rows'],1)
            self.assertEqual(summary['excel_validation']['scores'],[4])
            md=Path(summary['chat_summary']).read_text(encoding='utf8')
            self.assertIn('| 岗位 | 评分 | 卡点判断 | 卡点如何解决 |', md)
            self.assertIn('现有Agent输出质量波动',md)
            self.assertIn('【已有经历可迁移】',md)
            self.assertIn('高分JD最常见的核心技能',md)
            dec=read_json(item['decision_path'])
            self.assertEqual(dec['recommendation_score'],4)
            # 确认Excel真实保存两个新增列和数据（不修改任何Excel）。
            with zipfile.ZipFile(summary['excel']) as z:
                xml=html.unescape(z.read('xl/worksheets/sheet1.xml').decode('utf8'))
            self.assertIn('卡点判断',xml)
            self.assertIn('卡点如何解决',xml)
            self.assertIn('现有Agent输出质量波动',xml)

    def test_bad_quote_does_not_drop_job(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            src=base/'IMG-001.png'; create_test_image(src)
            path=base/'profile.json'; path.write_text(json.dumps(self.profile,ensure_ascii=False), encoding='utf8')
            run=base/'run'
            item=prepare_run([str(src)],str(path),str(run))['items'][0]
            Path(item['ocr_path']).write_text(self.ocr,encoding='utf8')
            Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf8')
            a=assessment_for(item,self.profile,'agent')
            a['pain_point_analysis']={**self.a,'jd_evidence_quotes':['JD不存在的原文']}
            Path(item['assessment_path']).write_text(json.dumps(a,ensure_ascii=False),encoding='utf8')
            output=finalize_run(run)
            self.assertEqual(output['excel_rows'],1)
            self.assertEqual(output['excel_validation']['scores'],[4])
            self.assertIn('待分析',read_json(item['decision_path'])['pain_point_analysis']['problem'])

if __name__=='__main__':
    unittest.main()

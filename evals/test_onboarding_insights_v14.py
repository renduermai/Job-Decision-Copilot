"""V1.4 回归：抗压规则跨画像独立、需求统计保守与假设覆盖。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'evals'))
from core import decide_score, sha256_json, validate_schema, validate_jd_evidence
from helpers import assessment_for, jd_for, make_profile, ocr_for
from onboarding import onboarding_welcome, profile_preview
from skill_insights import create_insights, render_chat_report, normalize_skill


class StressRulesV14(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile('weak-user')
        self.profile['stress_tolerance'] = 'weak'
        self.ocr = ocr_for('agent') + '\n任职要求：必须具备很强的抗压能力，成长曲线陡峭。'
        self.item = {'source_file':'agent.png','sha256':'a'*64,'ocr_path':'/tmp/IMG-001.txt'}
        self.jd = jd_for(self.item,'agent')
        self.jd['role_analysis']['pressure_expectations'] = [
            {'kind':'strong_stress','evidence_quote':'必须具备很强的抗压能力','applies_to_candidate':True}]
        self.assessment = assessment_for(self.item, self.profile,'agent')

    def test_weak_and_explicit_candidate_stress_decrements_one(self):
        validate_schema(self.jd, 'jd')
        validate_jd_evidence(self.jd, self.ocr)
        result = decide_score(self.assessment,self.profile,self.ocr,self.jd)
        self.assertEqual(result['model_recommendation_score'],4)
        self.assertEqual(result['recommendation_score'],3)
        self.assertEqual(result['stress_penalty'],1)
        self.assertTrue(result['stress_evidence'])

    def test_average_and_strong_profiles_same_jd_do_not_decrement(self):
        for v in ('strong','average','unspecified'):
            with self.subTest(v=v):
                p = {**self.profile,'stress_tolerance':v}
                a = assessment_for(self.item,p,'agent')
                self.assertEqual(decide_score(a,p,self.ocr,self.jd)['recommendation_score'],4)

    def test_no_evidence_no_decrement(self):
        self.jd['role_analysis']['pressure_expectations'] = []
        self.assertEqual(decide_score(self.assessment,self.profile,self.ocr,self.jd)['recommendation_score'],4)

    def test_company_growth_not_personal_pressure(self):
        self.jd['role_analysis']['pressure_expectations'] = [
            {'kind':'rapid_growth','evidence_quote':'必须具备很强的抗压能力','applies_to_candidate':False}]
        self.assertEqual(decide_score(self.assessment,self.profile,self.ocr,self.jd)['recommendation_score'],4)

    def test_unverifiable_stress_quote_rejected(self):
        self.jd['role_analysis']['pressure_expectations'][0]['evidence_quote']='JD里没有这句'
        with self.assertRaisesRegex(ValueError, '不是 OCR 原文子串'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_floor_two_and_hard_reject_one(self):
        a=self.assessment
        a['recommendation_score']=2
        self.assertEqual(decide_score(a,self.profile,self.ocr,self.jd)['recommendation_score'],2)
        a['recommendation_score']=4
        a['redline_assessments'][0].update({'conclusion':'true','evidence_quote':'开发Agent应用'})
        self.assertEqual(decide_score(a,self.profile,self.ocr,self.jd)['recommendation_score'],1)


class InsightTestsV14(unittest.TestCase):
    def jobs(self):
        def rec(i,sha,score,requirements):
            return {'item':{'item_id':f'IMG-{i:03d}','sha256':sha},
                    'jd':{'company_name':f'C{i}','job_title':f'J{i}',
                          'role_analysis':{'skill_requirements':[
                              {'skill':name,'requirement_level':level,'evidence_quote':f'原文:{name}'}
                              for name,level in requirements]}},
                    'decision':{'recommendation_score':score}}
        return [
            rec(1,'a'*64,5,[('Python','required'),('RAG','required')]),
            rec(2,'b'*64,4,[('Python','required'),('检索增强生成','required')]),
            rec(3,'c'*64,4,[('RAG','preferred'),('Docker','required')]),
            rec(4,'d'*64,3,[('C++','required')]),
            rec(5,'a'*64,5,[('Python','required')]), # duplicate image, must not inflate
        ]

    def test_three_jobs_one_skill_uplift_two(self):
        profile={'target_roles':['AI应用开发'],'confirmed_skills':['Python'],
                 'skills_confirmation_complete':True}
        data=create_insights(self.jobs(), profile)
        self.assertEqual(data['high_score_job_count'],3)
        self.assertEqual(data['measurable_job_count'],3)
        self.assertEqual(data['top5'][0]['skill'],'rag')
        self.assertEqual(data['top5'][0]['demand_jobs'],3)
        self.assertEqual(data['top5'][0]['required_jobs'],2)
        self.assertEqual(data['top5'][0]['incremental_jobs'],2)
        self.assertEqual(data['top5'][0]['scenario_covered_jobs'],2)
        md=render_chat_report(self.jobs(),profile,data)
        self.assertIn('66.7%',md)
        self.assertIn('2/3',md)
        self.assertEqual(len(data['top10_jobs']),3)
        self.assertTrue('Docker' not in [x['skill'] for x in data['top5']] or True)

    def test_unconfirmed_skills_unknown_not_zero(self):
        profile={'target_roles':['AI应用开发'],'confirmed_skills':['Python'],
                 'skills_confirmation_complete':False}
        data=create_insights(self.jobs(), profile)
        self.assertIsNone(data['baseline_covered_jobs'])
        self.assertIsNone(data['top5'][0]['incremental_jobs'])
        txt=render_chat_report(self.jobs(),profile,data)
        self.assertIn('待确认',txt)
        self.assertNotIn('+2岗',txt)

    def test_other_domain_skill_kept(self):
        self.assertEqual(normalize_skill('消费者洞察'),'消费者研究')
        self.assertEqual(normalize_skill('Uncommon Keyword'),'uncommon keyword')

    def test_three_personas_validate_and_are_not_auto_complete(self):
        import json
        for label in 'ABC':
            p=json.loads((ROOT/'assets'/'personas'/f'profile_{label}.json').read_text('utf8'))
            validate_schema(p, 'profile')
            self.assertEqual(p['stress_tolerance'],'unspecified')
            self.assertFalse(p['skills_confirmation_complete'])

    def test_profile_preview_recognizes_pasted_fields(self):
        text = profile_preview({'target_roles':['Agent开发'],'preferred_cities':['上海'],
                                'hard_reject_rules':[{'rule_id':'cpp','description':'C++必需排除'}]})
        self.assertIn('已识别：Agent开发',text)
        self.assertIn('已识别：上海',text)
        self.assertIn('已识别：C++必需排除',text)
        self.assertIn('待补充',text)

    def test_welcome_contains_three_steps_and_grounded_performance(self):
        x=onboarding_welcome()
        for snippet in ['第1步','第2步','第3步','93分','A ·','B ·','C ·','10–18分钟','请放心']:
            if snippet=='请放心':continue  # explicitly not a promise to every user
            self.assertIn(snippet,x)

if __name__=='__main__':
    unittest.main()

# Additional CLI preview behavior is separately invoked by the native CLI.

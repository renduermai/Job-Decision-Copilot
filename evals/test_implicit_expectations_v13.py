"""V1.3 合成证据回归：对照性测试，不代表腾讯实时模型评分。"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'evals'))
from core import decide_score, validate_jd_evidence, validate_schema, prepare_run, sha256_json, write_json
from helpers import assessment_for, create_test_image, jd_for, make_profile, ocr_for

class ImplicitExpectationsV13Tests(unittest.TestCase):
    def setUp(self):
        self.item={'source_file':'synthetic.png','sha256':'a'*64,'ocr_path':'IMG-001.txt'}
        self.ocr=ocr_for('agent')
        self.jd=jd_for(self.item,'agent')
        self.app=make_profile('app-person', rules=())
        self.platform=make_profile('platform-person', rules=())
        self.platform['background']='TEST DATA ONLY 资深Agent平台和模型推理部署工程师'
        self.assertNotEqual(sha256_json(self.app),sha256_json(self.platform))

    def add_signal(self,quote,kind,section,item_index=1):
        self.ocr+='\n'+quote
        self.jd['role_analysis']['expectation_signals'].append({
          'kind':kind, 'section':section,'item_index':item_index,'evidence_quote':quote})

    def assessment(self,profile,conclusion='major_gap',quotes=None,score=4):
        result=assessment_for(self.item,profile,'agent')
        result['recommendation_score']=score
        result['capability_fit']={
          'conclusion':conclusion, 'evidence_quotes':quotes or [],
          'rationale':'TEST DATA ONLY：与当前用户已确认的工程/算法背景存在或不存在差距'}
        return result

    def test_cross_section_requirement_responsibility_caps_major_gap(self):
        a='参与模型推理加速、微调与强化学习等性能优化工作'
        b='深刻理解Transformer与LLM原理'
        c='有模型微调或强化学习相关经验优先'
        self.add_signal(a,'algorithm_depth','responsibility',5)
        self.add_signal(b,'algorithm_depth','requirement',1)
        self.add_signal(c,'algorithm_depth','bonus',2)
        validate_schema(self.jd,'jd')
        validate_jd_evidence(self.jd,self.ocr)
        aa=self.assessment(self.app,quotes=[a,b,c])
        validate_schema(aa,'assessment')
        d=decide_score(aa,self.app,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],3)
        self.assertFalse(d['hard_reject'])
        self.assertEqual(d['model_recommendation_score'],4)
        self.assertEqual(d['capability_fit']['conclusion'],'major_gap')
        self.assertEqual(d['applied_caps'][0]['max_score'],3)

    def test_platform_user_not_auto_penalized_for_same_jd(self):
        a='参与模型推理加速、微调与强化学习等性能优化工作'
        b='深刻理解Transformer与LLM原理'
        self.add_signal(a,'algorithm_depth','responsibility')
        self.add_signal(b,'algorithm_depth','requirement')
        d=decide_score(self.assessment(self.platform,'aligned',[a,b]),self.platform,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],4)
        self.assertEqual(d['applied_caps'],[])

    def test_salary_and_experience_no_automatic_cap(self):
        a='上海/60-90K·16薪'
        b='经验不限'
        self.add_signal(a,'compensation_context','header')
        self.add_signal(b,'experience_openness','header')
        d=decide_score(self.assessment(self.app,'major_gap',[a,b]),self.app,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],4)
        self.assertFalse(d['hard_reject'])
        self.assertTrue(any('跨章节' in q for q in d['pending_conditions']))

    def test_long_excerpt_variant_works_but_short_keyword_not_enough(self):
        a='岗位职责5：参与模型推理加速、微调与强化学习等性能优化工作'
        b='岗位要求1：深刻理解Transformer与LLM原理'
        self.add_signal(a,'algorithm_depth','responsibility')
        self.add_signal(b,'algorithm_depth','requirement')
        d=decide_score(self.assessment(self.app,quotes=['参与模型推理加速、微调与强化学习','深刻理解Transformer与LLM原理']),self.app,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],3)
        short=decide_score(self.assessment(self.app,quotes=['模型','原理']),self.app,self.ocr,self.jd)
        self.assertEqual(short['recommendation_score'],4)

    def test_third_unrelated_profile_mismatch_is_independent(self):
        a='参与模型推理加速、微调与强化学习等性能优化工作'
        b='深刻理解Transformer与LLM原理'
        self.add_signal(a,'algorithm_depth','responsibility')
        self.add_signal(b,'algorithm_depth','requirement')
        third=make_profile('marketing-analytics-person',rules=())
        third['target_roles']=['品牌消费者洞察、社媒数据分析']
        third['background']='TEST DATA ONLY：4年品牌运营经验，了解数据分析'
        assessment=self.assessment(third,'unknown',[],score=4)
        assessment['role_fit']={
          'conclusion':'mismatch', 'evidence_quote':'开发Agent应用',
          'rationale':'TEST DATA ONLY：与品牌洞察目标明显不符'}
        result=decide_score(assessment,third,self.ocr,self.jd)
        self.assertEqual(result['recommendation_score'],2)
        self.assertFalse(result['hard_reject'])

    def test_requirement_and_bonus_corroboration_caps(self):
        a='深刻理解Transformer与LLM原理'
        b='有模型微调或强化学习相关经验优先'
        self.add_signal(a,'algorithm_depth','requirement',1)
        self.add_signal(b,'algorithm_depth','bonus',2)
        d=decide_score(self.assessment(self.app,quotes=[a,b]),self.app,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],3)

    def test_bonus_only_and_rhetoric_no_cap(self):
        a='精通大模型优先'
        b='熟悉模型微调者优先'
        self.add_signal(a,'strong_wording','bonus',1)
        self.add_signal(b,'algorithm_depth','bonus',2)
        d=decide_score(self.assessment(self.app,quotes=[a,b]),self.app,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],4)

    def test_first_item_position_alone_is_not_priority_rule(self):
        a='岗位职责1：负责产品日常沟通'
        b='加分项1：精通算法优先'
        self.add_signal(a,'strong_wording','responsibility',1)
        self.add_signal(b,'strong_wording','bonus',1)
        d=decide_score(self.assessment(self.app,quotes=[a,b]),self.app,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],4)

    def test_major_gap_single_quote_rejected(self):
        a='深刻理解Transformer与LLM原理'
        self.add_signal(a,'algorithm_depth','requirement')
        with self.assertRaisesRegex(ValueError,'至少需要两条'):
            decide_score(self.assessment(self.app,quotes=[a]),self.app,self.ocr,self.jd)

    def test_hallucinated_quote_rejected(self):
        a='深刻理解Transformer与LLM原理'
        b='不存在于OCR中的强化学习要求'
        self.add_signal(a,'algorithm_depth','requirement')
        with self.assertRaisesRegex(ValueError,'不是 OCR 原文子串'):
            decide_score(self.assessment(self.app,quotes=[a,b]),self.app,self.ocr,self.jd)

    def test_hard_reject_remains_exclusive_to_profile_rule(self):
        a='参与模型推理加速、微调与强化学习等性能优化工作'
        b='深刻理解Transformer与LLM原理'
        self.add_signal(a,'algorithm_depth','responsibility')
        self.add_signal(b,'algorithm_depth','requirement')
        profile=make_profile('no-model-optimization-person',rules=())
        profile['hard_reject_rules']=[{
          'rule_id':'model_optimization_in_duties',
          'description':'用户明确排除承担任何实际微调/推理优化职责的岗位'}]
        assess=self.assessment(profile,quotes=[a,b])
        assess['redline_assessments']=[{
          'rule_id':'model_optimization_in_duties','conclusion':'true',
          'evidence_quote':a,'rationale':'TEST：明确命中已配置的红线'}]
        d=decide_score(assess,profile,self.ocr,self.jd)
        self.assertEqual(d['recommendation_score'],1)
        self.assertTrue(d['hard_reject'])
        other=decide_score(self.assessment(self.platform,'aligned',[a,b]),self.platform,self.ocr,self.jd)
        self.assertEqual(other['recommendation_score'],4)

    def test_invalid_role_signal_quote_rejected(self):
        self.jd['role_analysis']['expectation_signals'].append({
          'kind':'algorithm_depth','section':'requirement','evidence_quote':'虚构的技术要求'})
        with self.assertRaisesRegex(ValueError,'不是 OCR 原文子串'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_v12_old_run_preserves_ocr_but_rebuilds_jd(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            image=folder/'sample.png';create_test_image(image)
            profile_path=folder/'profile.json';write_json(profile_path,self.app)
            run_dir=folder/'run'
            old=prepare_run([str(image)],str(profile_path),str(run_dir))
            item=old['items'][0]
            Path(item['ocr_path']).write_text(self.ocr,encoding='utf8')
            write_json(item['jd_path'],self.jd)
            write_json(item['assessment_path'],self.assessment(self.app,'partial',['开发Agent应用']))
            manifest_file=run_dir/'manifest.json'
            manifest=__import__('json').loads(manifest_file.read_text(encoding='utf8'))
            manifest['jd_contract_version']='role-facts-v2'
            manifest['assessment_contract_version']='profile-fit-v2'
            write_json(manifest_file,manifest)
            restored=prepare_run([str(image)],str(profile_path),str(run_dir))
            item=restored['items'][0]
            self.assertEqual(item['status'],'needs_jd')
            self.assertTrue(Path(item['ocr_path']).is_file())
            self.assertFalse(Path(item['jd_path']).is_file())
            self.assertFalse(Path(item['assessment_path']).is_file())

if __name__=='__main__':
    unittest.main()

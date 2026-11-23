"""V1.2 新增合成语义契约测试；不冒充实际视觉模型对7张JD的命中率。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'evals'))

from core import decide_score, validate_jd_evidence, validate_schema, prepare_run, sha256_json, write_json
from helpers import assessment_for, jd_for, make_profile, ocr_for, create_test_image


class ProfileAwareV12Tests(unittest.TestCase):
    def setUp(self):
        self.item = {"source_file": "case.png", "sha256": "a" * 64, "ocr_path": "/tmp/IMG-001.txt"}
        self.ocr = ocr_for('agent')
        self.jd = jd_for(self.item, 'agent')
        self.profile = make_profile(rules=())

    def updated_assessment(self, profile=None):
        profile = profile or self.profile
        return assessment_for(self.item, profile, 'agent')

    def test_required_skill_fact_autotriggers_only_configured_profile(self):
        """同一岗位事实，不同画像的结论必须不同；模型即使遗漏红线也会被事实层校正。"""
        # OCR 和岗位事实构造，确保原文中的“必须”关系明确。
        self.ocr += '必须熟练C++，负责CAD后端。\n'.replace('\u0002', '')
        self.jd['role_analysis']['skill_requirements'].append({
            'skill':'C++', 'requirement_level':'required', 'evidence_quote':'必须熟练C++'
        })
        validate_jd_evidence(self.jd, self.ocr)
        profile_a = make_profile('reject-cpp', rules=())
        profile_a['hard_reject_rules'] = [{
            'rule_id':'cpp_required','description':'必备C++技能排除',
            'structured_condition':{'type':'required_skill','skill':'C++'},
        }]
        assess_a = self.updated_assessment(profile_a)
        assess_a['redline_assessments'] = [{
            'rule_id':'cpp_required','conclusion':'false','evidence_quote':'',
            'rationale':'TEST: 模拟遗漏必需条件',
        }]
        result_a = decide_score(assess_a, profile_a, self.ocr, self.jd)
        self.assertEqual(result_a['recommendation_score'], 1)
        self.assertEqual(result_a['triggered_rules'][0]['origin'], 'structured_facts')
        self.assertEqual(result_a['triggered_rules'][0]['evidence_quote'], '必须熟练C++')
        profile_b = make_profile('cpp-engineer', rules=())
        assess_b = self.updated_assessment(profile_b)
        result_b = decide_score(assess_b, profile_b, self.ocr, self.jd)
        self.assertEqual(result_b['recommendation_score'], 4)
        self.assertFalse(result_b['hard_reject'])

    def test_preferred_cpp_not_autotriggers(self):
        self.ocr += '了解C++优先。\n'
        self.jd['role_analysis']['skill_requirements'].append({
            'skill':'C++','requirement_level':'preferred','evidence_quote':'了解C++优先'
        })
        self.profile['hard_reject_rules'] = [{
            'rule_id':'cpp','description':'C++为必需技能排除',
            'structured_condition':{'type':'required_skill','skill':'C++'},
        }]
        a = self.updated_assessment()
        a['redline_assessments'] = [{'rule_id':'cpp','conclusion':'false','evidence_quote':'','rationale':'TEST: 仅优先'}]
        self.assertEqual(decide_score(a,self.profile,self.ocr,self.jd)['recommendation_score'],4)

    def test_model_cannot_force_typed_redline_from_preferred_skill(self):
        self.ocr += '了解C++优先。\n'
        self.jd['role_analysis']['skill_requirements'].append({
            'skill':'C++','requirement_level':'preferred','evidence_quote':'了解C++优先'
        })
        self.profile['hard_reject_rules'] = [{
            'rule_id':'cpp','description':'C++必需技能排除',
            'structured_condition':{'type':'required_skill','skill':'C++'},
        }]
        a=self.updated_assessment()
        a['redline_assessments']=[{'rule_id':'cpp','conclusion':'true',
                                  'evidence_quote':'了解C++优先','rationale':'TEST: 错判必须'}]
        with self.assertRaisesRegex(ValueError,'强制程度/主要职责冲突'):
            decide_score(a,self.profile,self.ocr,self.jd)

    def test_java_alternative_not_autotriggers(self):
        self.ocr += 'Go/Python/Java任选一门。\n'
        self.jd['role_analysis']['skill_requirements'].append({
            'skill':'Java','requirement_level':'alternative','evidence_quote':'Go/Python/Java任选一门'
        })
        self.profile['hard_reject_rules'] = [{
            'rule_id':'java_required','description':'Java必需排除',
            'structured_condition':{'type':'required_skill','skill':'Java'},
        }]
        a = self.updated_assessment()
        a['redline_assessments'] = [{'rule_id':'java_required','conclusion':'false','evidence_quote':'','rationale':'TEST: 任选'}]
        self.assertFalse(decide_score(a,self.profile,self.ocr,self.jd)['hard_reject'])

    def test_no_global_blacklist_for_backend_role(self):
        self.ocr += '负责服务端基础设施设计与开发。\n'
        self.jd['role_analysis']['primary_role_family'] = 'backend_engineering'
        self.jd['role_analysis']['responsibility_evidence'] = [{
            'role_family':'backend_engineering','importance':'core',
            'evidence_quote':'负责服务端基础设施设计与开发',
        }]
        first = make_profile('application-only',rules=())
        first['hard_reject_rules'] = [{
            'rule_id':'backend_role','description':'后端基础设施为主要职责排除',
            'structured_condition':{'type':'primary_role','role_family':'backend_engineering'},
        }]
        a = self.updated_assessment(first)
        a['redline_assessments'] = [{'rule_id':'backend_role','conclusion':'unknown','evidence_quote':'','rationale':'TEST: 语义未确认'}]
        self.assertTrue(decide_score(a,first,self.ocr,self.jd)['hard_reject'])
        second = make_profile('backend-person', rules=())
        b = self.updated_assessment(second)
        self.assertEqual(decide_score(b,second,self.ocr,self.jd)['recommendation_score'],4)

    def test_role_mismatch_caps_without_hard_reject(self):
        a = self.updated_assessment()
        a['role_fit'] = {'conclusion':'mismatch','evidence_quote':'开发Agent应用','rationale':'TEST: 此用户只希望做传统后端'}
        result=decide_score(a,self.profile,self.ocr,self.jd)
        self.assertEqual(result['recommendation_score'],2)
        self.assertFalse(result['hard_reject'])
        self.assertEqual(result['applied_caps'][0]['max_score'],2)
        self.assertIn('限制为2分',result['recommendation_reason'])

    def test_configured_soft_cap_is_per_profile(self):
        selected=make_profile('avoid-operation-heavy',rules=())
        selected['soft_risk_rules']=[{'rule_id':'heavy_operation','description':'工作内容严重偏运营分析','max_score':3}]
        a=self.updated_assessment(selected)
        a['soft_risk_assessments']=[{'rule_id':'heavy_operation','conclusion':'true',
                                   'evidence_quote':'开发Agent应用','rationale':'TEST: 模拟引用有效原文'}]
        self.assertEqual(decide_score(a,selected,self.ocr,self.jd)['recommendation_score'],3)
        b=self.updated_assessment(self.profile)
        self.assertEqual(decide_score(b,self.profile,self.ocr,self.jd)['recommendation_score'],4)

    def test_wrong_profile_fingerprint_blocks_stale_assessment(self):
        a=self.updated_assessment()
        modified=copy.deepcopy(self.profile)
        modified['priority_conditions']=['全新岗位目标']
        self.assertEqual(modified['profile_id'],self.profile['profile_id'])
        with self.assertRaisesRegex(ValueError,'画像SHA256'):
            decide_score(a,modified,self.ocr,self.jd)

    def test_soft_rule_list_must_match_profile(self):
        a=self.updated_assessment()
        self.profile['soft_risk_rules']=[{'rule_id':'mismatch_role','description':'行业偏移','max_score':2}]
        a['profile_sha256']=sha256_json(self.profile)
        with self.assertRaisesRegex(ValueError,'软性风险评估与画像规则不一致'):
            decide_score(a,self.profile,self.ocr,self.jd)

    def test_invalid_role_evidence_blocked(self):
        self.jd['role_analysis']['skill_requirements'][0]['evidence_quote']='这句话并不存在'
        with self.assertRaisesRegex(ValueError,'不是 OCR 原文子串'):
            validate_jd_evidence(self.jd,self.ocr)

    def test_structured_condition_type_and_required_field(self):
        self.profile['hard_reject_rules']=[{'rule_id':'bad','description':'配置不完整',
            'structured_condition':{'type':'required_skill'}}]
        a=self.updated_assessment()
        a['redline_assessments']=[{'rule_id':'bad','conclusion':'unknown','evidence_quote':'','rationale':'TEST'}]
        with self.assertRaisesRegex(ValueError,'缺少 skill'):
            decide_score(a,self.profile,self.ocr,self.jd)

    def test_old_version_preserves_ocr_but_rebuilds_jd_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            image=root/'test.jpg'
            create_test_image(image)
            profile_path=root/'profile.json';write_json(profile_path,self.profile)
            run_dir=root/'run'
            manifest=prepare_run([str(image)],str(profile_path),str(run_dir))
            item=manifest['items'][0]
            Path(item['ocr_path']).write_text(self.ocr,encoding='utf8')
            from helpers import jd_for
            Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf8')
            old_manifest=json.loads((run_dir/'manifest.json').read_text())
            old_manifest.pop('jd_contract_version',None)
            old_manifest.pop('assessment_contract_version',None)
            write_json(run_dir/'manifest.json',old_manifest)
            refreshed=prepare_run([str(image)],str(profile_path),str(run_dir))
            item=refreshed['items'][0]
            self.assertEqual(item['status'],'needs_jd')
            self.assertEqual(Path(item['ocr_path']).read_text(encoding='utf8'),self.ocr)
            self.assertFalse(Path(item['jd_path']).is_file())


if __name__=='__main__':
    unittest.main()

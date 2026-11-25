"""V1.4.8: trust audit and voluntary showcase must never recalculate historic scores."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'evals')]
from helpers import make_profile, create_test_image, write_layers
from core import prepare_run, finalize_run, check_run, read_json, decide_score
from opportunity_audit import evaluate_audit, audit_run
from showcase import build_showcase, showcase_run
from learning_updates import _topic, preview


class FeedbackAndOpportunities148(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.profile=make_profile()
        self.profile_file=self.root/'profile.json'
        self.profile_file.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()

    def make_run(self, low_agent=False):
        a=self.root/'agent.png'; b=self.root/'java.png'
        create_test_image(a,color=(230,240,255))
        create_test_image(b,color=(255,240,230))
        run=self.root/'run'
        m=prepare_run([str(a),str(b)],str(self.profile_file),str(run))
        write_layers(m,self.profile)
        if low_agent:
            for item in m['items']:
                if item['source_file']=='agent.png':
                    x=read_json(item['assessment_path'])
                    x['recommendation_score']=2
                    Path(item['assessment_path']).write_text(json.dumps(x,ensure_ascii=False),encoding='utf-8')
        summary=finalize_run(run,sort_mode='input')
        return run,summary,m

    def test_audit_flags_uncertain_low_but_not_evidenced_hard_reject(self):
        run,summary,_=self.make_run(low_agent=True)
        audit=read_json(run/'audit_report.json')
        self.assertEqual(audit['reviewed_low_score'],2)
        self.assertEqual(audit['validated_hard_exclusions'],1)
        self.assertEqual(audit['flagged_total'],1)
        self.assertEqual(audit['items'][0]['score'],2)
        wb=load_workbook(summary['excel'])
        try:
            self.assertIn('低分复核',wb.sheetnames)
            self.assertEqual(wb['岗位汇总']['C2'].value,2)
            self.assertEqual(wb['岗位汇总']['C3'].value,1)
            self.assertEqual(wb['岗位汇总'].freeze_panes,'D2')
            self.assertEqual(wb['低分复核'].max_row,2)
            self.assertIn('Agent',wb['低分复核']['C2'].value)
        finally:
            wb.close()
        self.assertEqual(check_run(run)['status'],'passed')
        before=Path(summary['excel']).read_bytes()
        result=audit_run(run)
        self.assertFalse(result['excel_updated'])
        self.assertEqual(before,Path(summary['excel']).read_bytes())

    def test_explicit_reaudit_replaces_review_sheet_but_not_scores(self):
        run,summary,_=self.make_run(low_agent=True)
        result=audit_run(run,update_excel=True)
        self.assertTrue(result['excel_updated'])
        wb=load_workbook(summary['excel'])
        try:
            self.assertEqual(wb.sheetnames.count('低分复核'),1)
            self.assertEqual([wb['岗位汇总']['C2'].value,wb['岗位汇总']['C3'].value],[2,1])
        finally:wb.close()
        self.assertEqual(check_run(run)['status'],'passed')

    def test_fast_text_export_keeps_review_sheet(self):
        from excel import export_excel_fast
        from core import read_json
        run,summary,m=self.make_run(low_agent=True)
        item=m['items'][0]
        jd=read_json(item['jd_path'])
        decision=read_json(item['decision_path'])
        rec={'item':{**item,'input_kind':'text'},'jd':jd,'decision':decision,
             'ocr_text':Path(item['ocr_path']).read_text(encoding='utf-8')}
        target=self.root/'fast_review.xlsx'
        export_excel_fast(target,[rec],run,audit=read_json(run/'audit_report.json'))
        wb=load_workbook(target)
        try:
            self.assertIn('低分复核',wb.sheetnames)
            self.assertEqual(wb['岗位汇总']['C2'].value,2)
            self.assertEqual(wb['岗位汇总']['A2'].value,'无')
            self.assertEqual(wb['岗位汇总'].freeze_panes,'D2')
        finally:wb.close()

    def test_showcase_from_high_score_jd_no_fake_project_metrics(self):
        self.profile['confirmed_skills']=['Python']
        self.profile_file.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf-8')
        run,summary,_=self.make_run()
        before=Path(summary['excel']).read_bytes()
        result=showcase_run(run)
        self.assertEqual(result['status'],'suggested')
        self.assertGreaterEqual(result['suggestions'],1)
        output=read_json(run/'showcase_opportunities.json')
        first=output['items'][0]
        self.assertEqual(first['candidate_evidence']['kind'],'confirmed_skill')
        self.assertEqual(first['candidate_evidence']['quote'],'Python')
        self.assertIn('熟悉Python',str(first['jd_evidence']))
        self.assertEqual(before,Path(summary['excel']).read_bytes())
        self.assertEqual(check_run(run)['status'],'passed')

    def test_hard_redline_with_alternative_skill_is_only_reviewed_not_promoted(self):
        rec={"item":{"item_id":"IMG-777","sha256":"demo777"},
             "jd":{"company_name":"测试","job_title":"Agent开发", "role_analysis":{
                 "primary_role_family":"agent_application", "skill_requirements":[
                     {"skill":"Java", "requirement_level":"alternative", "evidence_quote":"Python或Java任选"}],
                 "ambiguities":[]}, "experience_resolution":{}},
             "decision":{"recommendation_score":1, "hard_reject":True,
                 "triggered_rules":[{"rule_id":"java_backend_primary","description":"Java后端为主则排除","evidence_quote":"Python或Java任选","origin":"semantic_assessment"}],
                 "pending_conditions":[],"role_fit":{"conclusion":"aligned"}}}
        audit=evaluate_audit([rec],self.profile)
        self.assertEqual(audit['flagged_total'],1)
        self.assertEqual(audit['items'][0]['score'],1)
        self.assertIn('任选',audit['items'][0]['reason'])
        self.assertEqual(rec['decision']['recommendation_score'],1)

    def test_showcase_no_profile_skill_not_invented(self):
        fake={'high_score_job_count':3,'all_skill_demands':[{'skill':'Kubernetes','demand_jobs':2,'required_jobs':2,'demand_coverage_percent':66.7}],
              'all_skill_evidence':{'kubernetes':[{'item_id':'IMG-002','quote':'负责Kubernetes平台'}]}}
        x=build_showcase(fake,make_profile())
        self.assertEqual(x['items'][0]['candidate_evidence']['kind'],'not_provided')
        self.assertIn('先核对',x['items'][0]['next_action'])
        self.assertEqual(x['items'][0]['jd_evidence'][0]['quote'],'负责Kubernetes平台')

    def test_feedback_routes_without_excel_edits(self):
        self.assertEqual(_topic('自动化是好，风险也大，不如老老实实自己搞')[1],'audit_request')
        self.assertEqual(_topic('发表自己的想法让老板找上门')[1],'showcase_request')
        self.assertEqual(_topic('这种工具卖信息')[1],'transparency_question')
        run,summary,_=self.make_run()
        text=self.root/'comment.txt';text.write_text('让老板找上门',encoding='utf-8')
        before=Path(summary['excel']).read_bytes()
        result=preview(run,text)
        self.assertEqual(result['suggested_action'],'showcase')
        self.assertTrue(result['excel_unchanged'])
        self.assertEqual(before,Path(summary['excel']).read_bytes())

    def test_docs_are_public_and_distinguish_planned_from_proven(self):
        for p in ['docs/USER_FEEDBACK.md','references/feedback-and-opportunities-v148.md']:
            self.assertTrue((ROOT/p).is_file())
        for path in ['SKILL.md','README.md','scripts/onboarding.py']:
            self.assertIn('反馈', (ROOT/path).read_text(encoding='utf-8'))
        readme=(ROOT/'README.md').read_text(encoding='utf-8')
        self.assertIn('flowchart TD',readme)
        self.assertIn('V1.1',readme)
        self.assertIn('V1.4.8',readme)
        self.assertIn('github.com/renduermai/Job-Decision-Copilot',readme)

if __name__=='__main__':unittest.main()

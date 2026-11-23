"""V1.4.7: post-delivery user learning content must never disturb historical scores."""
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'evals')]
from core import prepare_run,finalize_run,read_json
from helpers import make_profile,jd_for,assessment_for,ocr_for,create_test_image
from learning_updates import preview,apply


class LearningContent147(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.profile=make_profile()
        self.profile_file=self.root/'profile.json'
        self.profile_file.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf8')
        img=self.root/'job.png'
        create_test_image(img)
        self.run=self.root/'run'
        m=prepare_run([str(img)],str(self.profile_file),str(self.run))
        item=m['items'][0]
        Path(item['ocr_path']).write_text(ocr_for('agent'),encoding='utf8')
        Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf8')
        Path(item['assessment_path']).write_text(json.dumps(assessment_for(item,self.profile,'agent'),ensure_ascii=False),encoding='utf8')
        self.summary=finalize_run(self.run,sort_mode='input')
        self.note=self.root/'note.txt'
        self.note.write_text('来自小红书博主：噜噜oni。面试时反向提问HR：岗位挂了多久，团队组织架构，筛选简历关键词，薪资构成。',encoding='utf8')

    def tearDown(self):self.temp.cleanup()

    def headers(self):
        with closing(load_workbook(self.summary['excel'])) as book:
            ws=book['岗位汇总']
            return [c.value for c in ws[1]]

    def test_preview_does_not_change_workbook_and_detects_related_column(self):
        before=Path(self.summary['excel']).read_bytes()
        p=preview(self.run,self.note,creator='噜噜oni')
        self.assertEqual(p['suggested_column'],'面试反向提问')
        self.assertTrue(p['requires_new_column_confirmation'])
        self.assertIn('待确认条件',p['related_existing_columns'])
        self.assertEqual(before,Path(self.summary['excel']).read_bytes())
        self.assertIn('薪酬由固定月薪',p['sample_jobs'][0]['suggestion'])
        self.assertIn('岗位开放多久',p['sample_jobs'][0]['suggestion'])
        self.assertIn('团队人数',p['sample_jobs'][0]['suggestion'])
        self.assertIn('筛选简历',p['sample_jobs'][0]['suggestion'])

    def test_requires_explicit_confirmation_for_new_col(self):
        p=preview(self.run,self.note)
        with self.assertRaises(PermissionError):
            apply(self.run,p['draft'])
        self.assertNotIn('面试反向提问',self.headers())
        r=apply(self.run,p['draft'],confirm_new_column=True)
        self.assertEqual(r['status'],'updated')
        self.assertIn('面试反向提问',self.headers())
        with closing(load_workbook(self.summary['excel'])) as book:
            ws=book['岗位汇总']; col=self.headers().index('面试反向提问')+1
            self.assertIn('HC是新增还是替补',ws.cell(2,col).value)
            self.assertEqual(ws['C2'].value,4)
            self.assertEqual(ws.freeze_panes,'D2')
            self.assertEqual(len(ws._images),1)
        # Duplicate application must not make endless append chains.
        r2=apply(self.run,p['draft'],confirm_new_column=True)
        with closing(load_workbook(self.summary['excel'])) as book:
            ws=book['岗位汇总']; col=self.headers().index('面试反向提问')+1
            self.assertEqual(ws.cell(2,col).value.count('HC是新增还是替补'),1)
        # A later finalize should replay confirmed columns without re-OCR or re-score.
        s=finalize_run(self.run,sort_mode='input')
        with closing(load_workbook(s['excel'])) as book:
            ws=book['岗位汇总']; col=self.headers().index('面试反向提问')+1
            self.assertTrue(ws.cell(2,col).value)
            self.assertEqual(ws['C2'].value,4)
        from core import check_run
        self.assertEqual(check_run(self.run)['status'],'passed')

    def test_existing_column_updates_without_new_confirmation_and_keeps_score(self):
        p=preview(self.run,self.note,column='卡点判断')
        self.assertFalse(p['requires_new_column_confirmation'])
        proposal=read_json(p['draft'])
        # Existing column update is a user-directed derived-field patch only.
        proposal['updates']=[{'item_id':'IMG-001','column':'卡点判断',
                              'value':'新提问：该岗位开了多久？','mode':'append'}]
        Path(p['draft']).write_text(json.dumps(proposal,ensure_ascii=False),encoding='utf8')
        r=apply(self.run,p['draft'])
        self.assertEqual(r['updated_cells'],1)
        with closing(load_workbook(self.summary['excel'])) as book:
            ws=book['岗位汇总']; idx=self.headers().index('卡点判断')+1
            self.assertIn('该岗位开了多久',ws.cell(2,idx).value)
            self.assertIn('待分析：JD未提供足够证据',ws.cell(2,idx).value)
            self.assertEqual(ws['C2'].value,4)

    def test_immutable_column_and_forged_jd_quote_blocked(self):
        p=preview(self.run,self.note)
        draft=Path(p['draft']); data=read_json(draft)
        data['updates'][0]['column']='推荐等级（1-5分）'
        draft.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
        with self.assertRaises(ValueError):apply(self.run,draft,confirm_new_column=True)
        data['updates'][0]['column']='面试反向提问'
        data['updates'][0]['jd_evidence']='JD里根本没有的内部信息'
        draft.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
        with self.assertRaises(ValueError):apply(self.run,draft,confirm_new_column=True)

    def test_profile_aware_hr_question_selection(self):
        from excel import HEADERS
        from learning_updates import _suggest_questions
        h={x:i for i,x in enumerate(HEADERS,1)}
        row=[None]*len(HEADERS)
        row[h['岗位名称']-1]='Agent开发'
        row[h['JD全文']-1]='要求：精通C++，熟悉Python，负责Agent运行时架构'
        row[h['薪资范围']-1]='30-50K'
        row[h['卡点判断']-1]='需核实工程职责'
        a={**self.profile,'hard_reject_rules':[{'rule_id':'cpp','description':'C++为必须条件时排除'}]}
        b={**self.profile,'hard_reject_rules':[{'rule_id':'outsourcing','description':'外包排除'}]}
        self.assertIn('C++是必须独立编写',_suggest_questions(row,h,'反向提问HR',a))
        self.assertNotIn('C++是必须独立编写',_suggest_questions(row,h,'反向提问HR',b))

    def test_large_fast_export_maintains_contract_after_learning_patch(self):
        from excel import export_excel_fast,validate_excel
        from learning_updates import _update_workbook
        m=read_json(self.run/'manifest.json'); item=m['items'][0]
        jd=read_json(item['jd_path']); decision=read_json(item['decision_path'])
        records=[]
        for n in range(1,902):
            records.append({'item':{**item,'item_id':f'IMG-{n:03d}', 'input_kind':'text',
                                    'input_index':n},'jd':jd,'decision':decision,'ocr_text':ocr_for('agent')})
        excel=self.root/'fast901.xlsx'
        export_excel_fast(excel,records,self.run)
        _update_workbook(excel,[{'item_id':'IMG-781','column':'面试反向提问',
                                  'value':'确认前90天实际交付与质量验收标准'}],{'面试反向提问'})
        result=validate_excel(excel,901,sort_mode='input',expected_images=0)
        self.assertEqual(result['rows'],901)
        with closing(load_workbook(excel,read_only=True)) as book:
            ws=book['岗位汇总'];h={c.value:i for i,c in enumerate(ws[1],1)}
            self.assertEqual(ws.cell(782,h['面试反向提问']).value,'确认前90天实际交付与质量验收标准')
            self.assertEqual(ws.cell(782,3).value,4)
        with closing(load_workbook(excel)) as full:
            self.assertEqual(full['岗位汇总'].freeze_panes,'D2')

    def test_other_career_notes_must_not_be_autofabricated(self):
        self.note.write_text('来自职场博主：今天聊新人如何建立工作任务日志和及时向主管同步进度。',encoding='utf8')
        p=preview(self.run,self.note)
        self.assertEqual(p['topic'],'职场内容补充')
        self.assertFalse(read_json(p['draft'])['updates'])
        with self.assertRaises(ValueError):apply(self.run,p['draft'],confirm_new_column=True)

if __name__=='__main__':unittest.main()

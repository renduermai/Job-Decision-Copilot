"""V1.4.6: high-volume routes, no scoring regressions, full JD/painpoints in exports."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'evals')]
from bulk_ops import calibrate_documents,merge_runs,merge_from_cache,batch_finalize,split_run,MERGE_HEADERS
from document_input import prepare_documents
from jd_render import build_jd_full_text
from core import prepare_run,finalize_run,ingest_batch,read_json
from excel import export_excel,validate_excel,HEADERS
from helpers import make_profile,jd_for,assessment_for,ocr_for,create_test_image

def book_original_fulltext(path,headers):
 with closing(load_workbook(path)) as book:
  return book['全量整合表'].cell(2,headers.index('JD全文')+1).value

class Bulk146(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.profile=make_profile()
  self.pf=self.root/'profile.json';self.pf.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf8')
 def tearDown(self):self.tmp.cleanup()

 def image_run(self,name='first'):
  img=self.root/f'{name}.png';create_test_image(img)
  run=self.root/f'run-{name}'
  m=prepare_run([str(img)],str(self.pf),str(run))
  item=m['items'][0]
  Path(item['ocr_path']).write_text(ocr_for('agent'),encoding='utf8')
  Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf8')
  a=assessment_for(item,self.profile,'agent')
  a['pain_point_analysis']={'problem':'建设Agent可靠评测','solution':'用评测集和回归测试解决稳定性问题',
         'solution_basis':'employer_expectation','candidate_evidence_quote':'',
         'jd_evidence_quotes':['开发Agent应用']}
  Path(item['assessment_path']).write_text(json.dumps(a,ensure_ascii=False),encoding='utf8')
  s=finalize_run(run,sort_mode='input')
  return run,s

 def test_full_jd_renders_raw_metadata_not_missing(self):
  raw='序号：35\n岗位ID：888\n公司：甲\n部门：大模型\n发布时间：2026年11月\n投递链接：https://example.com\n职责：研发Agent'
  jd={'company_name':'甲','job_title':'算法工程师','location':'上海','experience':'3年'}
  text=build_jd_full_text(jd,raw)
  self.assertIn('部门：大模型',text);self.assertIn('投递链接',text)
  self.assertNotIn('岗位ID：888',text);self.assertNotIn('序号：35',text)

 def test_source_excel_adds_full_jd_and_keeps_score_column(self):
  run,s=self.image_run()
  with closing(load_workbook(s['excel'])) as book:
   ws=book['岗位汇总'];self.assertEqual(ws['C1'].value,'推荐等级（1-5分）')
   self.assertEqual(ws.freeze_panes,'D2')
   self.assertEqual([ws.cell(1,c).value for c in range(1,len(HEADERS)+1)],HEADERS)
   self.assertEqual(ws.cell(2,HEADERS.index('JD全文')+1).value.startswith('【公司】'),True)
   self.assertIn('【依据JD推断】',ws.cell(2,HEADERS.index('卡点判断')+1).value)
   self.assertIn('【岗位期待的解决方向】',ws.cell(2,HEADERS.index('卡点如何解决')+1).value)

 def test_dry_run_does_not_mutate(self):
  img=self.root/'original.png';create_test_image(img)
  run=self.root/'dry';m=prepare_run([str(img)],str(self.pf),str(run))
  item=m['items'][0];jd=jd_for(item,'agent');assess=assessment_for(item,self.profile,'agent')
  f=self.root/'batch.json';f.write_text(json.dumps({'results':[{'item_id':item['item_id'],
                      'ocr_text':ocr_for('agent'),'jd':jd,'assessment':assess}]},ensure_ascii=False),encoding='utf8')
  res=ingest_batch(run,f,dry_run=True)
  self.assertEqual(res['accepted'],1);self.assertFalse(Path(item['ocr_path']).exists())
  real=ingest_batch(run,f)
  self.assertEqual(real['accepted'],1);self.assertTrue(Path(item['assessment_path']).exists())

 def test_duplicate_text_is_not_reassessed_but_export_keeps_source_rows(self):
  source=self.root/'three.csv'
  # identical CSV row twice: same text digest
  with source.open('w',encoding='utf8',newline='') as f:
   w=csv.writer(f);w.writerow(['公司','岗位','JD']);w.writerow(['A','Agent','负责业务Agent开发']);w.writerow(['A','Agent','负责业务Agent开发'])
  run=self.root/'dupes';m=prepare_documents([str(source)],str(self.pf),str(run))
  self.assertEqual([i['status'] for i in m['items']],['needs_jd','duplicate'])
  self.assertEqual(m['items'][1]['duplicate_of'],m['items'][0]['item_id'])
  # Direct export test avoids a fake JD/evidence for CSV whose source text differs.

 def test_multi_file_dedup_split_preserves_both_original_files(self):
  sources=[self.root/'first.csv',self.root/'second.csv']
  for source in sources:
   with source.open('w',encoding='utf8',newline='') as f:
    w=csv.writer(f);w.writerow(['文字']);w.writerow([ocr_for('agent')])
  run=self.root/'multi';m=prepare_documents([str(s) for s in sources],str(self.pf),str(run))
  self.assertEqual([i['status'] for i in m['items']],['needs_jd','duplicate'])
  first=m['items'][0]
  Path(first['jd_path']).write_text(json.dumps(jd_for(first,'agent'),ensure_ascii=False),encoding='utf8')
  Path(first['assessment_path']).write_text(json.dumps(assessment_for(first,self.profile,'agent'),ensure_ascii=False),encoding='utf8')
  report=finalize_run(run,sort_mode='input')
  self.assertEqual(report['excel_rows'],2)
  result=split_run(run,self.root/'splits',also_merge=True)
  self.assertEqual(result['total_rows'],2)
  self.assertEqual(len(result['per_document']),2)
  self.assertEqual(result['merge_summary']['all_rows'],2)
  self.assertEqual(result['merge_summary']['unique_jobs'],1)
  for entry in result['per_document']:
   with closing(load_workbook(entry['output'])) as book:
    ws=book['岗位汇总'];self.assertEqual(ws['C2'].value,4)
    self.assertTrue(ws.cell(2,HEADERS.index('JD全文')+1).value)

 def test_large_fast_export_has_contract(self):
  run,s=self.image_run()
  m=read_json(run/'manifest.json');i=m['items'][0]
  jd=jd_for(i,'agent');dec=read_json(i['decision_path'])
  records=[{'item':{**i,'input_kind':'text'},'jd':jd,'decision':dec,'ocr_text':ocr_for('agent')}]
  out=self.root/'fast.xlsx';export_excel(out,records,run,fast=True)
  result=validate_excel(out,1,sort_mode='input',expected_images=0)
  self.assertEqual(result['rows'],1)
  with closing(load_workbook(out)) as w:
   self.assertEqual(w['岗位汇总']['A2'].value,'无')
   self.assertIn('JD全文',HEADERS)

 def test_calibrate_deterministic_and_empty_category(self):
  source=self.root/'jobs.csv'
  with source.open('w',encoding='utf8',newline='') as f:
   w=csv.writer(f);w.writerow(['名称','描述'])
   for i in range(10):w.writerow([f'公司{i}',f'这是产品经理岗位{i}' if i%2 else f'后端工程师开发{i}'])
  a=self.root/'a.jsonl';b=self.root/'b.jsonl'
  x=calibrate_documents([str(source)],a,per_category=3)
  y=calibrate_documents([str(source)],b,per_category=3)
  self.assertEqual(x['sample_rows'],6);self.assertEqual(a.read_bytes(),b.read_bytes())

 def test_merge_uses_settled_decisions_no_rescore(self):
  run,s=self.image_run()
  output=self.root/'merged.xlsx';result=merge_runs([run],output)
  self.assertEqual(result['status'],'succeeded');self.assertEqual(result['all_rows'],1)
  with closing(load_workbook(output)) as book:
   self.assertEqual(book.sheetnames,['全量整合表','高分岗位','公司分布'])
   for tab in ('全量整合表','高分岗位'):
    ws=book[tab]
    self.assertEqual(ws['C1'].value,'推荐等级（1-5分）')
    for name in ('JD全文','卡点判断','卡点如何解决'):
     col=MERGE_HEADERS.index(name)+1
     self.assertTrue(ws.cell(2,col).value)
  self.assertEqual(batch_finalize([str(run)])['status'],'passed')
  result2=merge_from_cache(result['cache_jsonl'],self.root/'cached.xlsx')
  self.assertEqual(result2['all_rows'],result['all_rows'])
  with closing(load_workbook(result2['output'])) as cached:
   self.assertEqual(cached['高分岗位']['C2'].value,4)
   self.assertEqual(cached['全量整合表'].cell(2,MERGE_HEADERS.index('JD全文')+1).value,
                    book_original_fulltext(output,MERGE_HEADERS))

if __name__=='__main__':unittest.main()

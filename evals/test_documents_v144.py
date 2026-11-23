"""V1.4.4 文本文件/含图片Excel路由，C列推荐等级与无图片安全导出。"""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'evals'))
from openpyxl import Workbook,load_workbook
from openpyxl.drawing.image import Image as SheetImage
from core import finalize_run,read_json,prepare_run
from document_input import prepare_documents,inspect_document
from helpers import make_profile,ocr_for,jd_for,assessment_for,create_test_image

class DocumentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.profile=make_profile(); self.profile_path=self.base/'profile.json'
        self.profile_path.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf8')
    def tearDown(self): self.temp.cleanup()
    def complete(self,manifest):
        for item in manifest['items']:
            assert item['input_kind']=='text'
            self.assertTrue(Path(item['ocr_path']).read_text(encoding='utf8').strip())
            self.assertFalse(Path(item['jd_path']).exists())
            self.assertEqual(item['status'],'needs_jd')
            jd=jd_for(item,'agent');a=assessment_for(item,self.profile,'agent')
            Path(item['jd_path']).write_text(json.dumps(jd,ensure_ascii=False),encoding='utf8')
            Path(item['assessment_path']).write_text(json.dumps(a,ensure_ascii=False),encoding='utf8')
        return finalize_run(Path(manifest['items'][0]['ocr_path']).parents[1],sort_mode='input')

    def test_csv_text_rows_do_not_create_pictures(self):
        source=self.base/'jobs.csv'
        with source.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f); w.writerow(['原始编号','岗位详情','图片'])
            w.writerow([1,ocr_for('agent'),'无']);w.writerow([2,ocr_for('agent'),'无'])
        self.assertEqual(inspect_document(source)['embedded_images'],0)
        run=self.base/'run'; manifest=prepare_documents([str(source)],str(self.profile_path),str(run))
        self.assertEqual(manifest['input_mode'],'text_only');self.assertEqual(len(manifest['items']),2)
        self.assertEqual([i['input_index'] for i in manifest['items']],[1,2])
        summary=self.complete(manifest)
        self.assertEqual(summary['excel_rows'],2)
        self.assertEqual(summary['excel_validation']['images'],0)
        with closing(load_workbook(summary['excel'])) as book:
            sheet=book['岗位汇总']
            self.assertEqual(sheet['C1'].value,'推荐等级（1-5分）')
            self.assertEqual(sheet.freeze_panes,'D2')
            self.assertEqual([sheet[f'A{r}'].value for r in (2,3)],['无','无'])
            self.assertEqual([sheet[f'C{r}'].value for r in (2,3)],[4,4])
            self.assertEqual(len(sheet._images),0)
            self.assertEqual(sheet['D2'].value,'示例科技公司')
            self.assertEqual(sheet['W2'].value,1)  # 原始序号保持在W列

    def test_xlsx_no_embedded_images_preserves_order_and_rerate(self):
        doc=self.base/'jobs.xlsx'; book=Workbook(); ws=book.active; ws.title='目标岗位'
        ws.append(['岗位详情','备注']);ws.append([ocr_for('agent'),'第一条']);ws.append([ocr_for('agent'),'第二条']);book.save(doc);book.close()
        self.assertEqual(inspect_document(doc)['type'],'text_only')
        run=self.base/'run';manifest=prepare_documents([str(doc)],str(self.profile_path),str(run))
        self.assertTrue(all(x['source_file'].startswith('jobs.xlsx#目标岗位!R') for x in manifest['items']))
        self.assertEqual([x['status'] for x in manifest['items']],['needs_jd','needs_jd'])
        self.complete(manifest)
        alt=make_profile('new-profile',('agent_primary','outsourcing_or_onsite'))
        pf=self.base/'newprofile.json';pf.write_text(json.dumps(alt,ensure_ascii=False),encoding='utf8')
        call=subprocess.run([sys.executable,str(ROOT/'scripts'/'run.py'),'rerate','--run-dir',str(run),'--profile',str(pf),'--output',str(self.base/'rerated')],capture_output=True,text=True)
        self.assertEqual(call.returncode,0,call.stderr)
        rerate=read_json(self.base/'rerated'/'manifest.json')
        self.assertEqual([x['status'] for x in rerate['items']],['needs_assessment','needs_assessment'])
        self.assertTrue(all(Path(x['ocr_path']).is_file() for x in rerate['items']))

    def test_xlsx_embedded_images_route_to_real_images(self):
        img=self.base/'photo.png';create_test_image(img)
        source=self.base/'with-images.xlsx';book=Workbook();s=book.active;s.append(['序号','图片']);s.append([1]);s.add_image(SheetImage(str(img)),'B2');book.save(source);book.close()
        self.assertEqual(inspect_document(source)['type'],'spreadsheet_with_images')
        run=self.base/'run';manifest=prepare_documents([str(source)],str(self.profile_path),str(run))
        self.assertEqual(manifest['input_mode'],'embedded_images')
        self.assertEqual(len(manifest['items']),1)
        self.assertEqual(manifest['items'][0]['status'],'needs_ocr')
        self.assertIn('with-images.xlsx',manifest['items'][0]['original_reference'])

    def test_large_text_batch_is_not_sent_to_ocr(self):
        csvpath=self.base/'bulk.csv'
        with csvpath.open('w',encoding='utf-8-sig',newline='') as out:
            writer=csv.writer(out);writer.writerow(['公司','岗位名称','JD文本'])
            for i in range(900):
                writer.writerow([f'公司{i}',f'岗位{i}',f'工作职责{i}，了解RAG应用'])
        call=subprocess.run([sys.executable,str(ROOT/'scripts'/'run.py'),'prepare',
            '--input',str(csvpath),'--profile',str(self.profile_path),'--output',str(self.base/'bulk-run')],
            capture_output=True,text=True)
        self.assertEqual(call.returncode,0,call.stderr)
        result=json.loads(call.stdout)
        self.assertEqual(result['items'],900)
        self.assertEqual(result['input_mode'],'text_only')
        self.assertIn('跳过analyzeImage',result['next'])
        manifest=read_json(self.base/'bulk-run'/'manifest.json')
        self.assertEqual(len(manifest['items']),900)
        self.assertTrue(all(i['status']=='needs_jd' and i['input_kind']=='text' for i in manifest['items']))
        self.assertEqual(list((self.base/'bulk-run'/'slices').glob('*.png')), [])

    def test_existing_image_mode_score_col_is_third(self):
        img=self.base/'photo.png';create_test_image(img)
        run=self.base/'images-run';manifest=prepare_run([str(img)],str(self.profile_path),str(run))
        item=manifest['items'][0]
        Path(item['ocr_path']).write_text(ocr_for('agent'),encoding='utf8')
        Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf8')
        Path(item['assessment_path']).write_text(json.dumps(assessment_for(item,self.profile,'agent'),ensure_ascii=False),encoding='utf8')
        output=finalize_run(run,sort_mode='input')
        self.assertEqual(output['excel_validation']['images'],1)
        with closing(load_workbook(output['excel'])) as book:
            sheet=book['岗位汇总'];self.assertEqual(sheet.freeze_panes,'D2')
            self.assertEqual(sheet['C2'].value,4)
            self.assertIsNone(sheet['A2'].value)
            self.assertEqual(len(sheet._images),1)

if __name__=='__main__': unittest.main()

"""V1.4.5: output name contextual, portable, no scoring changes."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'evals'))
from output_naming import safe_keyword,resolve_output_filename
from core import prepare_run,finalize_run,read_json
from document_input import prepare_documents
from helpers import create_test_image,make_profile,write_layers,jd_for,assessment_for,ocr_for


class OutputNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.profile=make_profile()
        self.profile_path=self.base/'profile.json'
        self.profile_path.write_text(json.dumps(self.profile,ensure_ascii=False),encoding='utf8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_keyword_priority_and_safe_character(self):
        m={'output_keyword':'准备_旧关键词', 'source_documents':['/tmp/投资投研.xlsx']}
        self.assertEqual(resolve_output_filename(m),'AI岗位筛选_准备_旧关键词.xlsx')
        self.assertEqual(resolve_output_filename(m,'AI岗位筛选_金融/RAG:*?'), 'AI岗位筛选_金融_RAG.xlsx')
        self.assertEqual(resolve_output_filename({'source_documents':['/tmp/岗位数据.2026.xlsx']}),'AI岗位筛选_岗位数据.2026.xlsx')
        self.assertEqual(resolve_output_filename({}),'AI岗位筛选_本批岗位.xlsx')
        self.assertEqual(safe_keyword(' '*40),'本批岗位')

    def test_document_and_embedded_source_name(self):
        self.assertEqual(resolve_output_filename({'source_documents':['/data/秋招岗位.csv']}),'AI岗位筛选_秋招岗位.xlsx')
        self.assertEqual(resolve_output_filename({'source_documents':['/data/职业筛选.xlsx'], 'input_mode':'embedded_images'}),'AI岗位筛选_职业筛选.xlsx')
        self.assertEqual(resolve_output_filename({'parameters':{'inputs':['/p/all_images']}}),'AI岗位筛选_all_images.xlsx')
        self.assertEqual(resolve_output_filename({'parameters':{'inputs':['/p/crop1.png','/p/crop2.jpg']}}),'AI岗位筛选_p.xlsx')
        self.assertEqual(resolve_output_filename({'parameters':{'inputs':['/p/a.png','/q/b.png']}}),'AI岗位筛选_2份岗位.xlsx')

    def test_csv_end_to_end_and_rename_on_second_finalize(self):
        doc=self.base/'2026秋招岗位.csv'
        with doc.open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow(['岗位详情']);w.writerow([ocr_for('agent')])
        run=self.base/'run'
        m=prepare_documents([str(doc)],str(self.profile_path),str(run))
        item=m['items'][0]
        Path(item['jd_path']).write_text(json.dumps(jd_for(item,'agent'),ensure_ascii=False),encoding='utf8')
        Path(item['assessment_path']).write_text(json.dumps(assessment_for(item,self.profile,'agent'),ensure_ascii=False),encoding='utf8')
        first=finalize_run(run)
        self.assertEqual(Path(first['excel']).name,'AI岗位筛选_2026秋招岗位.xlsx')
        self.assertTrue(Path(first['excel']).is_file())
        second=finalize_run(run,keyword='金融Agent/上海')
        self.assertEqual(Path(second['excel']).name,'AI岗位筛选_金融Agent_上海.xlsx')
        self.assertTrue(Path(second['excel']).is_file())
        self.assertFalse(Path(first['excel']).exists())
        self.assertEqual(second['excel_validation']['scores'],first['excel_validation']['scores'])

    def test_image_end_to_end_and_folder_name(self):
        folder=self.base/'上海_AI岗位';folder.mkdir()
        create_test_image(folder/'01.png')
        run=self.base/'run-images'
        m=prepare_run([str(folder)],str(self.profile_path),str(run))
        write_layers(m,self.profile)
        out=finalize_run(run)
        self.assertEqual(Path(out['excel']).name,'AI岗位筛选_上海_AI岗位.xlsx')
        self.assertEqual(out['excel_validation']['images'],1)

    def test_prepare_explicit_keyword_is_respected_by_finalize(self):
        folder=self.base/'inputs';folder.mkdir()
        create_test_image(folder/'photo.png')
        run=self.base/'run'
        m=prepare_run([str(folder)],str(self.profile_path),str(run))
        m['output_keyword']='Agent开发'
        from core import write_json
        write_json(run/'manifest.json',m)
        write_layers(m,self.profile)
        out=finalize_run(run)
        self.assertEqual(Path(out['excel']).name,'AI岗位筛选_Agent开发.xlsx')
        self.assertEqual(Path(read_json(run/'summary.json')['excel']).name,'AI岗位筛选_Agent开发.xlsx')

    def test_fresh_prepare_cleans_prior_generated_workbook(self):
        folder=self.base/'上海岗位';folder.mkdir()
        create_test_image(folder/'photo.png')
        run=self.base/'run'
        m=prepare_run([str(folder)],str(self.profile_path),str(run))
        write_layers(m,self.profile)
        result=finalize_run(run)
        self.assertTrue(Path(result['excel']).exists())
        prepare_run([str(folder)],str(self.profile_path),str(run),rebuild=True)
        self.assertFalse(Path(result['excel']).exists())


if __name__=='__main__':
    unittest.main()

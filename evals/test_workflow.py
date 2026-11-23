import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))

from core import (
    bundle_run,
    check_run,
    finalize_run,
    prepare_run,
    read_json,
    sha256_file,
)
from helpers import create_test_image, make_profile, write_layers


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.inputs = self.base / "inputs"
        self.inputs.mkdir()
        create_test_image(self.inputs / "agent.png")
        create_test_image(self.inputs / "java.jpg", color=(245, 240, 235))
        (self.inputs / "duplicate.png").write_bytes((self.inputs / "agent.png").read_bytes())
        self.profile = make_profile()
        self.profile_path = self.base / "profile.json"
        self.profile_path.write_text(json.dumps(self.profile, ensure_ascii=False), encoding="utf-8")
        self.run_dir = self.base / "run"

    def tearDown(self):
        self.temp.cleanup()

    def complete_run(self):
        manifest = prepare_run([str(self.inputs)], str(self.profile_path), str(self.run_dir))
        write_layers(manifest, self.profile)
        summary = finalize_run(self.run_dir)
        return manifest, summary

    def test_full_deterministic_workflow_sorting_excel_and_bundle(self):
        manifest, summary = self.complete_run()
        self.assertEqual(len(manifest["items"]), 3)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["excel_rows"], 2)
        self.assertEqual(summary["excel_validation"]["scores"], [4, 1])
        # V1.4 的结果必须伴随聊天总结与技能统计，而非仅新增一份隐藏文档。
        self.assertTrue(Path(summary["chat_summary"]).is_file())
        self.assertTrue(Path(summary["skill_insights"]).is_file())
        text = Path(summary["chat_summary"]).read_text(encoding="utf-8")
        self.assertIn("高分JD最常见的核心技能", text)
        self.assertIn("待确认", text)  # 合成画像没有确认完整技能清单，不得虚构覆盖增量
        self.assertEqual(check_run(self.run_dir)["status"], "passed")
        bundle = bundle_run(self.run_dir)
        with zipfile.ZipFile(bundle["zip"]) as archive:
            names = archive.namelist()
        self.assertTrue(any(name.endswith("profile.json") for name in names))
        self.assertTrue(any(name.endswith(Path(summary["excel"]).name) for name in names))
        self.assertTrue(any("/ocr/" in name for name in names))
        self.assertTrue(any("/decisions/" in name for name in names))

    def test_repeat_run_skips_and_rebuild_removes_old_layers(self):
        self.complete_run()
        repeated = prepare_run([str(self.inputs)], str(self.profile_path), str(self.run_dir))
        statuses = [item["status"] for item in repeated["items"]]
        self.assertEqual(statuses.count("ready"), 2)
        self.assertEqual(statuses.count("duplicate"), 1)
        summary = finalize_run(self.run_dir)
        self.assertEqual(summary["succeeded"], 0)
        self.assertEqual(summary["skipped"], 3)
        self.assertEqual(summary["excel_rows"], 2)

        rebuilt = prepare_run([str(self.inputs)], str(self.profile_path), str(self.run_dir), rebuild=True)
        self.assertTrue(rebuilt["parameters"]["rebuild"])
        unique = [item for item in rebuilt["items"] if item["status"] != "duplicate"]
        self.assertTrue(all(item["status"] == "needs_ocr" for item in unique))
        for item in rebuilt["items"]:
            self.assertFalse(Path(item["ocr_path"]).exists())
            self.assertFalse(Path(item["jd_path"]).exists())
            self.assertFalse(Path(item["assessment_path"]).exists())

    def test_rerate_reuses_ocr_and_jd_without_image_calls(self):
        _, original_summary = self.complete_run()
        original_decisions = {
            path.name: path.read_text(encoding="utf-8") for path in (self.run_dir / "decisions").glob("*.json")
        }
        old_manifest = read_json(self.run_dir / "manifest.json")
        old_ocr_hashes = {
            item["source_file"]: sha256_file(item["ocr_path"])
            for item in old_manifest["items"] if item["status"] != "duplicate"
        }
        new_profile = make_profile("test-different-profile", ("agent_primary", "outsourcing_or_onsite"))
        new_profile_path = self.base / "new-profile.json"
        new_profile_path.write_text(json.dumps(new_profile, ensure_ascii=False), encoding="utf-8")
        new_run = self.base / "rerate"
        command = [
            sys.executable, str(ROOT / "scripts" / "run.py"), "rerate",
            "--run-dir", str(self.run_dir), "--profile", str(new_profile_path), "--output", str(new_run),
        ]
        result = subprocess.run(command, cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ocr_reused"])
        manifest = read_json(new_run / "manifest.json")
        self.assertTrue(all(item["status"] == "needs_assessment" for item in manifest["items"]))
        for item in manifest["items"]:
            self.assertTrue(Path(item["ocr_path"]).is_file())
            self.assertTrue(Path(item["jd_path"]).is_file())
            self.assertFalse(Path(item["assessment_path"]).exists())
        new_ocr_hashes = {
            item["source_file"]: sha256_file(item["ocr_path"]) for item in manifest["items"]
        }
        self.assertEqual(old_ocr_hashes, new_ocr_hashes)

        write_layers(manifest, new_profile, only_assessment=True)
        summary = finalize_run(new_run)
        self.assertEqual(summary["excel_rows"], 2)
        self.assertEqual(summary["excel_validation"]["scores"], [2, 1])
        for name, content in original_decisions.items():
            self.assertEqual((self.run_dir / "decisions" / name).read_text(encoding="utf-8"), content)
        self.assertEqual(original_summary["excel_rows"], 2)

    def test_same_output_with_changed_profile_keeps_ocr_but_clears_assessment(self):
        self.complete_run()
        changed = make_profile("test-different-profile", ("agent_primary", "outsourcing_or_onsite"))
        changed_path = self.base / "changed.json"
        changed_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        manifest = prepare_run([str(self.inputs)], str(changed_path), str(self.run_dir))
        for item in manifest["items"]:
            if item["status"] == "duplicate":
                continue
            self.assertEqual(item["status"], "needs_assessment")
            self.assertTrue(Path(item["ocr_path"]).is_file())
            self.assertTrue(Path(item["jd_path"]).is_file())
            self.assertFalse(Path(item["assessment_path"]).exists())

    def test_long_image_whole_first_then_sliced_fallback(self):
        from core import activate_slice_fallback
        long_dir = self.base / "long"
        long_dir.mkdir()
        create_test_image(long_dir / "long.png", size=(300, 3540))
        run_dir = self.base / "long-run"
        manifest = prepare_run([str(long_dir)], str(self.profile_path), str(run_dir))
        item = manifest["items"][0]
        self.assertEqual(item["slices"], [item["source_path"]])
        self.assertEqual(item["image_strategy"], "whole_first")
        from PIL import Image
        with self.assertRaisesRegex(ValueError, "失败原因"):
            activate_slice_fallback(run_dir, item["item_id"], "")
        activated = activate_slice_fallback(run_dir, item["item_id"], "整图识别遗漏了岗位要求")
        self.assertEqual(activated["slice_count"], 2)
        new_item = read_json(run_dir / "manifest.json")["items"][0]
        slices = new_item["slices"]
        with Image.open(slices[0]) as first, Image.open(slices[1]) as second:
            self.assertEqual(first.height, 1805)
            self.assertEqual(second.height, 1805)
        with self.assertRaisesRegex(ValueError, "已执行过"):
            activate_slice_fallback(run_dir, item["item_id"], "再次试试")

    def test_empty_corrupt_and_unsupported_inputs_are_reported(self):
        empty = self.base / "empty"
        empty.mkdir()
        manifest = prepare_run([str(empty)], str(self.profile_path), str(self.base / "empty-run"))
        self.assertEqual(manifest["items"], [])
        self.assertEqual(len(manifest["errors"]), 1)

        corrupt = self.base / "corrupt.png"
        corrupt.write_text("TEST DATA ONLY：这不是图片", encoding="utf-8")
        manifest = prepare_run([str(corrupt)], str(self.profile_path), str(self.base / "corrupt-run"))
        self.assertEqual(manifest["items"], [])
        self.assertIn("prepare", manifest["errors"][0]["stage"])

        unsupported = self.base / "notes.txt"
        unsupported.write_text("TEST DATA ONLY", encoding="utf-8")
        manifest = prepare_run([str(unsupported)], str(self.profile_path), str(self.base / "unsupported-run"))
        self.assertEqual(manifest["items"], [])
        self.assertIn("不支持的文件格式", manifest["errors"][0]["error"])

    def test_short_bmp_is_reencoded_to_real_png(self):
        bmp_dir = self.base / "bmp"
        bmp_dir.mkdir()
        create_test_image(bmp_dir / "short.bmp", size=(320, 240))
        run_dir = self.base / "bmp-run"
        manifest = prepare_run([str(bmp_dir)], str(self.profile_path), str(run_dir))
        item = manifest["items"][0]
        self.assertTrue(item["source_path"].endswith(".png"))
        from PIL import Image
        with Image.open(item["source_path"]) as converted:
            self.assertEqual(converted.format, "PNG")
        self.assertEqual(item["slices"], [item["source_path"]])
        write_layers(manifest, self.profile)
        summary = finalize_run(run_dir)
        self.assertEqual(summary["status"], "succeeded")

    def test_failed_rebuild_does_not_bundle_stale_excel(self):
        _, original = self.complete_run()
        self.assertTrue(Path(original["excel"]).is_file())
        prepare_run([str(self.inputs)], str(self.profile_path), str(self.run_dir), rebuild=True)
        summary = finalize_run(self.run_dir)
        self.assertEqual(summary["status"], "failed")
        self.assertIsNone(summary["excel"])
        self.assertFalse(Path(original["excel"]).exists())
        bundle = bundle_run(self.run_dir)
        with zipfile.ZipFile(bundle["zip"]) as archive:
            self.assertFalse(any(name.lower().endswith(".xlsx") for name in archive.namelist()))

    def test_same_filename_references_and_prompt_snapshot(self):
        nested = self.base / "nested"
        for name, color in (("a", (230, 240, 250)), ("b", (245, 235, 240))):
            (nested / name).mkdir(parents=True)
            create_test_image(nested / name / "same.png", color=color)
        run_dir = self.base / "reference-run"
        manifest = prepare_run([str(nested)], str(self.profile_path), str(run_dir))
        references = {item["original_reference"] for item in manifest["items"]}
        self.assertEqual(references, {"input-01/a/same.png", "input-01/b/same.png"})
        metadata = read_json(run_dir / "run_metadata.json")
        self.assertEqual(len(metadata["prompt_artifacts"]), 5)
        for artifact in metadata["prompt_artifacts"]:
            self.assertEqual(len(artifact["sha256"]), 64)
            self.assertTrue(Path(artifact["snapshot"]).is_file())

    def test_zero_redline_profile_complete_workflow(self):
        zero_profile = make_profile("test-zero-redlines", rules=())
        zero_path = self.base / "zero.json"
        zero_path.write_text(json.dumps(zero_profile, ensure_ascii=False), encoding="utf-8")
        run_dir = self.base / "zero-run"
        manifest = prepare_run([str(self.inputs / "agent.png")], str(zero_path), str(run_dir))
        write_layers(manifest, zero_profile)
        summary = finalize_run(run_dir)
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["excel_rows"], 1)
        self.assertEqual(summary["excel_validation"]["scores"], [4])

    def test_excel_formula_injection_is_neutralized(self):
        run_dir = self.base / "formula-run"
        manifest = prepare_run([str(self.inputs / "agent.png")], str(self.profile_path), str(run_dir))
        write_layers(manifest, self.profile)
        item = manifest["items"][0]
        ocr_path = Path(item["ocr_path"])
        ocr_path.write_text(ocr_path.read_text(encoding="utf-8").replace("示例科技公司", "=1+1"), encoding="utf-8")
        jd_path = Path(item["jd_path"])
        jd = read_json(jd_path)
        jd["company_name"] = "=1+1"
        jd["field_evidence"]["company_name"] = ["=1+1"]
        jd_path.write_text(json.dumps(jd, ensure_ascii=False), encoding="utf-8")
        summary = finalize_run(run_dir)
        self.assertEqual(summary["status"], "succeeded")
        from openpyxl import load_workbook
        workbook = load_workbook(summary["excel"])
        try:
            cell = workbook["岗位汇总"]["D2"]
            self.assertEqual(cell.data_type, "s")
            self.assertEqual(cell.value, "'=1+1")
        finally:
            workbook.close()
        self.assertIn("=1+1", ocr_path.read_text(encoding="utf-8"))
        self.assertNotIn("'=1+1", ocr_path.read_text(encoding="utf-8"))

    def test_invalid_layer_fails_without_fake_excel(self):
        manifest = prepare_run([str(self.inputs)], str(self.profile_path), str(self.base / "invalid-run"))
        run_dir = self.base / "invalid-run"
        write_layers(manifest, self.profile)
        for item in manifest["items"]:
            if item["status"] != "duplicate":
                Path(item["assessment_path"]).write_text("not-json", encoding="utf-8")
                break
        summary = finalize_run(run_dir)
        self.assertGreaterEqual(summary["failed"], 1)
        self.assertEqual(summary["status"], "partial")


if __name__ == "__main__":
    unittest.main()

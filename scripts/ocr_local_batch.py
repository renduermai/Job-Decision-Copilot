"""Deterministic, opt-in OCR batch executor. No native Agent tool calls.

Dependency is deliberately optional. The original CodeArts multimodal OCR workflow
stays available because a different OCR model may change recognition accuracy.
"""
import json
import tempfile
from pathlib import Path

from core import claim_ocr, fail_ocr, ingest_batch, read_json, status_run


def text_from_rapidocr(result):
    """RapidOCR normal det+rec output exposes .txts. Preserve returned line text."""
    lines = getattr(result, "txts", None)
    if lines is None:
        raise ValueError("本地OCR没有返回txts文本行")
    if not isinstance(lines, (tuple, list)):
        raise ValueError("本地OCR结果的txts类型不正确")
    return "\n".join(str(x) for x in lines if isinstance(x, str) and x.strip()).strip()


def make_rapidocr_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "本地OCR依赖缺失，先在隔离环境执行：python -m pip install -r requirements-local-ocr.txt；"
            "缺少依赖时不自动回退到原生analyzeImage，以免重新触发循环。"
        ) from exc
    return RapidOCR()


def run_local_batch(run_dir, *, engine=None, max_items=None):
    """Single process owns all OCR calls; fail closed on already claimed items.

    If crash occurs after claim but before ingest, state remains inflight and no
    automatic replay occurs. A human may recover OCR text from existing logs.
    """
    root = Path(run_dir).expanduser().resolve()
    manifest = read_json(root / "manifest.json")
    candidates = []
    for item in manifest["items"]:
        if item.get("status") == "duplicate":
            continue
        ocr = Path(item["ocr_path"])
        if ocr.exists() and ocr.read_text(encoding="utf-8").strip():
            continue
        candidates.append(item)
    if max_items is not None:
        if max_items < 1:
            raise ValueError("--max-items 只能是正整数")
        candidates = candidates[:max_items]
    if engine is None and candidates:
        engine = make_rapidocr_engine()  # preflight BEFORE claiming anything
    report = {"mode": "local-rapidocr", "total": len(manifest["items"]),
              "selected": len(candidates), "ok": [], "failed": [], "blocked": []}
    for item in candidates:
        item_id = item["item_id"]
        try:
            claim = claim_ocr(root, item_id)
        except ValueError as exc:
            report["blocked"].append({"item_id": item_id, "reason": str(exc)})
            continue
        try:
            pieces = []
            # Each approved strategy is claimed ONCE. No identical retry.
            for image_path in claim["source_paths"]:
                pieces.append(text_from_rapidocr(engine(str(image_path))))
            ocr_text = "\n".join(s for s in pieces if s).strip()
            if not ocr_text:
                raise ValueError("本地OCR输出为空")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                             prefix="ocr-", dir=root, delete=False) as f:
                json.dump({"results": [{"item_id": item_id, "ocr_text": ocr_text}]}, f, ensure_ascii=False)
                temp_path = Path(f.name)
            try:
                result = ingest_batch(root, temp_path)
            finally:
                temp_path.unlink(missing_ok=True)
            if result["accepted"] != 1:
                raise ValueError("OCR结果写入失败：" + str(result["results"]))
            report["ok"].append(item_id)
        except Exception as exc:
            # A claim exists, so record an explicit failure; don't retry same input.
            try:
                fail_ocr(root, item_id, f"{type(exc).__name__}: {exc}")
            except ValueError:
                pass  # may have been successfully saved before later error
            report["failed"].append({"item_id": item_id, "reason": f"{type(exc).__name__}: {exc}"})
    report["status"] = status_run(root)["counts"]
    return report

"""V1.4.5: derive safe, contextual output filenames without changing evaluation."""
from __future__ import annotations

import re
from pathlib import Path

_PREFIX = "AI岗位筛选_"
_DEFAULT = "本批岗位"
_MAX_KEYWORD_LENGTH = 48


def safe_keyword(value: str | None) -> str:
    """Return a portable filename part, not a path. Keep meaningful Chinese/English terms."""
    value = str(value or "").strip()
    # Remove a source-document extension if user supplied a filename as a keyword.
    value = re.sub(r"\.(?:xlsx|xlsm|csv|tsv|txt|md)$", "", value, flags=re.I)
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f\x7f]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")
    if value.startswith(_PREFIX):
        value = value[len(_PREFIX):].strip(" ._")
    elif value.startswith("AI岗位筛选"):
        value = value[len("AI岗位筛选"):].strip(" ._")
    value = value[:_MAX_KEYWORD_LENGTH].rstrip(" ._")
    return value or _DEFAULT


def _input_name(manifest: dict) -> str:
    """Choose the original uploaded document, else the image collection name."""
    documents = manifest.get("source_documents") or []
    if documents:
        names = [Path(str(p)).stem for p in documents]
        return names[0] if len(names) == 1 else f"{names[0]}等{len(names)}份文档"
    inputs = manifest.get("source_inputs") or manifest.get("parameters", {}).get("inputs") or []
    if inputs:
        paths = [Path(str(p)) for p in inputs]
        if len(paths) == 1:
            p = paths[0]
            return p.stem if p.suffix.lower() else p.name
        # Several images: use their common parent folder rather than IMG-001 etc.
        parents = {str(p.parent) for p in paths}
        if len(parents) == 1:
            return paths[0].parent.name
        return f"{len(paths)}份岗位"
    return _DEFAULT


def resolve_output_filename(manifest: dict, keyword: str | None = None) -> str:
    """Explicit per-finalize keyword > saved preference > source name."""
    selected = keyword or manifest.get("output_keyword") or _input_name(manifest)
    return f"{_PREFIX}{safe_keyword(selected)}.xlsx"


def is_generated_name(name: str) -> bool:
    return name == "AI岗位筛选汇总.xlsx" or (
        name.startswith(_PREFIX) and name.lower().endswith(".xlsx")
    )


def prior_generated_excel(run_dir: Path) -> Path | None:
    """Only consider the workbook recorded in this run's own summary."""
    import json
    run_dir = Path(run_dir).resolve()
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        p = json.loads(summary_path.read_text(encoding="utf-8")).get("excel")
        if not p:
            return None
        path = Path(p).resolve()
        if path.parent == run_dir and is_generated_name(path.name):
            return path
    except (OSError, ValueError, TypeError):
        pass
    return None

"""Export-only complete JD text. Never use this function to change scores or source facts."""
from __future__ import annotations

SKIP_TECHNICAL_KEYS = ("序号", "岗位id", "图片编号", "记录id", "source_id", "item_id")


def clean_original_jd_text(ocr_text: str) -> str:
    """Preserve captured JD field lines including department/date/URL; omit only technical row IDs."""
    cleaned=[]
    for line in str(ocr_text or "").splitlines():
        key=line.split("：",1)[0].split(":",1)[0].strip().casefold()
        if any(key == skip for skip in SKIP_TECHNICAL_KEYS) and ("：" in line or ":" in line):
            continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip()


def build_jd_full_text(jd: dict, ocr_text: str) -> str:
    """One human-readable field, with all original JD text retained even for unknown schema fields.

    OCR remains separately available for audit; this is a readable export, not fabricated JD facts.
    """
    header=[("公司",jd.get("company_name")),("岗位",jd.get("job_title")),
            ("地点",jd.get("location")),("薪资",jd.get("salary_range")),
            ("学历",jd.get("education")),("经验",jd.get("experience"))]
    line="｜".join(f"【{name}】{value}" for name,value in header if value and value not in {"未提及","未知"})
    raw=clean_original_jd_text(ocr_text)
    # If OCR input is available, retain it whole rather than lose department, publication date, URL, bonuses.
    if raw:
        return f"{line}\n【JD原文及相关信息】\n{raw}" if line else f"【JD原文及相关信息】\n{raw}"
    return f"{line}\n【岗位职责与任职要求】\n{jd.get('job_details','未提及')}"

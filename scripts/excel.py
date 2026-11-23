"""Excel 导出：复用原 Demo 的缩略图、长文本、原子保存和隐藏去重表逻辑。"""
import io
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from jd_render import build_jd_full_text
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import Image, ImageOps

_PREVIOUS_HEADERS = [
    "图片", "文字", "公司名", "薪资范围", "地点", "经验年限", "学历", "职位详情", "推荐依据", "推荐等级（1-5分）",
    "图片编号", "文件名", "原图引用", "岗位名称", "核心技能要求", "是否一票否决", "触发规则", "证据原文",
    "待确认条件", "模型归纳", "推断信息", "未知字段", "原始序号", "实际岗位类型", "岗位方向匹配", "软性降档依据", "画像能力门槛匹配", "跨章节技术证据", "卡点判断", "卡点如何解决",
]

# 将原第10列“推荐等级”移动到第3列，其他字段相对顺序保持不变。
_SCORE_HEADER = "推荐等级（1-5分）"
HEADERS = _PREVIOUS_HEADERS[:2] + [_SCORE_HEADER] + [h for h in _PREVIOUS_HEADERS[2:] if h != _SCORE_HEADER] + ["JD全文"]
_SCORE_COL = 3


def image_to_png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def safe_cell_text(value, sidecar_path):
    value = str(value)
    if len(value) <= 32767:
        return value
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(value, encoding="utf-8")
    suffix = f"\n\n[Excel单元格长度上限：完整文字已保存于 {sidecar_path.name}]"
    return value[:32767 - len(suffix)] + suffix


def neutralize_formula_text(value):
    """不可信 OCR/JD 文本始终以字符串写入，原始内容仍保留在分层文件。"""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip(" \t\r\n")
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def attach_thumbnail(ws, image_path, row):
    """重要性能优化：先缩小像素，再嵌入Excel，避免将40张原尺寸截图重复打进xlsx。"""
    with Image.open(image_path) as source:
        picture = ImageOps.exif_transpose(source)
        if picture.mode not in ("RGB", "RGBA"):
            picture = picture.convert("RGB")
        picture = picture.copy()
    picture.thumbnail((248, 288), Image.Resampling.LANCZOS)
    buffer = io.BytesIO(image_to_png_bytes(picture))
    buffer.seek(0)
    drawing = ExcelImage(buffer)
    ws.add_image(drawing, f"A{row}")
    return drawing.height


def setup_book():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "岗位汇总"
    sheet.append(HEADERS)
    sheet.freeze_panes = "D2"
    sheet.sheet_view.showGridLines = False
    widths = {"A": 35, "B": 62, "C": 22, "D": 25, "E": 19, "F": 22, "G": 17, "H": 13, "I": 70, "J": 62}
    for col, width in widths.items():
        sheet.column_dimensions[col].width = width
    for index in range(11, len(HEADERS) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 24
    # 仅新增列加宽，原28列的列宽与格式保持不变。
    sheet.column_dimensions[get_column_letter(len(HEADERS) - 2)].width = 64
    sheet.column_dimensions[get_column_letter(len(HEADERS) - 1)].width = 68
    sheet.column_dimensions[get_column_letter(len(HEADERS))].width = 86
    sheet.row_dimensions[1].height = 32
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="17324D")
        cell.font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    history = workbook.create_sheet("去重记录")
    history.append(["SHA256", "图片文件", "Excel行号", "添加时间"])
    history.sheet_state = "hidden"
    return workbook


def export_excel(output_path, records, run_dir, fast=False):
    if fast:
        return export_excel_fast(output_path, records, run_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = setup_book()
    sheet = workbook["岗位汇总"]
    history = workbook["去重记录"]
    try:
        for row, record in enumerate(records, 2):
            item, jd, decision = record["item"], record["jd"], record["decision"]
            ocr_text = record["ocr_text"]
            triggered = decision["triggered_rules"]
            evidence = [entry["evidence_quote"] for entry in triggered if entry["evidence_quote"]]
            if not evidence:
                evidence = [entry["evidence_quote"] for entry in decision["matched_points"]]
            values = [
                "",
                safe_cell_text(ocr_text, Path(run_dir) / "fulltext" / f"{item['item_id']}.ocr.txt"),
                jd["company_name"], jd["salary_range"], jd["location"], jd["experience"], jd["education"],
                safe_cell_text(jd["job_details"], Path(run_dir) / "fulltext" / f"{item['item_id']}.jd.txt"),
                decision["recommendation_reason"], decision["recommendation_score"],
                item["item_id"], item["source_file"], item["original_reference"], jd["job_title"],
                "；".join(jd["core_skills"]) if jd["core_skills"] else "未提及",
                "是" if decision["hard_reject"] else "否",
                "；".join(entry["rule_id"] for entry in triggered) or "无",
                "\n".join(evidence), "\n".join(decision["pending_conditions"]),
                jd["model_summary"], "\n".join(jd["inferences"]), "\n".join(jd["unknown_fields"]),
                item.get("input_index", int(item["item_id"].split("-")[-1])),
                jd["role_analysis"]["primary_role_family"],
                decision["role_fit"]["conclusion"],
                "；".join(x["reason"] + "（" + x["evidence_quote"] + "）" for x in decision["applied_caps"]),
                decision["capability_fit"]["conclusion"],
                "；".join(x["evidence_quote"] for x in jd["role_analysis"]["expectation_signals"]
                         if x["kind"] in {"algorithm_depth", "engineering_depth", "ownership_depth"}),
                safe_cell_text(decision.get("pain_point_analysis", {}).get("problem", "待分析：缺少卡点证据"),
                               Path(run_dir) / "fulltext" / f"{item['item_id']}.pain_point.txt"),
                safe_cell_text(decision.get("pain_point_analysis", {}).get("solution", "待核实：尚无可验证解决依据"),
                               Path(run_dir) / "fulltext" / f"{item['item_id']}.pain_solution.txt"),
                safe_cell_text(build_jd_full_text(jd, ocr_text),
                               Path(run_dir) / "fulltext" / f"{item['item_id']}.jd_full.txt"),
            ]
            # 保持既有字段的内容与相对顺序，仅把第10列推荐等级移至第3列。
            values = values[:2] + [values[9]] + values[2:9] + values[10:]
            if item.get("input_kind") == "text":
                values[0] = "无"
            for col, value in enumerate(values, 1):
                cell = sheet.cell(row, col, neutralize_formula_text(value))
                cell.alignment = Alignment(vertical="top", wrap_text=True, horizontal="center" if col in (3, 5, 6, 7, 8) else "left")
                cell.font = Font(name="微软雅黑", size=10, color="25364A")
                if row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F4F8FC")
            score_cell = sheet.cell(row, _SCORE_COL)
            score_cell.font = Font(name="微软雅黑", size=13, bold=True,
                                   color="B91C1C" if decision["recommendation_score"] <= 2 else "167847")
            if item.get("input_kind") == "text":
                sheet.row_dimensions[row].height = 82
            else:
                image_height = attach_thumbnail(sheet, item["source_path"], row)
                sheet.row_dimensions[row].height = min(405, max(170, image_height * .75 + 12))
            history.append([item["sha256"], neutralize_formula_text(item["source_file"]), row, datetime.now(timezone.utc).isoformat()])
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(records) + 1}"
        fd, tmp_name = tempfile.mkstemp(prefix="job_import_", suffix=".xlsx", dir=output_path.parent)
        os.close(fd)
        try:
            workbook.save(tmp_name)
            os.replace(tmp_name, output_path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
    finally:
        workbook.close()


def validate_excel(path, expected_rows, sort_mode="score", expected_images=None):
    workbook = load_workbook(path)
    try:
        sheet = workbook["岗位汇总"]
        if [sheet.cell(1, index).value for index in range(1, len(HEADERS) + 1)] != HEADERS:
            raise ValueError("Excel 基础表头顺序不符合契约")
        actual_headers = [sheet.cell(1, i).value for i in range(1, sheet.max_column+1)]
        if len(actual_headers) != len(set(actual_headers)):
            raise ValueError("Excel 新增列名称重复")
        if sheet.max_row - 1 != expected_rows:
            raise ValueError(f"Excel 行数不符：预期 {expected_rows}，实际 {sheet.max_row - 1}")
        formula_cells = [cell.coordinate for row in sheet.iter_rows() for cell in row if cell.data_type == "f"]
        if formula_cells:
            raise ValueError(f"Excel 含未中和的公式单元格：{formula_cells[:8]}")
        if sheet.freeze_panes != "D2":
            raise ValueError("Excel 未冻结含推荐等级的前三列")
        scores = [sheet.cell(row, _SCORE_COL).value for row in range(2, sheet.max_row + 1)]
        if sort_mode == "score" and scores != sorted(scores, reverse=True):
            raise ValueError("Excel 推荐等级未按降序排列")
        indices = [sheet.cell(row, 23).value for row in range(2, sheet.max_row + 1)]
        if sort_mode == "input" and indices != sorted(indices):
            raise ValueError("Excel 原始序号未保持输入顺序")
        if len(indices) != len(set(indices)):
            raise ValueError("Excel 原始序号存在重复")
        if expected_images is None:
            expected_images = expected_rows
        if len(sheet._images) != expected_images:
            raise ValueError(f"Excel 缩略图数量不符：预期 {expected_images}，实际 {len(sheet._images)}")
        if expected_images == 0 and any(sheet.cell(row,1).value != "无" for row in range(2,sheet.max_row+1)):
            raise ValueError("纯文本岗位的图片列必须全部填写“无”")
        if "JD全文" not in HEADERS or "卡点判断" not in HEADERS or "卡点如何解决" not in HEADERS:
            raise ValueError("Excel缺少三项必需的输出字段")
        if any(not sheet.cell(r,len(HEADERS)).value for r in range(2,sheet.max_row+1)):
            raise ValueError("JD全文存在空行")
        if "去重记录" not in workbook.sheetnames or workbook["去重记录"].sheet_state != "hidden":
            raise ValueError("Excel 缺少隐藏去重表")
        # Do not dump 26k individual scores and indices into the model conversation.
        short = expected_rows > 1000
        return {"rows": expected_rows, "images": len(sheet._images),
                "scores": scores[:30] if short else scores,
                "input_indices": indices[:30] if short else indices,
                "sample_only": short,
                "score_distribution": dict(__import__('collections').Counter(scores)),
                "sort_mode": sort_mode, "bytes": Path(path).stat().st_size}
    finally:
        workbook.close()


def export_excel_fast(output_path, records, run_dir):
    """Low-memory text-only large-batch export; no pictures or expensive per-cell striped fills.

    Uses the SAME HEADERS and column construction as normal export (not a score change).
    """
    output_path=Path(output_path)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    book=Workbook(write_only=True)
    sheet=book.create_sheet("岗位汇总")
    sheet.freeze_panes="D2"
    sheet.sheet_view.showGridLines=False
    sheet.append(HEADERS)
    history=book.create_sheet("去重记录")
    history.append(["SHA256", "图片文件", "Excel行号", "添加时间"])
    history.sheet_state="hidden"
    for row, record in enumerate(records,2):
        item,jd,d=record["item"],record["jd"],record["decision"]
        if item.get("input_kind")!="text":
            raise ValueError("fast-export仅可用于纯文本，无图像缩略图")
        triggered=d["triggered_rules"]
        evidence=[e["evidence_quote"] for e in triggered if e.get("evidence_quote")]
        if not evidence:
            evidence=[e["evidence_quote"] for e in d["matched_points"] if e.get("evidence_quote")]
        oc=record["ocr_text"]
        pt=d.get("pain_point_analysis") or {}
        values=["无",safe_cell_text(oc,Path(run_dir)/"fulltext"/f"{item['item_id']}.ocr.txt"),
                jd["company_name"],jd["salary_range"],jd["location"],jd["experience"],jd["education"],
                safe_cell_text(jd["job_details"],Path(run_dir)/"fulltext"/f"{item['item_id']}.jd.txt"),
                d["recommendation_reason"],d["recommendation_score"],item["item_id"],item["source_file"],item["original_reference"],
                jd["job_title"],"；".join(jd["core_skills"]) if jd["core_skills"] else "未提及",
                "是" if d["hard_reject"] else "否","；".join(e["rule_id"] for e in triggered) or "无","\n".join(evidence),
                "\n".join(d["pending_conditions"]),jd["model_summary"],"\n".join(jd["inferences"]),"\n".join(jd["unknown_fields"]),
                item.get("input_index",int(item["item_id"].split("-")[-1])), jd["role_analysis"]["primary_role_family"],
                d["role_fit"]["conclusion"],"；".join(x["reason"]+"（"+x["evidence_quote"]+"）" for x in d["applied_caps"]),
                d["capability_fit"]["conclusion"],"；".join(x["evidence_quote"] for x in jd["role_analysis"]["expectation_signals"]
                          if x["kind"] in {"algorithm_depth","engineering_depth","ownership_depth"}),
                safe_cell_text(pt.get("problem","待分析：缺少卡点证据"),Path(run_dir)/"fulltext"/f"{item['item_id']}.pain_point.txt"),
                safe_cell_text(pt.get("solution","待核实：尚无可验证解决依据"),Path(run_dir)/"fulltext"/f"{item['item_id']}.pain_solution.txt"),
                safe_cell_text(build_jd_full_text(jd,oc),Path(run_dir)/"fulltext"/f"{item['item_id']}.jd_full.txt")]
        values=values[:2]+[values[9]]+values[2:9]+values[10:]
        assert len(values)==len(HEADERS)
        sheet.append([neutralize_formula_text(v) for v in values])
        history.append([item["sha256"], neutralize_formula_text(item["source_file"]),row,datetime.now(timezone.utc).isoformat()])
    fd,tmp=tempfile.mkstemp(prefix="job_stream_",suffix=".xlsx",dir=output_path.parent)
    os.close(fd)
    try:
        book.save(tmp)
        os.replace(tmp,output_path)
    finally:
        if os.path.exists(tmp):os.remove(tmp)

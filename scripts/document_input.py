"""文本 CSV/Excel/TSV/TXT 的岗位导入与含图 Excel 的原有截图路由。

本模块只负责将原始行转成 OCR 等价文本；JD 与评分仍使用相同 Schema/规则。
"""
from __future__ import annotations
import csv
import io
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
import uuid
from PIL import Image, ImageOps
from openpyxl import load_workbook

DOCUMENT_EXTS = {".csv", ".tsv", ".xlsx", ".xlsm", ".txt", ".md"}


def is_document_source(paths):
    """只在输入全部是文档时改变原有图片工作流；混合类型明确报错。"""
    items = [Path(p).expanduser() for p in paths]
    flags = [p.is_file() and p.suffix.lower() in DOCUMENT_EXTS for p in items]
    if any(flags) and not all(flags):
        raise ValueError("图片与文字文档混合输入请分批运行，避免岗位与原序号错位")
    return bool(flags) and all(flags)


def _nonempty(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and value == int(value):
        return str(int(value))
    return str(value).strip()


def _row_text(headers, cells):
    lines=[]
    for i, val in enumerate(cells):
        v = _nonempty(val)
        if not v:
            continue
        h = _nonempty(headers[i]) if i < len(headers) else ""
        if h.casefold() in {"图片", "image", "截图"} and v in {"无", "-", "无图片"}:
            continue
        lines.append(f"{h or '字段'+str(i+1)}：{v}")
    return "\n".join(lines)


def _read_csv(path):
    content=None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            content=path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            pass
    if content is None:
        raise ValueError(f"CSV 编码暂不支持：{path.name}")
    sample=content[:4096]
    try:
        dialect=csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect=csv.excel_tab if path.suffix.lower()==".tsv" else csv.excel
    all_rows=list(csv.reader(io.StringIO(content), dialect))
    if not all_rows:
        return []
    headers=all_rows[0]
    return [(i, _row_text(headers, row)) for i,row in enumerate(all_rows[1:],2) if _row_text(headers,row)]


def _get_workbook(path):
    return load_workbook(path, data_only=True, read_only=False)


def _read_xlsx(path):
    # Inspect for embedded images once; after confirming text-only stream rows in read-only mode.
    if inspect_document(path)["embedded_images"]:
        return None
    book = load_workbook(path, data_only=True, read_only=True)
    try:
        rows=[]
        for sheet in book.worksheets:
            iterator=sheet.iter_rows(values_only=True)
            headers=next(iterator,None)
            if not headers:
                continue
            for row_index,cells in enumerate(iterator,2):
                text=_row_text(headers,cells)
                if text:
                    rows.append((sheet.title,row_index,text))
        return rows
    finally:
        book.close()


def inspect_document(path):
    path=Path(path)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        book=_get_workbook(path)
        try:
            count=sum(len(ws._images) for ws in book.worksheets)
        finally:
            book.close()
        return {"type":"spreadsheet_with_images" if count else "text_only", "embedded_images":count}
    if path.suffix.lower() in DOCUMENT_EXTS:
        return {"type":"text_only", "embedded_images":0}
    raise ValueError(f"暂不支持的文档格式：{path.suffix}")


def _extract_images(path, dest):
    book=_get_workbook(path)
    try:
        entries=[]
        for sheet_idx,ws in enumerate(book.worksheets):
            for i,embedded in enumerate(ws._images):
                anchor=embedded.anchor
                row=(anchor._from.row+1) if hasattr(anchor,"_from") else i+2
                col=(anchor._from.col+1) if hasattr(anchor,"_from") else 1
                entries.append((sheet_idx,row,col,i,ws.title,embedded))
        entries.sort(key=lambda x:(x[0],x[1],x[2],x[3]))
        dest.mkdir(parents=True,exist_ok=True)
        output=[]
        for idx,(_,row,col,_,sheet,emb) in enumerate(entries,1):
            target=dest/f"EMB-{idx:04d}.png"
            with Image.open(io.BytesIO(emb._data())) as source:
                img=ImageOps.exif_transpose(source)
                if img.mode not in ("RGB","RGBA"):
                    img=img.convert("RGB")
                img.save(target,format="PNG")
            output.append((target,f"{path.name}#{sheet}!R{row}C{col}"))
        return output
    finally:
        book.close()


def prepare_documents(inputs,profile_path,output_dir,reuse_from=None,rebuild=False,
                      order_file=None,ocr_model=None,semantic_model=None):
    """同一输入只走一条路径：有图 -> 现有 prepare_run；纯文本 -> 无 OCR 文本导入。"""
    from core import (prepare_run, read_json, write_json, write_text, sha256_json, sha256_file,
                      validate_profile, artifact_paths, save_prompt_snapshot, utc_now,
                      SKILL_VERSION, PROMPT_VERSION, JD_CONTRACT_VERSION, ASSESSMENT_CONTRACT_VERSION,
                      RULE_ENGINE_VERSION, RUN_SUBDIRS, runtime_versions, copy_old_artifact, load_previous)
    from core import clear_stale_artifacts
    if order_file:
        raise ValueError("文本文档以工作表/CSV原行顺序编号，不需要 --order-file")
    paths=[Path(p).expanduser().resolve() for p in inputs]
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in DOCUMENT_EXTS:
            raise ValueError(f"不支持的文档：{path}")
    probes=[inspect_document(path) for path in paths]
    image_files=[(path,info) for path,info in zip(paths,probes) if info["embedded_images"]]
    if image_files:
        if len(paths)!=1:
            raise ValueError("含图片Excel请单独作为一批处理；不与其他文档混合")
        extraction=_extract_images(paths[0],Path(output_dir)/"_extracted_images")
        if not extraction:
            raise ValueError("图片提取为空")
        manifest=prepare_run([str(t) for t,_ in extraction],profile_path,output_dir,reuse_from,rebuild,
                             True,ocr_model or "CodeArts analyzeImage",semantic_model or "CodeArts Agent")
        for item,(_,origin) in zip(manifest["items"],extraction):
            item["original_reference"]=origin
            item["input_kind"]="image"
        manifest["input_mode"]="embedded_images"
        manifest["source_documents"]=[str(paths[0])]
        write_json(Path(output_dir)/"manifest.json",manifest)
        return manifest
    root=Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True,exist_ok=True)
    for folder in RUN_SUBDIRS:
        (root/folder).mkdir(exist_ok=True)
    from output_naming import prior_generated_excel
    old_excel = prior_generated_excel(root)
    if old_excel and old_excel.is_file():
        old_excel.unlink()
    for filename in ("AI岗位筛选汇总.xlsx","summary.json"):
        (root/filename).unlink(missing_ok=True)
    profile=read_json(profile_path)
    validate_profile(profile)
    profile_hash=sha256_json(profile)
    profile_target=root/"profile.json"
    if Path(profile_path).resolve()!=profile_target:
        shutil.copyfile(profile_path,profile_target)
    previous=[]
    if not rebuild:
        for candidate in (root,Path(reuse_from).resolve() if reuse_from else None):
            if candidate:
                old=load_previous(candidate)
                if old:
                    previous.append((candidate,old))
    lookup={}
    for old_dir,old_manifest in previous:
        if old_manifest.get("input_mode")!="text_only":
            continue
        for oi in old_manifest.get("items",[]):
            lookup.setdefault(oi["sha256"],(old_dir,old_manifest,oi))
    rows=[]
    for p in paths:
        if p.suffix.lower() in {".csv", ".tsv"}:
            rows.extend((p,f"{p.name}!R{r}",text) for r,text in _read_csv(p))
        elif p.suffix.lower() in {".xlsx", ".xlsm"}:
            xrows=_read_xlsx(p)
            if xrows is None:
                raise RuntimeError("含图Excel被意外路由到纯文本入口")
            rows.extend((p,f"{p.name}#{sheet}!R{r}",text) for sheet,r,text in xrows)
        else:
            txt=p.read_text(encoding="utf-8-sig")
            if txt.strip():
                rows.append((p,p.name,txt))
    if not rows:
        raise ValueError("文档没有可用的岗位文本行")
    items=[]
    first_by_digest={}
    for index,(path,ref,text) in enumerate(rows,1):
        item_id=f"IMG-{index:03d}" # 保持已有 JSON 合同和原始编号契约
        digest=__import__('hashlib').sha256(text.encode('utf-8')).hexdigest()
        item={"item_id":item_id,"input_index":index,"input_kind":"text",
              "source_file":ref,"source_path":str(path),"original_reference":ref,
              "sha256":digest,**artifact_paths(root,item_id),"status":"needs_jd",
              "duplicate_of":None,"slices":[],"image_strategy":"no_image"}
        write_text(item["ocr_path"],text)
        # Deduplicate identical text within this batch BEFORE expensive model scoring.
        # The duplicate still occupies its own source row, and finalize restores it.
        original_id=first_by_digest.get(digest)
        if original_id is not None:
            item["duplicate_of"]=original_id
            item["status"]="duplicate"
            clear_stale_artifacts(item, ("ocr",))
            items.append(item)
            continue
        first_by_digest[digest]=item_id
        old=lookup.get(digest)
        if old:
            old_dir,om,oi=old
            if om.get("jd_contract_version")==JD_CONTRACT_VERSION and copy_old_artifact(old_dir,oi,"jd_path","jd",".json",item["jd_path"]):
                jd=read_json(item["jd_path"])
                jd.update({"source_file":ref,"ocr_text_ref":Path(item["ocr_path"]).name})
                write_json(item["jd_path"],jd)
                if (om.get("profile_sha256")==profile_hash and
                    om.get("assessment_contract_version")==ASSESSMENT_CONTRACT_VERSION and
                    copy_old_artifact(old_dir,oi,"assessment_path","assessments",".json",item["assessment_path"])):
                    item["status"]="ready"
                else:
                    item["status"]="needs_assessment"
        clear_stale_artifacts(item,{"needs_jd":("ocr",),"needs_assessment":("ocr","jd"),
                                    "ready":("ocr","jd","assessment","decision")}[item["status"]])
        items.append(item)
    prompt_artifacts=save_prompt_snapshot(root)
    manifest={"manifest_version":"1.0","run_id":datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8],
              "skill_version":SKILL_VERSION,"created_at":utc_now(),"profile_path":str(profile_target),
              "profile_id":profile["profile_id"],"profile_version":profile["profile_version"],
              "profile_sha256":profile_hash,"rule_version":profile["rule_version"],
              "jd_contract_version":JD_CONTRACT_VERSION,"assessment_contract_version":ASSESSMENT_CONTRACT_VERSION,
              "prompt_version":PROMPT_VERSION,"prompt_artifacts":prompt_artifacts,"items":items,"errors":[],
              "input_mode":"text_only","source_documents":[str(p) for p in paths],
              "model":{"ocr":"文本原文直接导入（无图片OCR）","semantic":semantic_model or "CodeArts Agent"},
              "parameters":{"recursive":False,"rebuild":rebuild,"reuse_from":reuse_from,"order_file":None}}
    write_json(root/"manifest.json",manifest)
    write_json(root/"run_metadata.json",{"skill_version":SKILL_VERSION,"run_id":manifest["run_id"],
               "status":"prepared","created_at":manifest["created_at"],"model":manifest["model"],
               "prompt_version":PROMPT_VERSION,"prompt_artifacts":prompt_artifacts,
               "rule_version":profile["rule_version"],"rule_engine_version":RULE_ENGINE_VERSION,
               "parameters":{"inputs":[str(p) for p in paths],"output":str(root),**manifest["parameters"]},
               "runtime":runtime_versions()})
    write_json(root/"errors.json",[])
    return manifest

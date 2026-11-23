"""V1.4.6 large batch utilities. Only read existing evidence; NEVER alter semantic scores.

calibrate: deterministic representative preflight sample BEFORE full assessment.
merge: merge already-finalized runs without rerunning any LLM/score decisions.
batch_finalize: one process for multiple independent run folders.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill, Alignment

from jd_render import build_jd_full_text
from excel import neutralize_formula_text
from core import read_json, finalize_run, check_run
from document_input import _read_csv, _read_xlsx

ROLE_PATTERNS = {
    "产品": re.compile(r"产品经理|产品设计|产品规划|产品需求|product manager", re.I),
    "运营": re.compile(r"运营|客户成功|售前|销售|营销策略|活动策划", re.I),
    "算法": re.compile(r"预训练|深度学习|模型微调|强化学习|算法工程|模型训练", re.I),
    "数据": re.compile(r"BI|数据分析|报表|数仓|数据挖掘|数据科学", re.I),
    "工程": re.compile(r"开发工程|软件工程|Agent|RAG|后端|前端|分布式|编程", re.I),
}


def calibrate_documents(inputs, output, per_category=50):
    """Pick *representative* examples, not gold labels; user must calibrate manually."""
    if per_category < 1 or per_category > 300:
        raise ValueError("--per-category 必须为1–300")
    rows=[]
    for inp in inputs:
        p=Path(inp).expanduser().resolve()
        if p.suffix.lower() in {".csv", ".tsv"}:
            iterator=((f"R{r}", t) for r,t in _read_csv(p))
        elif p.suffix.lower() in {".xlsx", ".xlsm"}:
            data=_read_xlsx(p)
            if data is None:
                raise ValueError("calibrate 仅支持纯文本 Excel，不处理带内嵌图片的文件")
            iterator=((f"{sheet}!R{r}", t) for sheet,r,t in data)
        else:
            raise ValueError(f"不支持的校准文件：{p.name}")
        for ref,text in iterator:
            # Multi-label example selection for boundary coverage, but one primary bucket per row.
            found=[name for name,regex in ROLE_PATTERNS.items() if regex.search(text)]
            category=found[0] if found else "其他"
            key=hashlib.sha256(text.encode("utf-8")).hexdigest()
            rows.append({"source":str(p),"reference":ref,"category":category,
                         "all_categories":found,"sha256":key,"jd_text":text})
    buckets={k:[] for k in [*ROLE_PATTERNS,"其他"]}
    # Hash sorting is deterministic: same documents -> same samples, irrespective of input ordering.
    for row in rows:
        buckets[row["category"]].append(row)
    chosen=[]
    for name,entries in buckets.items():
        chosen+=sorted(entries,key=lambda r:(r["sha256"],r["source"],r["reference"]))[:per_category]
    out=Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8") as f:
        for row in chosen:
            f.write(json.dumps(row,ensure_ascii=False)+"\n")
    return {"status":"sampled","total_source_rows":len(rows),"sample_rows":len(chosen),
            "by_category":{c:len(buckets[c]) for c in buckets},"output":str(out),
            "next":"人工检查类目、Top排序与红线边界；冻结用户画像和规则后再全量，不得将此文件自动当作金标准"}

MERGE_HEADERS=["来源批次", "原始序号", "推荐等级（1-5分）", "公司", "岗位名称", "地点", "薪资范围",
               "经验年限", "学历", "实际岗位类型", "是否一票否决", "推荐依据", "JD全文",
               "卡点判断", "卡点如何解决", "原始来源", "职位详情", "核心技能要求", "记录键"]


def _merge_record(run_dir, manifest, item, lookup):
    """Read one settled row; duplicate rows reuse root decision but retain their own source ref."""
    source=item
    depth=0
    while source.get("status")=="duplicate":
        source=lookup[source["duplicate_of"]]
        depth+=1
        if depth>10:
            raise ValueError("重复引用链过长或循环")
    jd=read_json(source["jd_path"])
    d=read_json(source["decision_path"])
    text=Path(source["ocr_path"]).read_text(encoding="utf-8")
    # Keep all source rows in index; do not silently drop repeats from original 19 sheets.
    pt=d.get("pain_point_analysis") or {}
    full=build_jd_full_text(jd,text)
    identity="|".join(re.sub(r"\s+","",str(v).casefold()) for v in
                      (jd.get("company_name"),jd.get("job_title"),jd.get("location"),jd.get("job_details")))
    key=hashlib.sha256(identity.encode("utf-8")).hexdigest()
    uid=f"{run_dir.name}:{item['item_id']}"
    return [run_dir.name,item.get("input_index"),d["recommendation_score"],jd.get("company_name"),
            jd.get("job_title"),jd.get("location"),jd.get("salary_range"),jd.get("experience"),
            jd.get("education"),(jd.get("role_analysis") or {}).get("primary_role_family"),
            "是" if d.get("hard_reject") else "否",d.get("recommendation_reason") or "待核实",
            full,pt.get("problem") or "待分析：缺少可追溯JD证据",
            pt.get("solution") or "待核实：暂无岗位期待或个人经历证据",
            item.get("original_reference") or item.get("source_file"),
            jd.get("job_details"),"、".join(jd.get("core_skills") or []),uid],key


def _cell_text(value):
    if isinstance(value, (int,float)) and not isinstance(value,bool):
        return value
    text=str(value if value is not None else "")
    # Excel cell limit; indicate truncation rather than silently lose data.
    if len(text)>32767:
        text=text[:32700]+"\n[Excel单元格上限，剩余请查看对应运行目录OCR/JD原文]"
    return neutralize_formula_text(text)


def _ws(book,name,headers):
    sheet=book.create_sheet(name)
    sheet.freeze_panes="D2"
    sheet.sheet_view.showGridLines=False
    cells=[]
    for h in headers:
        c=WriteOnlyCell(sheet,value=h)
        c.font=Font(name="微软雅黑",bold=True,color="FFFFFF",size=10)
        c.fill=PatternFill("solid",fgColor="17324D")
        c.alignment=Alignment(wrap_text=True,vertical="center")
        cells.append(c)
    sheet.append(cells)
    return sheet


def merge_runs(runs, output, min_score=4, allow_mixed_profiles=False):
    """One-pass, write-only full index and sorted high score, both ALWAYS include JD+painpoint columns."""
    if not 1<=min_score<=5:
        raise ValueError("--min-score 必须为1–5")
    paths=[Path(r).expanduser().resolve() for r in runs]
    if len(paths)!=len(set(paths)):
        raise ValueError("--runs 重复传入了相同的运行目录")
    book=Workbook(write_only=True)
    all_ws=_ws(book,"全量整合表",MERGE_HEADERS)
    high_ws=_ws(book,"高分岗位",MERGE_HEADERS)
    company_ws=_ws(book,"公司分布",["公司","唯一岗位数","高分唯一岗位数"])
    seen=set(); company=Counter(); company_high=Counter(); high=[]
    total=0; errors=[]; profile_hashes=set(); unique_count=0
    out=Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True,exist_ok=True)
    cache_path=out.with_suffix(".merge_data.jsonl")
    cache_file=cache_path.open("w",encoding="utf-8")
    for run in paths:
        manifest=read_json(run/"manifest.json")
        profile_hashes.add(manifest.get("profile_sha256"))
        if len(profile_hashes)>1 and not allow_mixed_profiles:
            raise ValueError("运行目录属于不同用户画像。若确需混合，显式传 --allow-mixed-profiles，注意评分不可直接比较")
        summary_path=run/"summary.json"
        if not summary_path.exists():
            raise ValueError(f"{run} 未完成 finalize，不能直接合并")
        summary=read_json(summary_path)
        if summary.get("status")=="failed":
            raise ValueError(f"{run} 为失败运行，不应静默合并")
        lookup={i["item_id"]: i for i in manifest["items"]}
        for item in manifest["items"]:
            try:
                row,key=_merge_record(run,manifest,item,lookup)
                cache_file.write(json.dumps({"row":row,"dedupe_key":key},ensure_ascii=False)+"\n")
                all_ws.append([_cell_text(v) for v in row]);total+=1
                if key in seen:
                    continue
                seen.add(key);unique_count+=1
                org=str(row[3] or "未提及")
                company[org]+=1
                if int(row[2])>=min_score:
                    high.append((row,key))
                    company_high[org]+=1
            except (FileNotFoundError, KeyError, ValueError, TypeError) as e:
                errors.append({"run":str(run),"item_id":item.get("item_id"),"error":str(e)})
    cache_file.close()
    for row,_ in sorted(high,key=lambda kv:(-int(kv[0][2]),str(kv[0][3]),str(kv[0][4]))):
        high_ws.append([_cell_text(v) for v in row])
    for org,n in company.most_common():
        company_ws.append([_cell_text(org),n,company_high[org]])
    fd,tmp=tempfile.mkstemp(prefix="job_merge_",suffix=".xlsx",dir=out.parent)
    os.close(fd)
    try:
        book.save(tmp)
        os.replace(tmp,out)
    finally:
        if os.path.exists(tmp):os.remove(tmp)
    # Error report alongside workbook: missing data must not be hidden.
    errfile=out.with_suffix(".merge_errors.json")
    errfile.write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"status":"partial" if errors else "succeeded", "source_runs":len(paths),
            "all_rows":total,"unique_jobs":unique_count,"duplicates":total-unique_count,
            "high_unique_jobs":len(high),"output":str(out),"errors":len(errors),
            "error_file":str(errfile),"cache_jsonl":str(cache_path),"formula_free":True,
            "notes":"全量表和高分表均有JD全文、卡点判断和解法；重复仅在高分统计中去重；与原始运行评分完全一致"}


def batch_finalize(runs, workers=1, sort_mode="input"):
    if workers not in (1,2):
        raise ValueError("workers仅支持1或2")
    def one(r):
        try:
            summary=finalize_run(r,sort_mode=sort_mode)
            checked=check_run(r)
            return {"run":str(r),"status":checked["status"],"rows":summary["excel_rows"],"excel":summary["excel"]}
        except Exception as e:
            return {"run":str(r),"status":"failed","error":f"{type(e).__name__}: {e}"}
    if workers==1:
        details=[one(r) for r in runs]
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            details=list(pool.map(one,runs))
    return {"status":"passed" if all(v["status"]=="passed" for v in details) else "partial",
            "run_count":len(details),"details":details}


def merge_from_cache(cache_jsonl, output, min_score=4):
    """Re-render a cross-company summary from the previous immutable JSONL snapshot.

    DOES NOT revisit 19 run directories or recompute scores. Source changes after caching
    are deliberately not reflected; regenerate cache explicitly when source changes.
    """
    if not 1<=min_score<=5:
        raise ValueError("--min-score 必须为1–5")
    p=Path(cache_jsonl).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(p)
    book=Workbook(write_only=True)
    all_ws=_ws(book,"全量整合表",MERGE_HEADERS)
    high_ws=_ws(book,"高分岗位",MERGE_HEADERS)
    company_ws=_ws(book,"公司分布",["公司","唯一岗位数","高分唯一岗位数"])
    seen=set(); org_counter=Counter();org_high=Counter(); highs=[];total=0
    with p.open(encoding="utf-8") as f:
        for line_no,line in enumerate(f,1):
            if not line.strip():continue
            obj=json.loads(line)
            row=obj.get("row");key=obj.get("dedupe_key")
            if not isinstance(row,list) or len(row)!=len(MERGE_HEADERS) or not isinstance(key,str):
                raise ValueError(f"JSONL第{line_no}行格式与V1.4.6汇总契约不符")
            all_ws.append([_cell_text(v) for v in row]);total+=1
            if key in seen:continue
            seen.add(key)
            org=str(row[3] or "未提及");org_counter[org]+=1
            if int(row[2])>=min_score:
                highs.append(row);org_high[org]+=1
    for row in sorted(highs,key=lambda r:(-int(r[2]),str(r[3]),str(r[4]))):
        high_ws.append([_cell_text(v) for v in row])
    for org,n in org_counter.most_common():
        company_ws.append([_cell_text(org),n,org_high[org]])
    out=Path(output).expanduser().resolve();out.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix="cached_merge_",suffix=".xlsx",dir=out.parent);os.close(fd)
    try:
        book.save(tmp);os.replace(tmp,out)
    finally:
        if os.path.exists(tmp):os.remove(tmp)
    return {"status":"succeeded","all_rows":total,"unique_jobs":len(seen),
            "high_unique_jobs":len(highs),"output":str(out),"cache_jsonl":str(p),
            "notes":"从已有JSONL快照重建；原始评分与JD不会重新读取或修改，缓存生成后发生的修改不会自动同步"}


def split_run(run_dir, output_root, also_merge=False):
    """Export original per-document workbooks from one cross-file-deduplicated TEXT run.

    This avoids scoring overlapping CSVs twice. It reads ONLY settled decisions; it does
    not call any model or the original scoring function. It preserves original row refs.
    """
    from excel import export_excel, validate_excel
    from output_naming import safe_keyword
    run=Path(run_dir).expanduser().resolve()
    manifest=read_json(run/"manifest.json")
    if manifest.get("input_mode")!="text_only":
        raise ValueError("split目前仅支持纯文本CSV/Excel的多文件运行")
    if not (run/"summary.json").exists():
        raise ValueError("请先finalize全量运行后再split，不可重跑评分")
    output_root=Path(output_root).expanduser().resolve();output_root.mkdir(parents=True,exist_ok=True)
    lookup={item["item_id"]:item for item in manifest["items"]}
    groups={str(p):[] for p in manifest.get("source_documents",[])}
    errors=[]
    for item in manifest["items"]:
        source=item
        try:
            for _ in range(10):
                if source.get("status")!="duplicate":break
                source=lookup[source["duplicate_of"]]
            else:
                raise ValueError("重复引用链过长")
            jd=read_json(source["jd_path"])
            decision=read_json(source["decision_path"])
            ocr=Path(source["ocr_path"]).read_text(encoding="utf-8")
            doc=str(item["source_path"])
            groups.setdefault(doc,[]).append({"item":item,"jd":jd,"decision":decision,"ocr_text":ocr})
        except (ValueError,KeyError,FileNotFoundError) as e:
            errors.append({"item_id":item.get("item_id"),"error":str(e)})
    outputs=[];names=set()
    for path,records in groups.items():
        if not records:continue
        name=safe_keyword(Path(path).stem)
        unique_name=name
        counter=2
        while unique_name in names:
            unique_name=f"{name}_{counter}";counter+=1
        names.add(unique_name)
        target=output_root/f"AI岗位筛选_{unique_name}.xlsx"
        records.sort(key=lambda r:r["item"].get("input_index",0))
        export_excel(target,records,run,fast=len(records)>800)
        verified=validate_excel(target,len(records),sort_mode="input",expected_images=0)
        outputs.append({"input":path,"rows":len(records),"output":str(target),"validation":verified["rows"]})
    merged=None
    if also_merge:
        merged=merge_runs([run],output_root/"AI岗位筛选_跨公司汇总.xlsx")
    return {"status":"partial" if errors else "succeeded", "source_documents":len(groups),
            "per_document":outputs,"total_rows":sum(x["rows"] for x in outputs),
            "errors":errors[:100],"merge_summary":merged,
            "notes":"只从已落盘的决策导出，分文件与汇总均不重新识图/调用模型/重新评分"}

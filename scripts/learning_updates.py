"""V1.4.7 | Deliverable-aware learning from user-supplied career notes.

Preview never writes to the workbook. Apply changes only user-approved derived cells;
raw JD evidence, scoring and source order remain unchanged. Saved updates replay after finalize.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from excel import HEADERS, neutralize_formula_text

STORE = "learning_updates.json"
IMMUTABLE = set(HEADERS) - {"卡点判断", "卡点如何解决", "待确认条件", "模型归纳", "推断信息"}
# A semantically similar but broader column requires the user to decide whether to reuse it.
RELATED_COLUMNS = {"面试反向提问": ["待确认条件", "卡点判断"],
                   "薪资核实清单": ["待确认条件"],
                   "岗位卡点复核": ["卡点判断", "卡点如何解决"]}
COLUMN_ALIASES = {"面试反向提问": ["面试反向提问", "HR反向提问", "反问HR", "面试核实问题"],
                  "薪资核实清单": ["薪资核实清单", "薪酬反向提问"],
                  "岗位卡点复核": ["岗位卡点复核", "岗位卡点", "卡点判断"]}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".learn_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _excel_for(run_dir):
    run_dir = Path(run_dir).expanduser().resolve()
    summary = _load_json(run_dir / "summary.json")
    path = Path(summary["excel"]).expanduser().resolve()
    if not path.is_file():
        raise ValueError("原交付Excel不存在，先执行 finalize 或确认文件已恢复")
    if path.parent != run_dir:
        raise ValueError("学习更新只允许修改当前运行目录的最终Excel")
    return run_dir, path


def _headers(ws):
    h = {str(c.value): i for i, c in enumerate(ws[1], 1) if c.value is not None}
    if list(h)[:len(HEADERS)] != HEADERS:
        raise ValueError("需要V1.4.6+标准岗位汇总格式，且基础列未被重排")
    return h


def _topic(text):
    if any(k in text for k in ("反向提问", "反问HR", "面试时反问", "面试问HR", "问HR", "岗位挂了多久")):
        return "面试反向提问", "interview_questions"
    if any(k in text for k in ("薪资构成", "奖金比例", "谈薪", "社保基数")):
        return "薪资核实清单", "compensation_questions"
    if any(k in text for k in ("岗位卡点", "真实痛点", "职责难点")):
        return "卡点判断", "jd_pain_points"
    return "职场内容补充", "other"


def _suggest_questions(row, h, text, profile):
    """Conservative template for HR reversal notes, based on recorded JD facts only."""
    job = str(row[h["岗位名称"] - 1] or "")
    jd = str(row[h["JD全文"] - 1] or "")
    salary = str(row[h["薪资范围"] - 1] or "")
    problem = str(row[h["卡点判断"] - 1] or "")
    title = job.lower()
    preferred = " ".join(profile.get("target_roles", [])).lower()
    redlines = " ".join(str(x.get("description", "")) for x in profile.get("hard_reject_rules", []))
    verified_skills = {str(x).lower() for x in (profile.get("confirmed_skills") or [])}
    reasons = []
    questions = []
    # Focus on the JD-specific verification question before general interview advice.
    if any(k in jd.lower() for k in ("agent", "rag", "智能体", "大模型")):
        questions.append("入职后优先交付的AI应用是什么，如何衡量效果与稳定性？")
        reasons.append("JD含AI应用职责，需核实真实交付而非仅标题含AI")
    if any(k in jd.lower() for k in ("java", "c++", "后端", "架构", "算法", "微调")):
        questions.append("实际日常职责中，核心研发、后端维护和模型算法工作各占多少？")
        reasons.append("JD包含工程/算法关键词，需核实与本人方向及红线是否一致")
    if "c++" in jd.lower() and "C++" in redlines:
        questions.insert(0,"JD中的C++是必须独立编写的核心职责，还是优先项/协作项？")
        reasons.insert(0,"个人画像明确设置了C++相关排除条件")
    if "python" in jd.lower() and "python" in verified_skills:
        reasons.append("当前画像已确认具备Python，可针对实际技术交付深问")
    if any(k in preferred for k in ("agent", "rag", "智能体")) and any(k in jd for k in ("运营", "销售", "业务支持")):
        questions.insert(0,"能否介绍该岗位实际自行开发Agent的比例，以及哪些任务由工程团队承担？")
        reasons.insert(0,"当前目标偏Agent应用研发，需要核实JD的业务与工程职责占比")
    if any(k in jd for k in ("评测", "稳定性", "召回率", "性能优化")):
        questions.append("团队当前最难达成的质量指标是什么，已有评测集和验收机制吗？")
        reasons.append("JD含评测或质量交付线索")
    if any(k in title for k in ("运营", "策略", "产品经理", "售前")):
        questions.append("该岗位的实际工作中，AI方案落地与运营/策略协调各占多少？")
        reasons.append("岗位名称提示应确认主要职责")
    if problem and not problem.startswith("待分析"):
        reasons.append("结合现有卡点判断核实团队需求")
    # The four core HR reverse-question dimensions must not be buried by JD-specific questions.
    jd_specific = questions[:2]
    questions = ["这个HC是新增还是替补？岗位开放多久、何时批准、为何没有内部转岗？",
                 "团队人数、汇报对象、团队分工与协作关系是什么？",
                 "这个岗位前90天最重要的交付是什么？HR筛选简历时最看重哪些能力，过往候选人主要在哪类能力上不匹配？",
                 "薪酬由固定月薪、发薪月数、绩效奖金与浮动部分如何组成？"]
    questions = jd_specific + questions
    return "【优先反问】" + "\n".join(f"{i}. {q}" for i,q in enumerate(questions[:6],1)) + ("\n【选择依据】"+"；".join(reasons[:2]) if reasons else "")



def preview(run_dir, text_file, column=None, min_score=4, limit=10, creator=""):
    """Produce a contextual preview plus a draft proposal WITHOUT modifying the workbook."""
    if not 1 <= min_score <= 5 or not 1 <= limit <= 100:
        raise ValueError("分数与抽样上限越界")
    rd, path = _excel_for(run_dir)
    raw = Path(text_file).read_text(encoding="utf-8-sig")
    if not raw.strip():
        raise ValueError("用户提供的职场内容为空")
    topic, kind = _topic(raw)
    preferred = (column or topic).strip()
    # Resolve known synonymous output-column labels before checking for an existing column.
    preferred = next((canonical for canonical, aliases in COLUMN_ALIASES.items()
                      if preferred in aliases), preferred)
    if preferred in IMMUTABLE:
        raise ValueError("不能通过职场内容覆盖原始JD、评分、身份或证据列")
    profile = _load_json(rd / "profile.json")
    with closing(load_workbook(path, read_only=True)) as book:
        ws = book["岗位汇总"]
        h = _headers(ws)
        matching = next((a for a in COLUMN_ALIASES.get(preferred, [preferred]) if a in h), None)
        existing = matching or (preferred if preferred in h else None)
        related = [x for x in RELATED_COLUMNS.get(preferred, []) if x in h and x != existing]
        picked = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            score = row[h["推荐等级（1-5分）"] - 1]
            if not isinstance(score, (int,float)) or score < min_score:
                continue
            picked.append((score,row))
        picked.sort(key=lambda t:-t[0])
        picked = picked[:limit]
        updates = []
        snapshots = []
        for score, row in picked:
            iid = str(row[h["图片编号"] - 1] or "")
            jd = str(row[h["JD全文"] - 1] or "")
            # For non-HR content, Agent must provide contextual content, not generic paraphrase.
            value = _suggest_questions(row, h, raw, profile) if kind == "interview_questions" else ""
            if value:
                updates.append({"item_id":iid,"column":existing or preferred,"value":value,
                                "mode":"append", "jd_evidence":"", "profile_evidence":""})
            snapshots.append({"item_id":iid,"score":score,
                              "company":str(row[h["公司名"] - 1] or ""),
                              "job_title":str(row[h["岗位名称"] - 1] or ""),
                              "jd_excerpt":jd[:300], "suggestion":value[:450]})
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    draft = {"source":{"kind":"user_shared_career_content","platform":"小红书（如用户提供）",
                       "creator":creator,"sha256":digest,"topic":kind},
             "target_column":existing or preferred,"updates":updates,
             "summary":f"已识别主题：{topic}；{len(picked)}个高优先岗位候选，实际修改前请确认新增列。"}
    draft_path = rd / "learning_draft.json"
    _save_json(draft_path, draft)
    return {"status":"preview", "topic":topic, "suggested_column":preferred,
            "existing_column":existing, "related_existing_columns":related,
            "requires_new_column_confirmation":existing is None,
            "affected_candidates":len(picked), "draft":str(draft_path),
            "sample_jobs":snapshots,
            "next":("请确认新增列，或选择复用相关已有列" if existing is None else
                    "可以直接更新已有列；不会改变JD、推荐等级、原始图或评分"),
            "agent_instruction":"如原文为岗位/求职建议，基于已保存profile.json、JD全文与本段内容逐条调整draft的value；不得编造用户经历或企业事实。"}


def _read_store(run_dir):
    path = Path(run_dir) / STORE
    return _load_json(path) if path.exists() else {"version":1,"edits":[],"sources":[]}


def _update_workbook(path, edits, allowed_new, *, replay=False):
    """Single atomic workbook save, no score/JD/provenance mutation."""
    path = Path(path)
    with closing(load_workbook(path)) as book:
        ws = book["岗位汇总"]
        h = _headers(ws)
        for col in sorted({x["column"] for x in edits}):
            if col in IMMUTABLE:
                raise ValueError(f"禁止覆盖原始评分或JD字段：{col}")
            if col not in h:
                if col not in allowed_new:
                    raise ValueError(f"未获确认的新列：{col}")
                c = ws.cell(1, ws.max_column+1, col)
                c.font = Font(name="微软雅黑",bold=True,color="FFFFFF",size=10)
                c.fill = PatternFill("solid",fgColor="17324D")
                c.alignment = Alignment(wrap_text=True,vertical="center")
                h[col] = c.column
                ws.column_dimensions[get_column_letter(c.column)].width=45
        row_index = {}
        id_col = h["图片编号"]
        for r in range(2,ws.max_row+1):
            item_id = ws.cell(r,id_col).value
            if item_id:
                if str(item_id) in row_index:
                    raise ValueError(f"图片编号重复：{item_id}")
                row_index[str(item_id)] = r
        old_scores={r:ws.cell(r,3).value for r in range(2,ws.max_row+1)}
        for edit in edits:
            key = edit["item_id"]
            if key not in row_index:
                raise ValueError(f"岗位编号不存在：{key}")
            cell = ws.cell(row_index[key],h[edit["column"]])
            value = neutralize_formula_text(edit["value"])
            if len(str(value))>32767:
                raise ValueError(f"Excel单元格超过32767字符：{key}")
            cell.value = value
            cell.alignment=Alignment(vertical="top",wrap_text=True)
        for r, old in old_scores.items():
            if ws.cell(r,3).value != old:
                raise AssertionError("技能学习补丁不允许变更推荐等级")
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
        if ws.freeze_panes != "D2":
            raise ValueError("Excel冻结设置与原契约冲突")
        fd,tmp = tempfile.mkstemp(prefix=".learning_updated_",suffix=".xlsx",dir=path.parent)
        os.close(fd)
        try:
            book.save(tmp)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return str(path)


def apply(run_dir, proposals_path, *, confirm_new_column=False):
    rd, path = _excel_for(run_dir)
    proposals = _load_json(proposals_path)
    changes = proposals.get("updates")
    if not isinstance(changes,list) or not changes:
        raise ValueError("没有可更新的岗位建议。请先完成岗位关联分析，不生成空列")
    if len(changes) > 30000:
        raise ValueError("单次更新超过30000条，请拆分处理")
    source = proposals.get("source") or {}
    digest=source.get("sha256","")
    if not re.fullmatch(r"[a-f0-9]{64}",digest):
        raise ValueError("缺少内容来源SHA256，先运行 learn-preview")
    with closing(load_workbook(path, read_only=True)) as book:
        ws=book["岗位汇总"]
        h=_headers(ws)
        existing=set(h)
        known={str(row[h["图片编号"]-1]):row for row in ws.iter_rows(min_row=2,values_only=True)}
        allowed_new=set()
        prepared=[]
        for change in changes:
            key=str(change.get("item_id","")).strip()
            col=str(change.get("column") or proposals.get("target_column") or "").strip()
            value=str(change.get("value","")).strip()
            if not key or key not in known or not col or len(col)>40 or not value:
                raise ValueError(f"无效岗位/列/内容：{key}/{col}")
            if col in IMMUTABLE or col in {"推荐等级", "得分"}:
                raise ValueError(f"不可变列被修改：{col}")
            if col not in existing:
                if not confirm_new_column:
                    raise PermissionError(f"新增‘{col}’需要用户先确认；执行时加 --confirm-new-column")
                allowed_new.add(col)
            jd_q=str(change.get("jd_evidence") or "").strip()
            if jd_q:
                r=known[key]
                haystack="\n".join(str(r[h[x]-1] or "") for x in ("文字","JD全文"))
                if jd_q not in haystack:
                    raise ValueError(f"岗位{key}的JD引文无法在原文找到")
            else:
                # General interview questions are allowed, but must not pretend to establish company-specific facts.
                if any(x in value for x in ("公司已经", "内部已经确认", "HR承认")):
                    raise ValueError(f"岗位{key}缺JD证据，不得声称企业内部事实")
            mode=change.get("mode","append")
            if mode not in {"append","replace"}:
                raise ValueError(f"未知更新模式：{mode}")
            prior=str(known[key][h[col]-1] or "") if col in existing else ""
            if mode=="append" and prior and value not in prior:
                value=prior+"\n\n【新增职场内容建议】\n"+value
            elif mode=="append" and value in prior:
                value=prior
            prepared.append({"item_id":key,"column":col,"value":value})
        keys=[(x["item_id"],x["column"]) for x in prepared]
        if len(set(keys)) != len(keys):
            raise ValueError("同次更新的岗位编号与列重复，请先合并内容")
    if all((x["column"] in existing and
            str(known[x["item_id"]][h[x["column"]]-1] or "") == x["value"])
           for x in prepared):
        return {"status":"already_applied","excel":str(path),"updated_cells":0}
    store=_read_store(rd)
    # Idempotent application of the same source + proposals, not a blind repeat update.
    stamp=hashlib.sha256(json.dumps(prepared,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()
    if any(x.get("sha256")==digest and x.get("edits_hash")==stamp for x in store["sources"]):
        return {"status":"already_applied", "excel":str(path),"updated_cells":0}
    merged={(x["item_id"],x["column"]):x for x in store["edits"]}
    for x in prepared:
        merged[(x["item_id"],x["column"])]=x
    _update_workbook(path,prepared,allowed_new)
    store["edits"]=list(merged.values())
    store["sources"].append({"sha256":digest,"edits_hash":stamp,
                             "creator":source.get("creator",""),"topic":source.get("topic",""),
                             "updated_at":datetime.now(timezone.utc).isoformat(),"cells":len(prepared)})
    _save_json(rd / STORE,store)
    # The markdown is a render aid, not a second deliverable: give the user only chat recap + Excel.
    from collections import Counter
    count=Counter(x["column"] for x in prepared)
    summary_lines=["## 职涯罗盘 · 新内容优化结果",
                   f"收到并分析新提供的职场内容（主题：{source.get('topic','待确认')}；来源：{source.get('creator') or '用户提供'}）。",
                   "本次只更新已交付Excel中的建议字段，**未重做OCR、未重新评分**。", "",
                   "### 本轮更新", *[f"- **{col}**：{qty}条岗位" for col,qty in count.items()],
                   "", "### 建议如何使用", "优先查看更新后的高分岗位，面试时结合JD提问并记录招聘方答复，再决定投递/接Offer。",
                   "", "**本轮只交付更新后的Excel和这段聊天总结。**"]
    (rd / "learning_summary.md").write_text("\n".join(summary_lines)+"\n",encoding="utf-8")
    return {"status":"updated", "excel":str(path),"chat_summary":str(rd/"learning_summary.md"),"updated_cells":len(prepared),
            "columns":sorted(set(x["column"] for x in prepared)),
            "scores_unchanged":True,"next":"聊天只交付最终总结与更新后的Excel；不重复发布JSON/草稿"}


def replay_saved(run_dir,excel_path):
    """Replay only confirmed edits after core regenerates workbook; no new user permission needed."""
    store=_read_store(run_dir)
    edits=store.get("edits") or []
    if not edits:
        return 0
    new_cols={x["column"] for x in edits if x["column"] not in HEADERS}
    _update_workbook(excel_path,edits,new_cols,replay=True)
    return len(edits)

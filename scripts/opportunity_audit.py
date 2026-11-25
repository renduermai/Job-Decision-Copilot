"""V1.4.8 低分复核：只读JD/决策，用证据标出疑似误杀，不回写推荐分。"""
from __future__ import annotations

from collections import Counter

AUDIT_HEADERS = ["图片编号", "公司", "岗位", "原推荐分", "复核优先级", "疑点与核实方向", "JD原文证据"]


def _evidence(jd, texts):
    """Only return exact substrings found in the original OCR/JD evidence corpus."""
    collected = []
    for quote in texts:
        q = str(quote or "").strip()
        if q and q not in collected:
            collected.append(q[:160])
    return "；".join(collected[:2])


def evaluate_audit(records, profile, limit=100):
    """Read-only risk triage. No new model call, no score modification.

    `records` already passed existing JD/assessment scoring validation in finalize.
    High-confidence hard rejects remain rejected; no score 1 is promoted.
    """
    if limit < 1 or limit > 10000:
        raise ValueError("复核输出上限应介于1到10000")
    matches = []
    reviewed = 0
    hard_rejected = 0
    severity = Counter()
    seen = set()
    for rec in records:
        item, jd, d = rec["item"], rec["jd"], rec["decision"]
        # Cross-source duplicate lines are one job for risk triage; original Excel rows remain untouched.
        identity = item.get("sha256") or item.get("item_id")
        if identity in seen:
            continue
        seen.add(identity)
        score = d["recommendation_score"]
        if score > 3:
            continue
        reviewed += 1
        signals = []
        quotes = []
        # A proven hard rejection stays 1. Only detect *structurally conflicting facts*;
        # never auto-promote the job, and never interpret a weak pay/title clue as proof.
        if d.get("hard_reject"):
            role = jd.get("role_analysis") or {}
            requirements = role.get("skill_requirements") or []
            for trig in d.get("triggered_rules", []):
                desc = (trig.get("description", "") or "").lower()
                condition = trig.get("structured_condition") or {}
                wanted = str(condition.get("skill") or "").casefold()
                # Includes older profiles with a semantic-only Java primary-backend redline.
                if not wanted and "java" in desc and role.get("primary_role_family")=="agent_application":
                    wanted = "java"
                if not wanted:
                    continue
                levels = [v for v in requirements if str(v.get("skill", "")).casefold() == wanted]
                if levels and all(v.get("requirement_level") in
                                  {"alternative", "preferred", "mentioned", "collaboration", "unknown"} for v in levels):
                    signals.append(f"硬红线【{trig.get('rule_id','未命名')}】已触发，但结构化JD将{wanted}标为任选/加分/提及；请人工核实强制程度，原1分不变")
                    quotes.extend(v.get("evidence_quote", "") for v in levels)
            if not signals:
                hard_rejected += 1
                continue
        else:
            pass
        experience = jd.get("experience_resolution") or {}
        if not d.get("hard_reject") and experience.get("conflict"):
            signals.append("职位筛选栏与JD正文经验条件存在冲突；按正文核对实际门槛")
            quotes.extend(experience.get("detail_evidence", []))
            quotes.extend(experience.get("listing_evidence", []))
        fit = d.get("role_fit") or {}
        if not d.get("hard_reject") and fit.get("conclusion") == "unknown":
            signals.append("主要职责匹配尚不确定；向招聘方核实日常交付占比")
            quotes.append(fit.get("evidence_quote", ""))
        pending = d.get("pending_conditions") or []
        if not d.get("hard_reject") and pending:
            signals.append("评分仍有待确认条件；低分不宜当作完全确证的排除")
            # Pending conditions are hypotheses, not JD quotes; do not show as direct evidence.
        role = jd.get("role_analysis") or {}
        ambiguities = role.get("ambiguities") or []
        if not d.get("hard_reject") and ambiguities:
            signals.append("JD对职责/要求仍有歧义，需要人工核对原文")
            quotes.extend(x.get("evidence_quote", "") for x in role.get("responsibility_evidence", []) if isinstance(x, dict))
        if not signals:
            continue
        priority = "优先复核" if d.get("hard_reject") or experience.get("conflict") or fit.get("conclusion") == "unknown" else "建议核实"
        severity[priority] += 1
        matches.append({"item_id":item["item_id"], "company":jd.get("company_name", "未提及"),
                        "job_title":jd.get("job_title", "未提及"), "score":score,
                        "priority":priority, "reason":"；".join(dict.fromkeys(signals)),
                        "jd_evidence":_evidence(jd, quotes),
                        "questions": ["这项条件是必须独立承担，还是可通过协作/相关经历补足？",
                                      "岗位实际主要交付是什么、占工作时间多大比例？"]})
    matches.sort(key=lambda x: (0 if x["priority"] == "优先复核" else 1, -x["score"], x["item_id"]))
    return {"schema_version":"1.0", "scope":"仅原推荐等级1–3分，审核结果不修改原评分",
            "reviewed_low_score":reviewed, "validated_hard_exclusions":hard_rejected,
            "flagged_total":len(matches), "priority_counts":dict(severity),
            "shown":min(limit,len(matches)), "truncated":len(matches)>limit,
            "items":matches[:limit]}


def audit_sheet(book, result):
    """Append a small review sheet during the *same* Excel serialization (normal or write_only)."""
    if not result or not result["items"]:
        return
    from excel import neutralize_formula_text
    sh = book.create_sheet("低分复核")
    sh.append(AUDIT_HEADERS)
    for item in result["items"]:
        sh.append([neutralize_formula_text(item["item_id"]),
                   neutralize_formula_text(item["company"]),
                   neutralize_formula_text(item["job_title"]),
                   item["score"], item["priority"],
                   neutralize_formula_text(item["reason"]),
                   neutralize_formula_text(item["jd_evidence"])])
    if not getattr(book, "write_only", False):
        sh.freeze_panes="E2"
        sh.auto_filter.ref=sh.dimensions
        for col,width in {"A":15,"B":23,"C":28,"D":13,"E":15,"F":70,"G":75}.items():
            sh.column_dimensions[col].width=width


def render_audit_summary(result):
    lines=["## 低分岗位复核提醒",
           f"1–3分共 {result['reviewed_low_score']} 个；有证据的硬红线排除 {result['validated_hard_exclusions']} 个，不因复核而自动改分。",
           f"**另有 {result['flagged_total']} 个岗位值得核实**（当前展示 {result['shown']} 个，属于风险提示而非已确认的误判）。"]
    for x in result["items"][:8]:
        lines.append(f"- **{x['company']}｜{x['job_title']}**（{x['item_id']}，原{x['score']}分）：{x['reason']}"+
                     (f"；原文：{x['jd_evidence'][:120]}" if x['jd_evidence'] else "；JD证据不足，需向HR核实"))
    if not result["items"]:
        lines.append("本轮没有触发自动复核规则的低分岗位。**这不等于不存在误判**，仍可人工检查原始JD。")
    return "\n".join(lines)+"\n"


def audit_run(run_dir, limit=100, update_excel=False):
    """Re-audit an existing run without re-running OCR or scoring; optional workbook overlay."""
    import os
    import tempfile
    from pathlib import Path
    from core import read_json, write_json, write_text
    from openpyxl import load_workbook
    rd=Path(run_dir).expanduser().resolve()
    manifest=read_json(rd/"manifest.json")
    lookup={x["item_id"]:x for x in manifest["items"]}
    records=[]
    for item in manifest["items"]:
        source=item
        while source.get("status")=="duplicate":
            source=lookup[source["duplicate_of"]]
        try:
            jd=read_json(source["jd_path"])
            decision=read_json(source["decision_path"])
        except (OSError,ValueError):
            continue
        records.append({"item":item,"jd":jd,"decision":decision})
    result=evaluate_audit(records,read_json(rd/"profile.json"),limit)
    write_json(rd/"audit_report.json",result)
    write_text(rd/"audit_summary.md",render_audit_summary(result))
    if update_excel:
        summary=read_json(rd/"summary.json")
        path=Path(summary["excel"]).expanduser().resolve()
        if path.parent!=rd or not path.exists():
            raise ValueError("只允许更新当前运行目录内已存在的交付Excel")
        book=load_workbook(path)
        try:
            if "低分复核" in book:
                del book["低分复核"]
            audit_sheet(book,result)
            fd,tmp=tempfile.mkstemp(prefix="audit_",suffix=".xlsx",dir=rd)
            os.close(fd)
            try:
                book.save(tmp)
                os.replace(tmp,path)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
        finally:
            book.close()
    return {"status":"completed", "flagged":result["flagged_total"],
            "reviewed":result["reviewed_low_score"], "report":str(rd/"audit_summary.md"),
            "excel_updated":bool(update_excel), "score_unchanged":True}

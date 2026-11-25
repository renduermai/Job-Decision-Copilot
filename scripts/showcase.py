"""V1.4.8 作品机会建议：只从已保存的高分JD技能需求和用户已提供画像生成草案。"""
from __future__ import annotations
import json
from pathlib import Path
from skill_insights import normalize_skill

# Editorial suggestions, not claims about an employer or the user's past achievements.
TOPICS = {
    "agent评测": ("如何判断一个Agent是否真正能稳定交付？", "用自己已做过的评测/错误复盘展示评分维度、失败案例和验证过程"),
    "大模型评测": ("大模型应用从Demo到验收：什么指标才有用？", "整理一次真实评测过程及可复核的数据与限制"),
    "langgraph": ("Agent工作流出现异常，怎样设计可恢复的执行链？", "展示真实的工作流节点、状态与失败恢复记录"),
    "rag": ("RAG回答不准，究竟卡在检索、证据还是生成？", "展示可验证的检索错误分析或对比实验"),
    "python": ("我如何用Python把一项重复工作变成可靠流程？", "用实际代码和失败修复记录展示自动化交付"),
    "sql": ("从自然语言到SQL：怎样减少错误答案？", "用真实Text-to-SQL测试、保护机制和错误案例证明方法"),
    "社媒洞察": ("热议评论背后，消费者真正表达了什么？", "用可授权公开的聚合样例展示洞察过程与验证方法"),
    "消费者研究": ("如何从零散评论还原可执行的消费者需求？", "展示研究提纲、证据分类和业务建议的形成过程"),
    "数据分析": ("分析报告怎样从描述数据变成可执行决策？", "展示问题定义、证据和决策反馈闭环"),
}


def _evidence_from_profile(profile, skill):
    """Separate confirmed skills from weaker self-described experience, never fabricate projects."""
    raw = [str(x).strip() for x in profile.get("confirmed_skills", []) if str(x).strip()]
    for known in raw:
        if normalize_skill(known) == normalize_skill(skill):
            return {"kind":"confirmed_skill", "quote":known}
    for key in ("resume_text", "background", "project_experience"):
        source = profile.get(key)
        if not isinstance(source,str) or not source.strip():
            continue
        if skill.casefold() in source.casefold():
            start = max(0,source.casefold().index(skill.casefold())-55)
            end = min(len(source),start+170)
            return {"kind":"self_reported_text_unverified", "quote":source[start:end]}
    for p in profile.get("projects", []):
        if isinstance(p,str) and skill.casefold() in p.casefold():
            return {"kind":"self_reported_text_unverified", "quote":p[:170]}
    return {"kind":"not_provided", "quote":""}


def build_showcase(insights, profile, limit=5):
    if not 1 <= limit <= 20:
        raise ValueError("作品机会建议最多展示20项")
    opportunities=[]
    for demand in sorted(insights.get("all_skill_demands", []),
                         key=lambda v:(-v["demand_jobs"],-v["required_jobs"],v["skill"])):
        if len(opportunities)>=limit:
            break
        if demand["demand_jobs"]<=0:
            continue
        skill=demand["skill"]
        # Reuse only exact quotes extracted from JD and bound to job/item IDs in skill_insights.
        evidence=[]
        for fact in insights.get("skills", []):
            if normalize_skill(fact["skill"])==normalize_skill(skill):
                evidence=[e for e in fact.get("evidence",[]) if e.get("quote") and e.get("item_id")][:3]
                break
        if not evidence:
            evidence=[e for e in (insights.get("all_skill_evidence",{}).get(skill)
                   or insights.get("all_skill_evidence",{}).get(normalize_skill(skill), [])) if e.get("quote") and e.get("item_id")][:3]
        personal=_evidence_from_profile(profile,skill)
        title, artifact_hint = TOPICS.get(normalize_skill(skill),
                                          (f"高分岗位为什么需要「{skill}」？我能提供什么可信证据？",
                                           f"围绕「{skill}」的真实项目复盘（问题→解决过程→验证证据）"))
        artifact=(artifact_hint if personal["kind"]!="not_provided"
                  else f"围绕「{skill}」的岗位需求解读；在核实个人经历前，不写成已完成项目")
        opportunities.append({
            "skill":skill,
            "topic":title,
            "demand_jobs":demand["demand_jobs"],
            "demand_coverage_percent":demand["demand_coverage_percent"],
            "jd_evidence":evidence,
            "candidate_evidence":personal,
            "suggested_artifact":artifact,
            "channels":["GitHub项目文档","小红书经验分享","B站项目演示"],
            "target_roles":profile.get("target_roles", [])[:3],
            "next_action":("引用具体项目文件、指标或演示证明技能；发表前确认所有成果数字" if personal["kind"]!="not_provided"
                           else "先核对是否真的具备此技能；未确认前只介绍市场需求，不自称已有项目"),
        })
    return {"schema_version":"1.0", "status":"suggested" if opportunities else "insufficient_data",
            "scope":"只依据本批4–5分岗位中已抽取的技能需求及画像文本；为作品选题草案，不保证招聘方联系",
            "high_score_job_count":insights.get("high_score_job_count",0),
            "items":opportunities}


def render_showcase(result):
    lines=["## 让机会找到我：最值得公开展示的作品机会", "基于本批高分JD和已提供画像生成；不自动发布、不虚构项目成果，也不保证招聘结果。"]
    if not result["items"]:
        return "\n".join(lines+["没有足够高分岗位的明确技能需求，暂不生成选题。"])+"\n"
    for idx,item in enumerate(result["items"],1):
        evidence="；".join(f"{e['item_id']}：{e['quote'][:110]}" for e in item["jd_evidence"]) or "技能需求已统计，但缺少可附上的逐字JD引文，请人工核实"
        personal=item["candidate_evidence"]
        proof=(f"已确认技能：{personal['quote']}" if personal["kind"]=="confirmed_skill"
               else f"画像自述提及、成果尚待核实：{personal['quote']}" if personal["kind"]=="self_reported_text_unverified"
               else "当前画像未提供相关能力证据，不能声称自己已经具备")
        lines.extend(["",f"### {idx}. {item['topic']}",
                      f"- **岗位需求**：{item['demand_jobs']} 个高分岗位涉及该技能；JD证据：{evidence}",
                      f"- **已有能力依据**：{proof}",
                      f"- **作品形式**：{item['suggested_artifact']}",
                      f"- **发布与目标**：{ '、'.join(item['channels']) }；目标方向：{'、'.join(item['target_roles']) or '待确认'}",
                      f"- **行动建议**：{item['next_action']}"])
    return "\n".join(lines)+"\n"


def showcase_run(run_dir, limit=5):
    rd=Path(run_dir).expanduser().resolve()
    from core import read_json, write_json, write_text
    insights=read_json(rd/"skill_insights.json")
    profile=read_json(rd/"profile.json")
    result=build_showcase(insights,profile,limit)
    write_json(rd/"showcase_opportunities.json",result)
    write_text(rd/"showcase_report.md",render_showcase(result))
    return {"status":result["status"],"suggestions":len(result["items"]),
            "report":str(rd/"showcase_report.md"), "data":str(rd/"showcase_opportunities.json"),
            "next":"在聊天框展示报告；仅在用户要求时生成完整帖文或演示大纲，内容须人工确认"}

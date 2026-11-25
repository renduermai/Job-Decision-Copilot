"""从已完成的结构化JD/决策计算可复核的高分岗位技能需求与假设覆盖。

数据与评分互不影响：模型解析 skill_requirements，Python 负责归一化/计数/模拟。
"""
from __future__ import annotations
from collections import defaultdict
from typing import Any

# 显式的保守同义词映射；不对含糊的“AI/框架”等词做自动归并。
ALIASES = {
    "python": {"python", "python3"},
    "java": {"java", "java se"},
    "c++": {"c++", "cpp"},
    "sql": {"sql", "结构化查询语言"},
    "langgraph": {"langgraph"},
    "langchain": {"langchain"},
    "rag": {"rag", "检索增强生成", "retrieval augmented generation", "retrieval-augmented generation"},
    "mcp": {"mcp", "model context protocol"},
    "tool calling": {"tool calling", "function calling", "工具调用", "函数调用"},
    "docker": {"docker", "容器化部署docker"},
    "kubernetes": {"k8s", "kubernetes"},
    "agent评测": {"agent评测", "agent eval", "agent evals", "智能体评测", "agent evaluation"},
    "大模型评测": {"大模型评测", "llm eval", "llm evaluation"},
    "数据分析": {"数据分析", "data analysis"},
    "社媒洞察": {"社媒洞察", "社交媒体洞察", "social listening"},
    "消费者研究": {"消费者研究", "consumer insight", "消费者洞察"},
    "情绪分析": {"情绪分析", "情感分析", "sentiment analysis"},
    "pytorch": {"pytorch"},
    "excel": {"excel"},
    "dify": {"dify"},
    "coze": {"coze", "扣子"},
}
REVERSE = {x.casefold(): label for label, values in ALIASES.items() for x in values}
LEVELS = {"required", "preferred"}


def normalize_skill(skill: str) -> str:
    key = " ".join(str(skill).casefold().split())
    return REVERSE.get(key, key)


def build_jobs(records):
    jobs = []
    seen = set()
    for r in records:
        item, jd, decision = r["item"], r["jd"], r["decision"]
        if decision["recommendation_score"] < 4:
            continue
        if item.get("sha256") in seen:
            continue
        seen.add(item.get("sha256"))
        merged = {}
        for req in jd.get("role_analysis", {}).get("skill_requirements", []):
            if req.get("requirement_level") not in LEVELS:
                continue
            label = normalize_skill(req["skill"])
            if not label:
                continue
            candidate = {"level": req["requirement_level"], "evidence_quote": req["evidence_quote"]}
            if label not in merged or (candidate["level"] == "required" and merged[label]["level"] != "required"):
                merged[label] = candidate
        jobs.append({"item_id": item["item_id"], "company": jd.get("company_name", "未提及"),
                     "title": jd.get("job_title", "未提及"), "score": decision["recommendation_score"],
                     "skills": merged,
                     "pain_point": decision.get("pain_point_analysis", {}).get("problem", "待分析：缺少卡点证据"),
                     "pain_solution": decision.get("pain_point_analysis", {}).get("solution", "待核实：尚无可验证解决依据")})
    return sorted(jobs, key=lambda x: (-x["score"], x["item_id"]))


def create_insights(records, profile: dict[str, Any]) -> dict[str, Any]:
    jobs = build_jobs(records)
    count = len(jobs)
    confirmed = {normalize_skill(s) for s in profile.get("confirmed_skills", [])}
    complete = profile.get("skills_confirmation_complete") is True
    requirements = defaultdict(lambda: {"required": set(), "preferred": set(), "evidence": []})
    for job in jobs:
        for skill, fact in job["skills"].items():
            data = requirements[skill]
            data[fact["level"]].add(job["item_id"])
            data["evidence"].append({"item_id":job["item_id"],"level":fact["level"],
                                     "quote":fact["evidence_quote"]})
    # 只计算存在明确 required 要求的高分岗位；不把“未提及”当作“无技能要求”。
    measurable = [j for j in jobs if any(f["level"] == "required" for f in j["skills"].values())]
    def covered_job_ids(skills):
        return [j["item_id"] for j in measurable
                if {k for k,v in j["skills"].items() if v["level"] == "required"}.issubset(skills)]
    before_ids = covered_job_ids(confirmed) if complete else []
    list_skills = []
    all_demands = []
    for name, req in requirements.items():
        required_jobs, preferred_jobs = req["required"], req["preferred"]
        all_demands.append({"skill":name, "required_jobs":len(required_jobs),
                            "preferred_jobs":len(preferred_jobs),
                            "demand_jobs":len(required_jobs | preferred_jobs),
                            "demand_coverage_percent":round(100*len(required_jobs|preferred_jobs)/count,1) if count else 0})
        if complete and name in confirmed:
            continue  # 已确认掌握不推荐补齐，但必须仍列在岗位整体需求表
        after_ids = covered_job_ids(confirmed | {name}) if complete else []
        uplift = len(after_ids) - len(before_ids) if complete else None
        list_skills.append({"skill":name,"required_jobs":len(required_jobs),
                            "preferred_jobs":len(preferred_jobs),
                            "demand_jobs":len(required_jobs | preferred_jobs),
                            "demand_coverage_percent":round(100*len(required_jobs|preferred_jobs)/count,1) if count else 0,
                            "evidence":req["evidence"][:8],
                            "confirmed_possession": False if complete else None,
                            "baseline_covered_jobs":len(before_ids) if complete else None,
                            "scenario_covered_jobs":len(after_ids) if complete else None,
                            "incremental_jobs":uplift})
    list_skills.sort(key=lambda x: (-(x["incremental_jobs"] if x["incremental_jobs"] is not None else -1),
                                     -x["required_jobs"], -x["demand_jobs"], x["skill"]))
    top5 = list_skills[:5]
    return {"schema_version":"1.0", "definition":"在当前评分4–5分的唯一招聘截图内统计明确required/preferred技能；覆盖变化只是在用户确认完整能力清单后、假设新增一项技能使所有明确required技能满足的静态模拟；不重评JD、不预测Offer。",
            "high_score_job_count":count, "measurable_job_count":len(measurable),
            "skills_confirmation_complete":complete,"confirmed_skills":sorted(confirmed),
            "baseline_covered_jobs":len(before_ids) if complete else None,
            "top5":top5, "skills":list_skills, "all_skill_demands":all_demands,
            "all_skill_evidence":{skill:data["evidence"][:8] for skill,data in requirements.items()},
            "top10_jobs":[{"item_id":j["item_id"],"company":j["company"],
                           "title":j["title"],"score":j["score"],
                           "pain_point":j["pain_point"],"pain_solution":j["pain_solution"]} for j in jobs[:10]]}


def _md_cell(value):
    """不让招聘JD/OCR里的Markdown与换行破坏表格列的对齐。"""
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("`", "\\`")


def render_chat_report(records, profile, insights):
    num=insights["high_score_job_count"]
    lines=["## 本轮筛选结果与准备建议",f"**4–5分高分岗位：{num}个**（同一图片只计一次），建议优先复核这些岗位。",
           "", "### 我最推荐先看的岗位（最多10个）"]
    if not insights["top10_jobs"]:
        lines.append("本轮没有可靠的4–5分岗位；请先核对结果和用户画像，不编造前10名单。")
    else:
        # 与Excel同源的逐岗卡点，两列只做展示，不改变高分岗位选取与技能覆盖算法。
        lines.extend(["以下卡点是结合JD推断的招聘需求，并非已获证实的企业内部问题。",
                      "| 岗位 | 评分 | 卡点判断 | 卡点如何解决 |",
                      "|---|---:|---|---|"])
        for j in insights["top10_jobs"]:
            job = _md_cell(f"{j['company']}｜{j['title']}（{j['item_id']}）")
            lines.append(f"| {job} | {j['score']} | {_md_cell(j['pain_point'])} | {_md_cell(j['pain_solution'])} |")
    lines.extend(["", "### 高分JD最常见的核心技能（最多10项）",
                  f"统计范围：{num}个高分岗位；每个岗位每项技能只计一次，必需与加分分开统计。",
                  "| 技能 | 必需岗位数 | 加分岗位数 | 需求覆盖率 |",
                  "|---|---:|---:|---:|"])
    demands=sorted(insights["all_skill_demands"], key=lambda s:(-s["demand_jobs"],-s["required_jobs"],s["skill"]))[:10]
    if not demands:
        lines.append("| 暂无明确的必需/加分技能数据 | — | — | — |")
    for s in demands:
        lines.append(f"| {s['skill']} | {s['required_jobs']} | {s['preferred_jobs']} | {s['demand_coverage_percent']}% |")
    lines.extend(["", "### 当前最值得补充的前5项能力",
                  "仅从未确认已掌握的技能中选择；若用户技能清单不完整，下列为待确认的需求候选，不是已证实的个人缺口。"])
    if insights['top5']:
        for i,s in enumerate(insights['top5'],1):
            lines.append(f"{i}. **{s['skill']}**：需求覆盖 {s['demand_jobs']}/{num} 岗（必需{s['required_jobs']}、加分{s['preferred_jobs']}）"+(f"，假设单项补齐可新增覆盖 **{s['incremental_jobs']}岗**" if s['incremental_jobs'] is not None else "，个人缺口待确认"))
    else:
        lines.append('暂无可确认的新增技能需求。')
    lines.extend(["", "### 补齐一项技能前后：假设覆盖变化",
                  "**口径**：仅统计有明确必需技能条目的4–5分岗位；假设新增这一项技能后，所列必需技能均已确认掌握的岗位数。**不代表Offer概率、能力真实提升或岗位推荐分变动。**",
                  "| 假设新掌握技能 | 目前满足全部明确必需技能的岗位 | 模拟补齐后 | 净增加 |",
                  "|---|---:|---:|---:|"])
    if not insights["skills_confirmation_complete"]:
        lines.append("| 能力信息尚未完整确认 | 待确认 | 待确认 | 待确认 |")
        lines.append("请先确认你真实掌握的技能及能力清单是否完整，才能计算覆盖变化；**没提到不等于不会**。上面的Top5暂按岗位需求排序，并非已确认的个人能力缺口。")
    elif not insights["top5"]:
        lines.append("| 暂无模拟数据 | — | — | — |")
    else:
        denom=insights["measurable_job_count"]
        for s in insights["top5"]:
            before = round(100 * s["baseline_covered_jobs"] / denom, 1) if denom else 0
            after = round(100 * s["scenario_covered_jobs"] / denom, 1) if denom else 0
            lines.append(f"| {s['skill']} | {before}%（{s['baseline_covered_jobs']}/{denom}） | {after}%（{s['scenario_covered_jobs']}/{denom}） | +{s['incremental_jobs']}岗 |")
    targets="、".join(profile.get("target_roles", [])[:2]) or "相关岗位"
    if profile.get("confirmed_skills"):
        strengths="、".join(profile["confirmed_skills"][:3]); pre=f"我看到你已经明确提供了 **{strengths}** 等能力证据，这很棒；"
    else:
        pre=f"我看到你已经明确了 **{targets}** 的求职目标与筛选原则，这很棒；"
    picks="、".join(f"{j['company']}·{j['title']}" for j in insights["top10_jobs"][:10]) or "本轮尚无可确认的高分岗位"
    skill=insights["top5"][0]["skill"] if insights["top5"] else "进一步核对个人技能与岗位的真实要求"
    qualifier="（在目前已确认能力的静态覆盖测算中）" if insights["skills_confirmation_complete"] else "（目前仅按高分JD需求频次建议，需先确认实际能力缺口）"
    lines.extend(["", "### 最后给你的建议", pre+f"我最推荐你先看的岗位是：**{picks}**。如果接下来有时间，我建议优先考虑 **{skill}** {qualifier}；先验证项目成果能否证明这项能力，再决定是否投入时间。"])
    lines.extend(["", "**📌 看到小红书职场好文？可在评论区‘@点点给我逐字稿’，把点点的总结或逐字稿粘贴给我。我会结合你的画像和本批JD提出更新建议；已有列直接更新，新列先请你确认。**"])
    return "\n".join(lines)+"\n"

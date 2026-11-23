"""JD 卡点推断与画像解法的输出防护：独立于评分引擎，失败也不改动原推荐分。"""
from __future__ import annotations

MISSING_PROBLEM = "待分析：JD未提供足够证据，不能推断企业内部卡点"
MISSING_SOLUTION = "待核实：暂无足够岗位要求或个人经历证据"


def _profile_experience_sources(profile):
    """只有用户自述经历和已确认技能能证明'我会'；求职偏好不能充当能力。"""
    sources = []
    for key in ("background", "resume_text", "experience", "project_experience"):
        v = profile.get(key)
        if isinstance(v, str) and v.strip():
            sources.append(v)
    for key in ("confirmed_skills", "projects"):
        v = profile.get(key, [])
        if isinstance(v, list):
            sources += [value for value in v if isinstance(value, str) and value.strip()]
    return sources


def format_pain_point(analysis, ocr_text, profile):
    """返回可展示的两列及诊断。错误分析不应使整个岗位失败、也不得影响评分。"""
    fallback = {"problem": MISSING_PROBLEM, "solution": MISSING_SOLUTION,
                "status": "missing", "evidence_quotes": [], "issue": ""}
    if not isinstance(analysis, dict):
        return fallback
    problem = str(analysis.get("problem", "")).strip()
    solution = str(analysis.get("solution", "")).strip()
    quotes = analysis.get("jd_evidence_quotes", [])
    if not problem or not isinstance(quotes, list) or not quotes or not all(
        isinstance(q, str) and q.strip() and q in ocr_text for q in quotes
    ):
        return {**fallback, "issue": "卡点缺少可验证的JD原文证据"}
    # 只有结构化JD引文可信；职位背后困境本身是推断，不当作公司已证实事实。
    clean_quotes = list(dict.fromkeys(q.strip() for q in quotes))[:3]
    evidence_short = "；".join(q[:110] for q in clean_quotes)
    problem_display = f"【依据JD推断】{problem}（原文：{evidence_short}）"
    if not solution:
        return {"problem": problem_display, "solution": MISSING_SOLUTION,
                "status": "partial", "evidence_quotes": clean_quotes, "issue": "缺少解决思路"}
    basis = analysis.get("solution_basis")
    candidate_quote = str(analysis.get("candidate_evidence_quote", "")).strip()
    if basis == "candidate_experience":
        if not candidate_quote or not any(candidate_quote in source for source in _profile_experience_sources(profile)):
            return {"problem": problem_display, "solution": "待核实：模型声称的个人经历未在已确认画像中找到，不能作为解决能力", 
                    "status": "partial", "evidence_quotes": clean_quotes, "issue": "个人经历引用未获验证"}
        return {"problem": problem_display, "solution": f"【已有经历可迁移】{solution}（画像依据：{candidate_quote}）",
                "status": "ok", "evidence_quotes": clean_quotes, "issue": ""}
    if basis == "employer_expectation":
        return {"problem": problem_display, "solution": f"【岗位期待的解决方向】{solution}（候选人实际能否胜任待验证）",
                "status": "ok", "evidence_quotes": clean_quotes, "issue": ""}
    return {"problem": problem_display, "solution": MISSING_SOLUTION,
            "status": "partial", "evidence_quotes": clean_quotes, "issue": "解法来源不明确"}

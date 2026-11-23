"""单元测试合成数据；全部为 TEST DATA ONLY，不是模型能力证据。"""
import json
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw


def make_profile(profile_id="test-agent-profile", rules=("java_backend_primary", "outsourcing_or_onsite")):
    descriptions = {
        "java_backend_primary": "以Java后端微服务为主要职责",
        "agent_primary": "以Agent应用开发为主要职责（仅测试规则切换）",
        "outsourcing_or_onsite": "第三方外包或驻场",
    }
    return {
        "profile_id": profile_id,
        "profile_version": "1.0.0",
        "target_roles": ["AI Agent"],
        "background": "TEST DATA ONLY：Python、RAG、初级Agent开发",
        "preferred_cities": ["上海"],
        "priority_conditions": ["真实业务落地", "AI评测"],
        "hard_reject_rules": [{"rule_id": rule, "description": descriptions[rule]} for rule in rules],
        "rule_version": "test-rules-v1",
        "soft_risk_rules": [],
        "role_preferences": {"preferred": ["Agent应用开发"], "acceptable": [], "avoid": []},
    }


def ocr_for(kind):
    if kind == "java":
        return """TEST DATA ONLY
公司：示例科技公司
岗位：Java后端工程师
薪资：20-30K
地点：上海
学历：本科
经验：3年
职责：Spring Cloud微服务后端开发。
要求：熟悉Java。
"""
    return """TEST DATA ONLY
公司：示例科技公司
岗位：Agent开发工程师
薪资：20-30K
地点：上海
学历：本科
经验：3年
职责：开发Agent应用与RAG评测。
要求：熟悉Python。
"""


def kind_for(item):
    return "java" if "java" in item["source_file"].lower() else "agent"


def jd_for(item, kind):
    ocr = ocr_for(kind)
    title = "Java后端工程师" if kind == "java" else "Agent开发工程师"
    details = "职责：Spring Cloud微服务后端开发。\n要求：熟悉Java。" if kind == "java" else "职责：开发Agent应用与RAG评测。\n要求：熟悉Python。"
    skills = ["Java", "Spring Cloud"] if kind == "java" else ["Python", "Agent", "RAG评测"]
    skill_quote = "熟悉Java" if kind == "java" else "熟悉Python"
    return {
        "source_file": item["source_file"], "sha256": item["sha256"], "ocr_text_ref": Path(item["ocr_path"]).name,
        "company_name": "示例科技公司", "job_title": title, "salary_range": "20-30K", "location": "上海",
        "education": "本科", "experience": "3年",
        "experience_resolution": {"listing_experience": "3年", "detail_experience": "",
           "detail_requirement_level": "none", "effective_experience": "3年", "conflict": False,
           "listing_evidence": ["经验：3年"], "detail_evidence": [],
           "resolution_reason": "TEST：只有招聘栏列明年限"},
        "job_details": details, "core_skills": skills,
        "model_summary": "TEST DATA ONLY 合成岗位摘要", "inferences": [], "unknown_fields": ["是否外包未提及"],
        "role_analysis": {
            "primary_role_family": "backend_engineering" if kind == "java" else "agent_application",
            "secondary_role_families": [],
            "responsibility_evidence": [{"role_family": "backend_engineering" if kind == "java" else "agent_application", "importance": "core", "evidence_quote": "Spring Cloud微服务后端开发" if kind == "java" else "开发Agent应用"}],
            "skill_requirements": [{"skill": "Java" if kind == "java" else "Python", "requirement_level": "required", "evidence_quote": "熟悉Java" if kind == "java" else "熟悉Python"}],
            "seniority_evidence": ["经验：3年"], "ambiguities": [], "expectation_signals": [],
        },
        "field_evidence": {
            "company_name": ["公司：示例科技公司"], "job_title": [f"岗位：{title}"], "salary_range": ["20-30K"],
            "location": ["地点：上海"], "education": ["学历：本科"], "experience": ["经验：3年"],
            "job_details": [details.split("\n")[0]], "core_skills": [skill_quote],
        },
    }


def assessment_for(item, profile, kind):
    ocr = ocr_for(kind)
    matched_quotes = ["Spring Cloud微服务后端开发", "熟悉Java"] if kind == "java" else ["开发Agent应用", "熟悉Python"]
    assessments = []
    for rule in profile["hard_reject_rules"]:
        rule_id = rule["rule_id"]
        if rule_id == "java_backend_primary" and kind == "java":
            assessments.append({"rule_id": rule_id, "conclusion": "true", "evidence_quote": "Spring Cloud微服务后端开发", "rationale": "TEST：明确为Java主后端"})
        elif rule_id == "agent_primary" and kind == "agent":
            assessments.append({"rule_id": rule_id, "conclusion": "true", "evidence_quote": "开发Agent应用", "rationale": "TEST：画像切换后明确命中Agent排除"})
        elif rule_id == "outsourcing_or_onsite":
            assessments.append({"rule_id": rule_id, "conclusion": "unknown", "evidence_quote": "", "rationale": "TEST：截图未提及"})
        else:
            assessments.append({"rule_id": rule_id, "conclusion": "false", "evidence_quote": "", "rationale": "TEST：未明确命中"})
    hard = any(entry["conclusion"] == "true" for entry in assessments)
    score = 1 if hard else (4 if kind == "agent" else 2)
    return {
        "source_sha256": item["sha256"], "profile_id": profile["profile_id"],
        "profile_sha256": hashlib.sha256(json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "recommendation_score": score, "recommendation_reason": "TEST DATA ONLY：统一核心测试理由",
        "matched_points": [{"point": f"TEST匹配点{i}", "evidence_quote": quote} for i, quote in enumerate(matched_quotes, 1)],
        "gaps": [], "redline_assessments": assessments, "uncertainties": ["TEST：是否外包未提及"],
        "role_fit": {"conclusion": "aligned" if kind == "agent" else "partial", "evidence_quote": "开发Agent应用" if kind == "agent" else "Spring Cloud微服务后端开发", "rationale": "TEST：当前画像的职责契合度"},
        "soft_risk_assessments": [],
        "capability_fit": {"conclusion": "partial", "evidence_quotes": [matched_quotes[0]],
                           "rationale": "TEST DATA ONLY: 仅用于测试跨画像的能力门槛判断"},
    }


def write_layers(manifest, profile, only_assessment=False):
    for item in manifest["items"]:
        if item["status"] == "duplicate":
            continue
        kind = kind_for(item)
        if not only_assessment:
            Path(item["ocr_path"]).write_text(ocr_for(kind), encoding="utf-8")
            Path(item["jd_path"]).write_text(json.dumps(jd_for(item, kind), ensure_ascii=False), encoding="utf-8")
        Path(item["assessment_path"]).write_text(
            json.dumps(assessment_for(item, profile, kind), ensure_ascii=False), encoding="utf-8"
        )


def create_test_image(path, color=(235, 245, 255), size=(800, 1200)):
    image = Image.new("RGB", size, color)
    ImageDraw.Draw(image).text((20, 20), "TEST DATA ONLY", fill="black")
    image.save(path)

"""统一业务核心：原生 Agent 与独立 Hy4 后端共用校验、评分和运行状态逻辑。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import os
import platform
import shutil
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import version as package_version
from pathlib import Path

import jsonschema
from PIL import Image, ImageOps

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = SKILL_ROOT / "assets" / "schemas"
SKILL_VERSION = "1.4.8"
PROMPT_VERSION = "native-v1.4.3-with-pain-point-context"
RULE_ENGINE_VERSION = "profile-aware-experience-and-stress-v5"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
RUN_SUBDIRS = ("sources", "slices", "ocr", "jd", "assessments", "decisions", "logs", "fulltext")
UNKNOWN_VALUES = {"未提及", "未知", "待确认"}
JD_CONTRACT_VERSION = "role-facts-experience-stress-v5"
ASSESSMENT_CONTRACT_VERSION = "profile-fit-and-capability-painpoint-v4"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        Path(tmp_name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        Path(tmp_name).write_text(text, encoding="utf-8")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(data):
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def schema_path(name):
    return SCHEMA_DIR / f"{name}.schema.json"


@lru_cache(maxsize=8)
def _schema_validator(name):
    """Avoid reopening/recompiling schema for every row in a 20k+ JD batch."""
    return jsonschema.Draft202012Validator(read_json(schema_path(name)))


def validate_schema(data, name):
    errors = sorted(_schema_validator(name).iter_errors(data), key=lambda item: list(item.absolute_path))
    if errors:
        details = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:8])
        raise ValueError(f"{name} JSON Schema 校验失败：{details}")


def validate_profile(profile):
    validate_schema(profile, "profile")
    ids = [rule["rule_id"] for rule in profile["hard_reject_rules"]]
    if len(ids) != len(set(ids)):
        raise ValueError("用户画像 hard_reject_rules 存在重复 rule_id")
    soft_ids = [rule["rule_id"] for rule in profile.get("soft_risk_rules", [])]
    if len(soft_ids) != len(set(soft_ids)):
        raise ValueError("用户画像 soft_risk_rules 存在重复 rule_id")
    if set(ids) & set(soft_ids):
        raise ValueError("硬红线和软性规则不能复用同一个 rule_id")
    for rule in profile["hard_reject_rules"]:
        condition = rule.get("structured_condition")
        if not condition:
            continue
        expected = "skill" if condition["type"] == "required_skill" else "role_family"
        if expected not in condition:
            raise ValueError(f"结构化红线 {rule['rule_id']} 缺少 {expected}")


def evaluate_structured_condition(condition, jd):
    """只用模型提取的岗位事实做严格相等匹配，不从出现关键词臆断必须性。"""
    if not condition or jd is None:
        return None
    facts = jd["role_analysis"]
    kind = condition["type"]
    if kind == "required_skill":
        wanted = condition["skill"].strip().casefold()
        for entry in facts["skill_requirements"]:
            if entry["skill"].strip().casefold() == wanted and entry["requirement_level"] == "required":
                return entry["evidence_quote"]
    elif kind == "primary_role":
        family = condition["role_family"]
        if facts["primary_role_family"] == family:
            for entry in facts["responsibility_evidence"]:
                if entry["role_family"] == family and entry["importance"] == "core":
                    return entry["evidence_quote"]
    elif kind == "core_responsibility":
        family = condition["role_family"]
        for entry in facts["responsibility_evidence"]:
            if entry["role_family"] == family and entry["importance"] == "core":
                return entry["evidence_quote"]
    return None


def validate_evidence_quote(quote, ocr_text, label):
    if quote and quote not in ocr_text:
        raise ValueError(f"{label} 的证据不是 OCR 原文子串：{quote[:100]!r}")


def validate_jd_evidence(jd, ocr_text):
    for field, quotes in jd["field_evidence"].items():
        value = jd.get(field)
        if (isinstance(value, str) and value in UNKNOWN_VALUES) or (field == "core_skills" and not value):
            if quotes:
                raise ValueError(f"未知字段 {field} 不应伪造证据")
            continue
        if not quotes:
            raise ValueError(f"字段 {field} 缺少原文证据")
        for quote in quotes:
            if not quote:
                raise ValueError(f"字段 {field} 存在空证据")
            validate_evidence_quote(quote, ocr_text, f"字段 {field}")

    exp = jd.get("experience_resolution")
    if exp is not None:
        if jd["experience"] != exp["effective_experience"]:
            raise ValueError("JD experience 必须与冲突处理后的 effective_experience 一致")
        for key in ("listing_evidence", "detail_evidence"):
            for quote in exp[key]:
                validate_evidence_quote(quote, ocr_text, f"经验来源 {key}")
        if exp["detail_requirement_level"] == "required":
            if not exp["detail_evidence"] or not exp["detail_experience"].strip():
                raise ValueError("JD正文必需年限必须有逐字原文证据")
            if jd["experience"] != exp["detail_experience"] or exp["effective_experience"] != exp["detail_experience"]:
                raise ValueError("JD正文明确年限必须优先于招聘页面筛选栏")
            if not any(quote in jd["field_evidence"]["experience"] for quote in exp["detail_evidence"]):
                raise ValueError("实际经验字段必须引用JD正文必需年限的原文证据")
        elif exp["detail_requirement_level"] == "preferred":
            if not exp["detail_evidence"]:
                raise ValueError("JD正文加分年限须有原文证据")
            if exp["effective_experience"] == exp["detail_experience"] and exp["listing_experience"] != exp["detail_experience"]:
                raise ValueError("JD中的优先经验不得自动上升为必须经验")
        if exp["detail_requirement_level"] == "conflicting":
            if not exp["conflict"]:
                raise ValueError("JD内部年限互相矛盾时必须标记冲突待核实")
        if jd["experience"] not in {"未提及", "未知", "待确认"} and not jd["field_evidence"]["experience"]:
            raise ValueError("经验字段必须提供原文证据")
    facts = jd["role_analysis"]
    for entry in facts["responsibility_evidence"]:
        validate_evidence_quote(entry["evidence_quote"], ocr_text, "岗位实际职责")
    for entry in facts["skill_requirements"]:
        validate_evidence_quote(entry["evidence_quote"], ocr_text, "技能强制程度")
    for quote in facts["seniority_evidence"]:
        validate_evidence_quote(quote, ocr_text, "经验资历")
    for item in facts["expectation_signals"]:
        validate_evidence_quote(item["evidence_quote"], ocr_text, "能力期望信号")
    for item in facts.get("pressure_expectations", []):
        validate_evidence_quote(item["evidence_quote"], ocr_text, "候选人抗压/成长要求")
    if facts["primary_role_family"] != "unknown" and not facts["responsibility_evidence"]:
        raise ValueError("已识别主要岗位类型却未提供实际职责原文证据")



def has_cross_section_depth_support(capability_fit, jd):
    """重大能力缺口的降档需来自职责/任职/加分的两个不同章节，不能仅靠薪资、年限或措辞。"""
    if jd is None:
        return False
    quotes = set(capability_fit["evidence_quotes"])
    def supports_quote(fact_quote):
        # OCR逐字证据允许合理摘录片段，但短泛词（如“AI”）不能冒充跨章节印证。
        return any(fact_quote == quote or
                   (min(len(fact_quote), len(quote)) >= 8 and
                    (fact_quote in quote or quote in fact_quote)) for quote in quotes)

    sections = {
        entry["section"]
        for entry in jd["role_analysis"]["expectation_signals"]
        if supports_quote(entry["evidence_quote"])
        and entry["kind"] in {"algorithm_depth", "engineering_depth", "ownership_depth"}
        and entry["section"] in {"responsibility", "requirement", "bonus"}
    }
    return "requirement" in sections and bool(sections & {"responsibility", "bonus"})

def decide_score(assessment, profile, ocr_text, jd=None):
    """画像驱动的最终分数约束：事实不等于用户否决；所有降档依赖当前画像评估。"""
    validate_profile(profile)
    if assessment["profile_sha256"] != sha256_json(profile):
        raise ValueError("评分画像SHA256与当前画像内容不一致，必须重新生成评估")
    rules = {rule["rule_id"]: rule for rule in profile["hard_reject_rules"]}
    assessed = assessment["redline_assessments"]
    ids = [item["rule_id"] for item in assessed]
    if len(ids) != len(set(ids)) or set(ids) != set(rules):
        raise ValueError(f"红线评估与画像规则不一致：缺少={sorted(set(rules)-set(ids))}，多出={sorted(set(ids)-set(rules))}")

    soft_rules = {rule["rule_id"]: rule for rule in profile.get("soft_risk_rules", [])}
    soft_assessed = assessment["soft_risk_assessments"]
    soft_ids = [item["rule_id"] for item in soft_assessed]
    if len(soft_ids) != len(set(soft_ids)) or set(soft_ids) != set(soft_rules):
        raise ValueError(f"软性风险评估与画像规则不一致：缺少={sorted(set(soft_rules)-set(soft_ids))}，多出={sorted(set(soft_ids)-set(soft_rules))}")

    triggered = []
    soft_triggered = []
    pending = list(assessment["uncertainties"])
    for item in assessed:
        rule = rules[item["rule_id"]]
        quote = item["evidence_quote"]
        validate_evidence_quote(quote, ocr_text, f"红线 {item['rule_id']}")
        auto_quote = evaluate_structured_condition(rule.get("structured_condition"), jd)
        if auto_quote:
            validate_evidence_quote(auto_quote, ocr_text, f"结构化条件 {item['rule_id']}")
        if item["conclusion"] == "true":
            if not quote:
                raise ValueError(f"红线 {item['rule_id']} 被判 true 但缺少 JD 原文证据")
            if rule.get("structured_condition") and jd is not None and not auto_quote:
                raise ValueError(f"结构化红线 {item['rule_id']} 与JD事实强制程度/主要职责冲突，请复核事实层，不能误杀")
            triggered.append({**rule, **item, "origin": "semantic_assessment"})
        elif auto_quote:
            triggered.append({**rule, **item, "conclusion": "true", "evidence_quote": auto_quote,
                              "rationale": "结构化JD事实明确命中画像配置的红线条件；原语义判断未触发",
                              "origin": "structured_facts"})
        elif item["conclusion"] == "unknown":
            pending.append(f"{rule['description']}：{item['rationale']}")
    for item in soft_assessed:
        rule = soft_rules[item["rule_id"]]
        quote = item["evidence_quote"]
        validate_evidence_quote(quote, ocr_text, f"软性风险 {item['rule_id']}")
        if item["conclusion"] == "true":
            if not quote:
                raise ValueError(f"软性风险 {item['rule_id']} true 缺少岗位原文证据")
            soft_triggered.append({**rule, **item})
        elif item["conclusion"] == "unknown":
            pending.append(f"{rule['description']}：{item['rationale']}")

    role_fit = assessment["role_fit"]
    validate_evidence_quote(role_fit["evidence_quote"], ocr_text, "岗位本质匹配")
    if role_fit["conclusion"] != "unknown" and not role_fit["evidence_quote"]:
        raise ValueError("岗位本质匹配不是 unknown 时必须提供原文证据")
    if role_fit["conclusion"] == "unknown":
        pending.append("岗位主要职责与当前画像的契合程度需核实：" + role_fit["rationale"])

    capability_fit = assessment["capability_fit"]
    for quote in capability_fit["evidence_quotes"]:
        validate_evidence_quote(quote, ocr_text, "画像能力门槛对照")
    if capability_fit["conclusion"] == "major_gap" and len(set(capability_fit["evidence_quotes"])) < 2:
        raise ValueError("重大能力差距至少需要两条不同的原文证据，不得由薪资或单个用词推断")
    if capability_fit["conclusion"] == "unknown":
        pending.append("当前画像的能力门槛匹配需核实：" + capability_fit["rationale"])

    for point in assessment["matched_points"] + assessment["gaps"]:
        validate_evidence_quote(point["evidence_quote"], ocr_text, "评分点")
    model_score = assessment["recommendation_score"]
    if model_score >= 4 and len(assessment["matched_points"]) < 2:
        raise ValueError("4–5 分必须至少有两条可追溯的匹配点")

    hard_reject = bool(triggered)
    capped_score = max(2, model_score)
    caps = []
    if role_fit["conclusion"] == "mismatch":
        caps.append({"reason": "当前画像与岗位主要职责明显不匹配", "max_score": 2,
                     "evidence_quote": role_fit["evidence_quote"]})
    if capability_fit["conclusion"] == "major_gap":
        if has_cross_section_depth_support(capability_fit, jd):
            caps.append({"reason": "当前画像相较JD跨章节技术/工程门槛存在重大能力差距（非硬性否决）",
                         "max_score": 3, "evidence_quote": "；".join(capability_fit["evidence_quotes"])})
        else:
            pending.append("模型报告重大能力差距，但未形成任职要求+职责/加分项的跨章节技术证据；需人工核实，不自动降档")
    for item in soft_triggered:
        caps.append({"reason": item["description"], "max_score": item["max_score"],
                     "evidence_quote": item["evidence_quote"], "rule_id": item["rule_id"]})
    for cap in caps:
        capped_score = min(capped_score, cap["max_score"])
    # 可配置的独立偏好：仅当前画像明确选择 weak，且JD事实提取有候选人要求的逐字证据时扣1分。
    # 不修改硬红线判定，不会低于2分；strong/average/未回答均不扣分。
    pressure_evidence = []
    if jd is not None and profile.get("stress_tolerance") == "weak" and not hard_reject:
        pressure_evidence = [fact["evidence_quote"] for fact in jd["role_analysis"].get("pressure_expectations", [])
                             if fact.get("applies_to_candidate") is True and fact.get("kind") in
                             {"strong_stress", "rapid_growth", "steep_learning_curve"}]
        for quote in pressure_evidence:
            validate_evidence_quote(quote, ocr_text, "抗压弱偏好扣分")
    stress_penalty = 1 if pressure_evidence and capped_score > 2 else 0
    final_score = 1 if hard_reject else capped_score - stress_penalty
    final_reason = assessment["recommendation_reason"]
    if hard_reject:
        final_reason += "；[规则校验] 命中当前画像硬性红线，最终1分。"
    elif final_score < model_score:
        final_reason += f"；[规则校验] 基于当前画像的岗位本质/能力门槛/软性约束及抗压偏好，最终限制为{final_score}分。"
    if stress_penalty:
        final_reason += "；[抗压偏好] 用户选择抗压能力弱，JD明确要求候选人高抗压/快速成长，额外降低1分。"
    return {
        "source_sha256": assessment["source_sha256"],
        "profile_id": profile["profile_id"],
        "profile_version": profile["profile_version"],
        "rule_version": profile["rule_version"],
        "rule_engine_version": RULE_ENGINE_VERSION,
        "model_recommendation_score": model_score,
        "recommendation_score": final_score,
        "hard_reject": hard_reject,
        "triggered_rules": triggered,
        "triggered_soft_rules": soft_triggered,
        "role_fit": role_fit,
        "capability_fit": capability_fit,
        "applied_caps": caps,
        "stress_penalty": stress_penalty,
        "stress_evidence": pressure_evidence,
        "recommendation_reason": final_reason,
        "matched_points": assessment["matched_points"],
        "gaps": assessment["gaps"],
        "pending_conditions": sorted(set(pending)),
        "score_trace": {
            "method": "当前画像硬否决=>1；岗位本质mismatch=>最高2；画像重大能力差距且跨章节证据充分=>最高3；显式软规则按画像限制2–4分；其余保留2–5分",
            "model_score": model_score,
            "validated_hard_reject": hard_reject,
            "applied_caps": caps,
            "final_score": final_score,
            "stress_penalty": stress_penalty,
        },
    }


def natural_sort_key(path):
    """稳定自然文件名排序：1、2、10；不依赖文件修改时间。"""
    return [(0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", str(path))]


def ordered_image_inputs(inputs, order_file=None):
    """可选文本清单，每行一张图片路径（相对清单目录），顺序即 Excel 输入顺序。"""
    if not order_file:
        return inputs
    order_path = Path(order_file).expanduser().resolve()
    if not order_path.is_file():
        raise ValueError(f"图片顺序清单不存在：{order_path}")
    entries = [line.strip() for line in order_path.read_text(encoding="utf-8-sig").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if not entries:
        raise ValueError("图片顺序清单为空")
    paths = []
    for entry in entries:
        path = Path(entry).expanduser()
        if not path.is_absolute():
            path = order_path.parent / path
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            raise ValueError(f"图片顺序清单包含不存在或不支持的图片：{entry}")
        paths.append(str(path))
    if len(paths) != len({str(Path(x).resolve()) for x in paths}):
        raise ValueError("图片顺序清单存在重复路径；相同图片内容仍可保留不同文件名")
    return paths


def discover_images(inputs, recursive=True):
    images, errors = [], []
    seen_paths = set()
    for raw in inputs:
        path = Path(raw).expanduser()
        if not path.exists():
            errors.append({"source": raw, "stage": "input", "error": "输入路径不存在"})
            continue
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            candidates = sorted((p for p in iterator if p.is_file()), key=lambda p: natural_sort_key(p.relative_to(path)))
        else:
            errors.append({"source": raw, "stage": "input", "error": "输入不是普通文件或目录"})
            continue
        valid = [p for p in candidates if p.suffix.lower() in IMAGE_EXTS]
        if path.is_file() and not valid:
            errors.append({"source": raw, "stage": "input", "error": "不支持的文件格式，请提供 PNG/JPG/JPEG 等图片"})
        if path.is_dir() and not valid:
            errors.append({"source": raw, "stage": "input", "error": "目录中没有支持的 PNG/JPG/JPEG 等图片"})
        for candidate in valid:
            resolved = candidate.resolve()
            if resolved not in seen_paths:
                seen_paths.add(resolved)
                images.append(candidate)
    return images, errors


def make_image_slices(source_path, target_dir):
    """复用原 Demo：高于3000px分片，最多8片，重叠35px。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as origin:
        image = ImageOps.exif_transpose(origin)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        width, height = image.size
        if height <= 3000:
            return [str(source_path)], width, height
        count = min(8, max(2, math.ceil(height / 2100)))
        step = math.ceil(height / count)
        paths = []
        for index in range(count):
            top = max(0, index * step - (35 if index else 0))
            bottom = min(height, (index + 1) * step + (35 if index < count - 1 else 0))
            piece = image.crop((0, top, width, bottom))
            target = target_dir / f"slice-{index + 1:02d}-of-{count:02d}.png"
            piece.save(target, format="PNG", optimize=True)
            paths.append(str(target))
        return paths, width, height


def _ocr_attempt_path(run_dir, item_id, strategy):
    if strategy not in ("whole_first", "sliced_fallback"):
        raise ValueError("不支持的 OCR 输入策略")
    return Path(run_dir) / "ocr_attempts" / f"{item_id}-{strategy}.json"


def _find_ocr_item(run_dir, item_id):
    root = Path(run_dir).expanduser().resolve()
    manifest = read_json(root / "manifest.json")
    items = [x for x in manifest["items"] if x["item_id"] == item_id]
    if len(items) != 1 or items[0].get("status") == "duplicate":
        raise ValueError("item_id 不存在或属于重复图片，不应再次 OCR")
    return root, manifest, items[0]


def claim_ocr(run_dir, item_id):
    """发起工具调用前使用排他文件抢占：同一图、同一输入策略最多领取一次。

    注意：平台原生 analyzeImage 不受本 Python 代码拦截；Agent 必须遵循单一路径调用。
    """
    root, _, item = _find_ocr_item(run_dir, item_id)
    if Path(item["ocr_path"]).is_file() and Path(item["ocr_path"]).read_text(encoding="utf-8").strip():
        raise ValueError("OCR 已保存，禁止重复调用 analyzeImage，请直接生成 JD")
    if item.get("status") not in ("needs_ocr", "processed"):
        raise ValueError("图片当前状态不需要 OCR")
    strategy = item.get("image_strategy", "whole_first")
    if strategy == "sliced_fallback":
        old = _ocr_attempt_path(root, item_id, "whole_first")
        if old.exists() and read_json(old).get("state") != "failed":
            raise ValueError("整图未记录明确失败，禁止切片重试")
    dest = _ocr_attempt_path(root, item_id, strategy)
    dest.parent.mkdir(parents=True, exist_ok=True)
    entry = {"item_id": item_id, "sha256": item["sha256"],
             "strategy": strategy, "state": "issued", "issued_at": utc_now(),
             "source_paths": item.get("slices") or [item["source_path"]]}
    try:
        fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = read_json(dest)
        raise ValueError(f"已领取OCR调用（状态：{existing.get('state')}），禁止对 {item_id} 同输入重复调用 analyzeImage")
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(entry, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return {"allowed": True, "item_id": item_id, "strategy": strategy,
            "source_paths": entry["source_paths"], "note": "本图本策略只允许一次真实 analyzeImage 调用；工具有返回后立即 ingest；严禁同轮工具双路由"}


def fail_ocr(run_dir, item_id, reason):
    """OCR无可用输出时记录明确失败；不在这里重试模型。"""
    if not reason or not reason.strip():
        raise ValueError("必须记录 OCR 失败原因，如 EMPTY_OUTPUT_FALLBACK")
    root, _, item = _find_ocr_item(run_dir, item_id)
    if Path(item["ocr_path"]).is_file() and Path(item["ocr_path"]).read_text(encoding="utf-8").strip():
        raise ValueError("OCR 已入库，不能再标记识别失败")
    path = _ocr_attempt_path(root, item_id, item.get("image_strategy", "whole_first"))
    if not path.exists():
        raise ValueError("无 OCR 领取记录，禁止编造工具调用失败")
    attempt = read_json(path)
    if attempt.get("state") != "issued" or attempt.get("sha256") != item["sha256"]:
        raise ValueError("OCR 尝试不处于已领取状态，不能反复标记失败")
    attempt.update({"state": "failed", "failed_at": utc_now(), "reason": reason.strip()})
    write_json(path, attempt)
    return {"item_id": item_id, "state": "failed", "strategy": attempt["strategy"],
            "next": "如属于整图失败且实际适合切片，可用 slice-on-failure 更换输入；否则标记人工复核并处理下一张"}


def activate_slice_fallback(run_dir, item_id, reason):
    """整图视觉识别明确失败后，唯一一次启用切片并保存清单；不自动重新识别。"""
    if not reason or not reason.strip():
        raise ValueError("切片回退必须明确提供整图失败原因")
    run_dir = Path(run_dir).expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    matches = [x for x in manifest["items"] if x["item_id"] == item_id]
    if len(matches) != 1:
        raise ValueError("未找到唯一 item_id")
    item = matches[0]
    if item["status"] != "needs_ocr":
        raise ValueError("只有尚未完成OCR的项目可以切片回退")
    if item.get("image_strategy") == "sliced_fallback":
        raise ValueError("已执行过切片回退，禁止对同一输入无差别重复重试")
    previous_attempt = _ocr_attempt_path(run_dir, item_id, "whole_first")
    if previous_attempt.exists() and read_json(previous_attempt).get("state") != "failed":
        raise ValueError("整图识别已发起但未记录失败；先保存真实工具结果，或使用 ocr-fail 记录无可用输出")
    slices, width, height = make_image_slices(item["source_path"], run_dir / "slices" / item_id)
    if len(slices) < 2:
        raise ValueError("原始图片不满足长图切片条件，请检查OCR错误原因")
    item.update({"slices": slices, "width": width, "height": height,
                 "image_strategy": "sliced_fallback", "fallback_reason": reason.strip()})
    write_json(manifest_path, manifest)
    return {"item_id": item_id, "strategy": "sliced_fallback", "slice_count": len(slices), "reason": reason.strip()}


def artifact_paths(run_dir, item_id):
    return {
        "ocr_path": str(run_dir / "ocr" / f"{item_id}.txt"),
        "jd_path": str(run_dir / "jd" / f"{item_id}.json"),
        "assessment_path": str(run_dir / "assessments" / f"{item_id}.json"),
        "decision_path": str(run_dir / "decisions" / f"{item_id}.json"),
    }


def clear_stale_artifacts(item, keep=()):
    """防止 rebuild 或画像变化时误用同目录中的旧生成文件。"""
    mapping = {
        "ocr": "ocr_path",
        "jd": "jd_path",
        "assessment": "assessment_path",
        "decision": "decision_path",
    }
    for name, key in mapping.items():
        if name in keep:
            continue
        path = Path(item[key])
        if path.is_file():
            path.unlink()


def old_artifact_path(old_run_dir, old_item, key, subdir, suffix):
    stored = Path(old_item.get(key, ""))
    if stored.is_file():
        return stored
    candidate = old_run_dir / subdir / f"{old_item['item_id']}{suffix}"
    return candidate if candidate.is_file() else None


def copy_old_artifact(old_run_dir, old_item, key, subdir, suffix, target):
    source = old_artifact_path(old_run_dir, old_item, key, subdir, suffix)
    if not source:
        return False
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return True


def load_previous(run_dir):
    path = run_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def original_reference(image_path, inputs):
    """保留输入序号和安全相对路径，区分不同目录中的同名图片。"""
    resolved = image_path.resolve()
    for index, raw in enumerate(inputs, 1):
        root = Path(raw).expanduser()
        try:
            if root.is_dir():
                relative = resolved.relative_to(root.resolve())
            elif root.is_file() and resolved == root.resolve():
                relative = Path(image_path.name)
            else:
                continue
            return f"input-{index:02d}/{relative.as_posix()}"
        except (OSError, ValueError):
            continue
    return image_path.name


def save_prompt_snapshot(run_dir):
    """将本次实际指令与后端 Prompt 源文件随结果保存，便于独立复现。"""
    relative_files = (
        Path("references/native-workflow.md"),
        Path("references/document-workflow.md"),
        Path("references/scoring-rules.md"),
        Path("references/role-facts-and-profile-policy.md"),
        Path("scripts/backend.py"),
    )
    artifacts = []
    for relative in relative_files:
        source = SKILL_ROOT / relative
        target = run_dir / "prompt_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        artifacts.append({"source": relative.as_posix(), "snapshot": str(target), "sha256": sha256_file(source)})
    return artifacts


def prepare_run(inputs, profile_path, output_dir, reuse_from=None, rebuild=False, recursive=True,
                ocr_model="CodeArts analyzeImage (model version not exposed)",
                semantic_model="CodeArts Agent (runtime selected)", order_file=None):
    if rebuild and reuse_from:
        raise ValueError("--rebuild 与 --reuse-from 不能同时使用：评测重跑必须重新 OCR")
    run_dir = Path(output_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in RUN_SUBDIRS:
        (run_dir / name).mkdir(exist_ok=True)
    # rebuild 表示一次新的正式识别试验，旧 OCR 领取记录不能混入新实验。
    if rebuild and (run_dir / "ocr_attempts").exists():
        shutil.rmtree(run_dir / "ocr_attempts")

    profile = read_json(profile_path)
    validate_profile(profile)
    profile_hash = sha256_json(profile)
    profile_target = run_dir / "profile.json"
    if Path(profile_path).resolve() != profile_target.resolve():
        shutil.copyfile(profile_path, profile_target)

    if order_file:
        inputs = ordered_image_inputs(inputs, order_file)
    images, input_errors = discover_images(inputs, recursive)
    # 新运行开始后清除上次 finalize 在本运行目录内生成的旧工作簿。
    from output_naming import prior_generated_excel
    old_excel = prior_generated_excel(run_dir)
    if old_excel and old_excel.is_file():
        old_excel.unlink()
    # 仍兼容旧版输出的清理流程。
    for stale_name in ("AI岗位筛选汇总.xlsx", "summary.json"):
        stale_path = run_dir / stale_name
        if stale_path.is_file():
            stale_path.unlink()
    previous = None if rebuild else load_previous(run_dir)
    external = load_previous(Path(reuse_from).expanduser().resolve()) if reuse_from and not rebuild else None
    previous_sets = [(run_dir, previous), (Path(reuse_from).expanduser().resolve(), external)] if external else [(run_dir, previous)]
    old_by_hash = {}
    for old_dir, manifest in previous_sets:
        if manifest:
            for old_item in manifest.get("items", []):
                old_by_hash.setdefault(old_item["sha256"], (old_dir, manifest, old_item))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    items, prepare_errors = [], list(input_errors)
    seen_hashes = {}
    for index, image_path in enumerate(images, 1):
        item_id = f"IMG-{index:03d}"
        try:
            digest = sha256_file(image_path)
            input_suffix = image_path.suffix.lower()
            suffix = input_suffix if input_suffix in {".png", ".jpg", ".jpeg"} else ".png"
            source_path = run_dir / "sources" / f"{item_id}{suffix}"
            if image_path.resolve() != source_path.resolve():
                if input_suffix == suffix:
                    shutil.copyfile(image_path, source_path)
                else:
                    # WebP/BMP/GIF/TIFF 短图也必须实际转码，不能只改扩展名；动图取首帧。
                    with Image.open(image_path) as origin:
                        converted = ImageOps.exif_transpose(origin)
                        if converted.mode not in ("RGB", "RGBA"):
                            converted = converted.convert("RGB")
                        converted.save(source_path, format="PNG", optimize=True)
            item = {
                "item_id": item_id,
                "input_index": index,
                "source_file": image_path.name,
                "source_path": str(source_path),
                "original_reference": original_reference(image_path, inputs),
                "sha256": digest,
                **artifact_paths(run_dir, item_id),
                "status": "needs_ocr",
                "duplicate_of": None,
            }
            if digest in seen_hashes:
                item["status"] = "duplicate"
                item["duplicate_of"] = seen_hashes[digest]
                item["slices"] = []
                clear_stale_artifacts(item)
                items.append(item)
                continue
            seen_hashes[digest] = item_id
            # 缓存命中后不再做耗时的长图裁剪；真正 needs_ocr 才创建切片。
            item["slices"] = []
            candidate = old_by_hash.get(digest)
            if candidate:
                old_dir, old_manifest, old_item = candidate
                # rerate 输入取自旧运行的 sources 时保留用户原始文件名和引用。
                if Path(old_item.get("source_path", "")).resolve() == image_path.resolve():
                    item["source_file"] = old_item.get("source_file", item["source_file"])
                    item["original_reference"] = old_item.get("original_reference", item["original_reference"])
                copied_ocr = copy_old_artifact(old_dir, old_item, "ocr_path", "ocr", ".txt", item["ocr_path"])
                compatible_jd = old_manifest.get("jd_contract_version") == JD_CONTRACT_VERSION
                compatible_assessment = old_manifest.get("assessment_contract_version") == ASSESSMENT_CONTRACT_VERSION
                copied_jd = compatible_jd and copy_old_artifact(old_dir, old_item, "jd_path", "jd", ".json", item["jd_path"])
                same_profile = old_manifest.get("profile_sha256") == profile_hash
                copied_assessment = copied_jd and same_profile and compatible_assessment and copy_old_artifact(
                    old_dir, old_item, "assessment_path", "assessments", ".json", item["assessment_path"]
                )
                if copied_ocr and copied_jd and copied_assessment:
                    item["status"] = "ready"
                elif copied_ocr and copied_jd:
                    item["status"] = "needs_assessment"
                elif copied_ocr:
                    item["status"] = "needs_jd"
                if copied_ocr:
                    item["reused_from"] = str(old_dir)
                # 相同图片哈希但文件名可能变化：保持新输入对应关系，避免图片错位。
                if copied_jd and old_item.get("source_file") != item["source_file"]:
                    copied_jd_data = read_json(item["jd_path"])
                    copied_jd_data["source_file"] = item["source_file"]
                    copied_jd_data["ocr_text_ref"] = Path(item["ocr_path"]).name
                    write_json(item["jd_path"], copied_jd_data)
            if item["status"] == "needs_ocr":
                with Image.open(source_path) as img:
                    width, height = ImageOps.exif_transpose(img).size
                # V3: 长图默认整图；只有视觉工具明确失败后才按需切片。
                item.update({"width": width, "height": height, "slices": [str(source_path)],
                             "image_strategy": "whole_first"})
            keep_by_status = {
                "needs_ocr": (),
                "needs_jd": ("ocr",),
                "needs_assessment": ("ocr", "jd"),
                "ready": ("ocr", "jd", "assessment", "decision"),
            }
            clear_stale_artifacts(item, keep_by_status[item["status"]])
            items.append(item)
        except Exception as exc:
            prepare_errors.append({"source": str(image_path), "stage": "prepare", "error": f"{type(exc).__name__}: {exc}"})

    prompt_artifacts = save_prompt_snapshot(run_dir)
    manifest = {
        "manifest_version": "1.0",
        "run_id": run_id,
        "skill_version": SKILL_VERSION,
        "created_at": utc_now(),
        "profile_path": str(profile_target),
        "profile_id": profile["profile_id"],
        "profile_version": profile["profile_version"],
        "profile_sha256": profile_hash,
        "rule_version": profile["rule_version"],
        "jd_contract_version": JD_CONTRACT_VERSION,
        "assessment_contract_version": ASSESSMENT_CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_artifacts": prompt_artifacts,
        "source_inputs": [str(Path(p).expanduser()) for p in inputs],
        "items": items,
        "errors": prepare_errors,
        "model": {"ocr": ocr_model, "semantic": semantic_model},
        "parameters": {"recursive": recursive, "rebuild": rebuild, "reuse_from": reuse_from,
                       "order_file": str(Path(order_file).expanduser().resolve()) if order_file else None},
    }
    write_json(run_dir / "manifest.json", manifest)
    write_json(run_dir / "run_metadata.json", {
        "skill_version": SKILL_VERSION,
        "run_id": run_id,
        "status": "prepared",
        "created_at": manifest["created_at"],
        "model": manifest["model"],
        "prompt_version": PROMPT_VERSION,
        "prompt_artifacts": prompt_artifacts,
        "rule_version": profile["rule_version"],
        "rule_engine_version": RULE_ENGINE_VERSION,
        "parameters": {"inputs": [str(Path(p).expanduser()) for p in inputs], "output": str(run_dir), **manifest["parameters"]},
        "runtime": runtime_versions(),
    })
    write_json(run_dir / "errors.json", prepare_errors)
    return manifest


def runtime_versions():
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "jsonschema": package_version("jsonschema"),
        "Pillow": package_version("Pillow"),
        "openpyxl": package_version("openpyxl"),
    }


def finalize_run(run_dir, ocr_model=None, semantic_model=None, sort_mode="score", include_duplicates=False, keyword=None):
    run_dir = Path(run_dir).expanduser().resolve()
    manifest = read_json(run_dir / "manifest.json")
    profile = read_json(run_dir / "profile.json")
    validate_profile(profile)
    records = []
    errors = list(manifest.get("errors", []))
    reused_count = 0

    for item in manifest["items"]:
        if item["status"] == "duplicate":
            continue
        if item["status"] == "backend_failed":
            errors.append({
                "source": item["source_file"], "item_id": item["item_id"], "stage": "backend",
                "error": item.get("backend_error", "独立后端处理失败"),
            })
            continue
        try:
            ocr_path = Path(item["ocr_path"])
            ocr_text = ocr_path.read_text(encoding="utf-8")
            if not ocr_text.strip():
                raise ValueError("OCR 文本为空")
            jd = read_json(item["jd_path"])
            assessment = read_json(item["assessment_path"])
            validate_schema(jd, "jd")
            validate_schema(assessment, "assessment")
            if jd["sha256"] != item["sha256"] or assessment["source_sha256"] != item["sha256"]:
                raise ValueError("图片 SHA256 与结构化结果不一致")
            if jd["source_file"] != item["source_file"]:
                raise ValueError("图片文件名与 JD 不一致")
            if assessment["profile_id"] != profile["profile_id"]:
                raise ValueError("评分使用了不同的用户画像")
            validate_jd_evidence(jd, ocr_text)
            decision = decide_score(assessment, profile, ocr_text, jd)
            # 增强信息仅用于呈现，不进入 score_trace/降档/红线；引用错误降级显示而非丢掉整行。
            from painpoints import format_pain_point
            decision["pain_point_analysis"] = format_pain_point(
                assessment.get("pain_point_analysis"), ocr_text, profile
            )
            decision.update({"item_id": item["item_id"], "source_file": item["source_file"], "backend": manifest["model"]["semantic"]})
            write_json(item["decision_path"], decision)
            records.append({"item": item, "jd": jd, "decision": decision, "ocr_text": ocr_text})
            if item["status"] == "ready":
                reused_count += 1
            else:
                item["status"] = "completed"
        except Exception as exc:
            errors.append({"source": item["source_file"], "item_id": item["item_id"], "stage": "finalize", "error": f"{type(exc).__name__}: {exc}"})

    # Text-only files may contain repeated rows; dedupe semantic evaluation but retain every original row.
    if include_duplicates or manifest.get("input_mode") == "text_only":
        record_by_id = {record["item"]["item_id"]: record for record in records}
        for item in manifest["items"]:
            if item["status"] == "duplicate":
                target = record_by_id.get(item["duplicate_of"])
                if target:
                    records.append({**target, "item": item})
                else:
                    errors.append({"source": item["source_file"], "item_id": item["item_id"],
                                   "stage": "duplicate", "error": "对应原图处理失败，无法复制有效结论"})
    if sort_mode == "input":
        records.sort(key=lambda record: record["item"].get("input_index", int(record["item"]["item_id"].split("-")[-1])))
    elif sort_mode == "score":
        records.sort(key=lambda record: (-record["decision"]["recommendation_score"], record["item"].get("input_index", 0)))
    else:
        raise ValueError("--sort 只允许 input 或 score")
    from opportunity_audit import evaluate_audit, render_audit_summary
    audit = evaluate_audit(records, profile)
    write_json(run_dir / "audit_report.json", audit)
    write_text(run_dir / "audit_summary.md", render_audit_summary(audit))
    from output_naming import resolve_output_filename, prior_generated_excel
    previous_excel = prior_generated_excel(run_dir)
    excel_path = run_dir / resolve_output_filename(manifest, keyword)
    excel_info = None
    if records:
        from excel import export_excel, validate_excel
        export_excel(excel_path, records, run_dir,
                     fast=(manifest.get("input_mode") == "text_only" and len(records) > 800), audit=audit)
        # User-confirmed post-delivery columns are presentation overlays, never score changes.
        from learning_updates import replay_saved
        replay_saved(run_dir, excel_path)
        excel_info = validate_excel(excel_path, len(records), sort_mode=sort_mode,
                                    expected_images=sum(r["item"].get("input_kind") != "text" for r in records))
        # Only remove an earlier workbook that this same run recorded, after new export succeeds.
        if previous_excel and previous_excel != excel_path:
            previous_excel.unlink(missing_ok=True)

    duplicate_count = sum(item["status"] == "duplicate" for item in manifest["items"])
    total = len(manifest["items"]) + len(manifest.get("errors", []))
    failed = len(errors)
    succeeded = len(records) - reused_count
    finished_at = utc_now()
    try:
        wall_seconds = round((datetime.fromisoformat(finished_at) -
                              datetime.fromisoformat(manifest["created_at"])).total_seconds(), 2)
    except (ValueError, KeyError):
        wall_seconds = None
    # 独立只读分析，不能反向修改任何岗位评分或画像。
    from skill_insights import create_insights
    insights = create_insights(records, profile)
    write_json(run_dir / "skill_insights.json", insights)
    from skill_insights import render_chat_report
    write_text(run_dir / "chat_summary.md", render_chat_report(records, profile, insights)
               + "\n" + render_audit_summary(audit)
               + "\n**想让适合的团队看见你？可输入「让机会找到我」，我会根据高分JD和真实经历生成至多5个作品选题。**\n")
    summary = {
        "chat_summary": str(run_dir / "chat_summary.md"),
        "skill_insights": str(run_dir / "skill_insights.json"),
        "audit_report": str(run_dir / "audit_report.json"),
        "audit_flagged": audit["flagged_total"],
        "run_id": manifest["run_id"],
        "wall_seconds": wall_seconds,
        "skill_version": SKILL_VERSION,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": duplicate_count + reused_count,
        "excel_rows": len(records),
        "input_mode": manifest.get("input_mode", "images"),
        "sort_mode": sort_mode,
        "include_duplicates": include_duplicates,
        "excel": str(excel_path) if records else None,
        "errors": str(run_dir / "errors.json"),
        "status": "partial" if records and errors else ("succeeded" if records else "failed"),
        "excel_validation": excel_info,
    }
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "errors.json", errors)
    write_json(run_dir / "manifest.json", manifest)
    metadata_path = run_dir / "run_metadata.json"
    metadata = read_json(metadata_path) if metadata_path.exists() else {}
    if ocr_model:
        manifest["model"]["ocr"] = ocr_model
    if semantic_model:
        manifest["model"]["semantic"] = semantic_model
    metadata.update({
        "status": summary["status"],
        "finished_at": finished_at,
        "model": manifest["model"],
        "summary": summary,
        "prompt_version": manifest.get("prompt_version", PROMPT_VERSION),
        "rule_version": profile["rule_version"],
        "rule_engine_version": RULE_ENGINE_VERSION,
        "runtime": runtime_versions(),
    })
    write_json(metadata_path, metadata)
    write_json(run_dir / "manifest.json", manifest)
    return summary


def check_run(run_dir):
    from excel import validate_excel
    run_dir = Path(run_dir).expanduser().resolve()
    summary = read_json(run_dir / "summary.json")
    if not summary.get("excel"):
        raise ValueError("没有可检查的 Excel")
    result = validate_excel(Path(summary["excel"]), summary["excel_rows"],
                            sort_mode=summary.get("sort_mode", "score"),
                            expected_images=summary.get("excel_validation", {}).get("images"))
    return {"status": "passed", "excel": summary["excel"], "validation": result}


def bundle_run(run_dir, output=None):
    run_dir = Path(run_dir).expanduser().resolve()
    if not (run_dir / "summary.json").is_file():
        raise ValueError("运行尚未 finalize，不能打包交付结果")
    target = Path(output).expanduser().resolve() if output else run_dir.parent / f"{run_dir.name}-results.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file() and path.resolve() != target.resolve():
                archive.write(path, Path(run_dir.name) / path.relative_to(run_dir))
    return {"status": "created", "zip": str(target), "bytes": target.stat().st_size}


def status_run(run_dir):
    """不依赖模型，统计当前批次分层文件的实时状态，供 Agent 无需反复确认即可继续。"""
    run_dir = Path(run_dir).expanduser().resolve()
    manifest = read_json(run_dir / "manifest.json")
    counts = {key: 0 for key in ("duplicate", "ready", "needs_ocr", "ocr_inflight", "ocr_failed",
                                   "needs_jd", "needs_assessment", "needs_validation", "validated", "backend_failed")}
    pending = []
    blocked = []
    for item in manifest["items"]:
        if item.get("status") == "duplicate":
            stage = "duplicate"
        elif item.get("status") == "backend_failed":
            stage = "backend_failed"
        elif not Path(item["ocr_path"]).is_file() or not Path(item["ocr_path"]).read_text(encoding="utf-8").strip():
            stage = "needs_ocr"
        elif not Path(item["jd_path"]).is_file():
            stage = "needs_jd"
        elif not Path(item["assessment_path"]).is_file():
            stage = "needs_assessment"
        elif Path(item["decision_path"]).is_file():
            stage = "validated"
        else:
            stage = "needs_validation"
        if stage == "needs_ocr":
            claim_path = _ocr_attempt_path(run_dir, item["item_id"], item.get("image_strategy", "whole_first"))
            if claim_path.exists():
                claim = read_json(claim_path)
                if claim.get("sha256") != item["sha256"]:
                    stage = "ocr_failed"  # 危险：同item_id指向不同图片，不自动OCR
                else:
                    stage = "ocr_failed" if claim.get("state") == "failed" else "ocr_inflight"
        counts[stage] += 1
        entry = {"item_id": item["item_id"], "stage": stage, "source_file": item["source_file"]}
        if stage in {"ocr_inflight", "ocr_failed"}:
            blocked.append(entry)
        elif stage not in {"duplicate", "validated"}:
            pending.append(entry)
    return {"run_id": manifest["run_id"], "total": len(manifest["items"]),
            "counts": counts, "pending": pending, "blocked_ocr": blocked,
            "input_errors": manifest.get("errors", [])}


def ingest_batch(run_dir, source_path, dry_run=False):
    """一次 ingest N 张 JD 的 OCR/JD/assessment；文件逐层原子写入、校验后才提交。"""
    run_dir = Path(run_dir).expanduser().resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    profile = read_json(run_dir / "profile.json")
    validate_profile(profile)
    by_id = {item["item_id"]: item for item in manifest["items"]}
    source_path = Path(source_path).expanduser().resolve()
    raw = source_path.read_text(encoding="utf-8-sig")
    if source_path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        decoded = json.loads(raw)
        rows = decoded if isinstance(decoded, list) else decoded.get("results", [])
    if not isinstance(rows, list):
        raise ValueError("batch 输入必须是 JSON 数组、{results:[...]} 或 JSONL")
    outcomes = []
    seen = set()
    for row in rows:
        item_id = row.get("item_id") if isinstance(row, dict) else None
        if not isinstance(item_id, str) or item_id not in by_id or item_id in seen:
            outcomes.append({"item_id": item_id, "status": "rejected", "error": "未知或重复的 item_id"})
            continue
        seen.add(item_id)
        item = by_id[item_id]
        if item["status"] == "duplicate":
            outcomes.append({"item_id": item_id, "status": "rejected", "error": "重复图片应复用原图，不能单独评分"})
            continue
        try:
            allowed = {"item_id", "ocr_text", "jd", "assessment"}
            extra = set(row) - allowed
            if extra:
                raise ValueError(f"出现不支持的批次字段：{sorted(extra)}")
            if not (set(row) - {"item_id"}):
                raise ValueError("至少提供 ocr_text、jd、assessment 之一")
            ocr_text = row.get("ocr_text")
            if ocr_text is not None and (not isinstance(ocr_text, str) or not ocr_text.strip()):
                raise ValueError("ocr_text 必须是非空文本")
            if ocr_text is None and Path(item["ocr_path"]).is_file():
                ocr_text = Path(item["ocr_path"]).read_text(encoding="utf-8")
            if not ocr_text or not ocr_text.strip():
                raise ValueError("缺少有效 OCR：不能写入 JD 或 assessment")
            jd = row.get("jd")
            if jd is not None:
                validate_schema(jd, "jd")
                if jd["sha256"] != item["sha256"] or jd["source_file"] != item["source_file"]:
                    raise ValueError("JD 的 SHA256/source_file 与本图片不一致")
                if jd["ocr_text_ref"] != Path(item["ocr_path"]).name:
                    raise ValueError("JD ocr_text_ref 不一致")
                validate_jd_evidence(jd, ocr_text)
            elif Path(item["jd_path"]).is_file():
                jd = read_json(item["jd_path"])
                validate_schema(jd, "jd")
                validate_jd_evidence(jd, ocr_text)
            assessment = row.get("assessment")
            if assessment is not None:
                if jd is None:
                    raise ValueError("没有有效 JD，不能写入 assessment")
                validate_schema(assessment, "assessment")
                if assessment["source_sha256"] != item["sha256"] or assessment["profile_id"] != profile["profile_id"]:
                    raise ValueError("assessment 与图片或画像不匹配")
                decide_score(assessment, profile, ocr_text, jd)
            if dry_run:
                outcomes.append({"item_id": item_id, "status": "accepted", "next": item["status"]})
                continue
            # 验证成功后再逐层原子替换。若 OCR 或 JD 变更则移除下游陈旧文件。
            if "ocr_text" in row:
                # OCR一次成功后立即落盘；领取记录标记完成，阻止相同输入再次发起。
                write_text(item["ocr_path"], row["ocr_text"])
                attempt_path = _ocr_attempt_path(run_dir, item_id, item.get("image_strategy", "whole_first"))
                if attempt_path.is_file():
                    attempt = read_json(attempt_path)
                    if attempt.get("sha256") == item["sha256"] and attempt.get("state") == "issued":
                        attempt.update({"state": "saved", "saved_at": utc_now()})
                        write_json(attempt_path, attempt)
                if "jd" not in row:
                    Path(item["jd_path"]).unlink(missing_ok=True)
                    jd = None
                if "assessment" not in row:
                    Path(item["assessment_path"]).unlink(missing_ok=True)
                Path(item["decision_path"]).unlink(missing_ok=True)
            if "jd" in row:
                write_json(item["jd_path"], row["jd"])
                if "assessment" not in row:
                    Path(item["assessment_path"]).unlink(missing_ok=True)
                Path(item["decision_path"]).unlink(missing_ok=True)
            if "assessment" in row:
                write_json(item["assessment_path"], row["assessment"])
                Path(item["decision_path"]).unlink(missing_ok=True)
            item["status"] = ("processed" if Path(item["assessment_path"]).is_file() else
                              "needs_assessment" if Path(item["jd_path"]).is_file() else
                              "needs_jd" if Path(item["ocr_path"]).is_file() else "needs_ocr")
            outcomes.append({"item_id": item_id, "status": "accepted", "next": item["status"]})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            outcomes.append({"item_id": item_id, "status": "rejected", "error": str(exc)})
    if not dry_run:
        write_json(manifest_path, manifest)
    accepted = sum(r["status"] == "accepted" for r in outcomes)
    rejected = len(outcomes) - accepted
    # A 26k-row ingest used to reread all 26k OCR files on every chunk.
    # For large batches report cheap manifest counters; full status remains available via status command.
    counts = (status_run(run_dir)["counts"] if len(by_id) <= 1000 and not dry_run else
              dict(__import__('collections').Counter(i.get("status", "unknown") for i in manifest["items"])))
    return {"accepted": accepted, "rejected": rejected,
            "results": outcomes if len(outcomes) <= 1000 else [r for r in outcomes if r["status"] == "rejected"][:200],
            "truncated_results": len(outcomes) > 1000,
            "dry_run": dry_run, "status": counts}


def record_metrics(run_dir, input_tokens=None, output_tokens=None, credits=None,
                   confirmations=None, active_seconds=None, notes=None):
    """仅记录平台实际显示/用户实测值，绝不从图片数虚构 Token 或积分。"""
    run_dir = Path(run_dir).expanduser().resolve()
    path = run_dir / "performance.json"
    info = read_json(path) if path.is_file() else {"measurements": []}
    entry = {"recorded_at": utc_now()}
    for key, val in (("input_tokens", input_tokens), ("output_tokens", output_tokens),
                     ("credits", credits), ("confirmations", confirmations),
                     ("active_seconds", active_seconds), ("notes", notes)):
        if val is not None:
            entry[key] = val
    info["measurements"].append(entry)
    if (run_dir / "run_metadata.json").exists():
        meta = read_json(run_dir / "run_metadata.json")
        created = datetime.fromisoformat(meta["created_at"])
        if meta.get("finished_at"):
            info["wall_seconds"] = round((datetime.fromisoformat(meta["finished_at"]) - created).total_seconds(), 2)
    write_json(path, info)
    return {"path": str(path), "latest": entry, "wall_seconds": info.get("wall_seconds")}

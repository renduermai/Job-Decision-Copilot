#!/usr/bin/env python3
"""Job Decision Copilot 统一执行入口。使用 --help 查看子命令。"""
import argparse
import json
import sys
from pathlib import Path

from onboarding import onboarding_welcome, profile_preview

from core import (
    SKILL_VERSION,
    bundle_run,
    check_run,
    finalize_run,
    prepare_run,
    read_json,
    write_json,
    ingest_batch,
    status_run,
    record_metrics,
    activate_slice_fallback,
    claim_ocr,
    fail_ocr,
)


def add_prepare_arguments(parser):
    parser.add_argument("--input", nargs="+", required=True, help="图片/目录，或不带图片的CSV、Excel、TXT等文档")
    parser.add_argument("--order-file", help="每行一张图片路径，按清单顺序编号，适合黄金集严格对齐")
    parser.add_argument("--profile", required=True, help="用户画像 JSON；普通用户由 Agent 根据聊天生成")
    parser.add_argument("--output", required=True, help="本次运行的独立输出目录")
    parser.add_argument("--keyword", help="本批招聘岗位的关键词；未指定时按上传文档或图片文件夹命名")
    parser.add_argument("--reuse-from", help="历史运行目录，复用 OCR/JD；画像完全一致时也复用评分")
    parser.add_argument("--rebuild", action="store_true", help="评测重跑：忽略历史去重，重新执行 OCR")
    parser.add_argument("--no-recursive", action="store_true", help="不扫描输入目录的子目录")
    parser.add_argument("--ocr-model", default="CodeArts analyzeImage (model version not exposed)")
    parser.add_argument("--semantic-model", default="CodeArts Agent (runtime selected)")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"job-decision-copilot {SKILL_VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("welcome", help="展示面向普通用户的三步引导与三种示例画像")
    preview = commands.add_parser("profile-preview", help="展示已从用户文字识别的画像选项及待补充字段")
    preview.add_argument("--file", required=True, help="Agent从用户文字提取的画像草稿JSON，可不完整")

    learn_preview = commands.add_parser("learn-preview", help="交付后读取职场博主内容，检测现有Excel列并预览高分岗位的更新建议")
    learn_preview.add_argument("--run-dir", required=True)
    learn_preview.add_argument("--text-file", required=True, help="用户提供的小红书点点总结、逐字稿或职场内容文本")
    learn_preview.add_argument("--column", help="候选输出列；不提供时根据内容建议")
    learn_preview.add_argument("--creator", default="", help="用户提供的来源博主名称")
    learn_preview.add_argument("--min-score", type=int, default=4)
    learn_preview.add_argument("--limit", type=int, default=10, help="默认只为前10个高分岗位拟定建议，避免浪费")

    learn_apply = commands.add_parser("learn-apply", help="更新现有Excel派生列；新增列必须先得到用户确认")
    learn_apply.add_argument("--run-dir", required=True)
    learn_apply.add_argument("--file", required=True, help="经Agent结合JD/用户画像修订的learning_draft.json")
    learn_apply.add_argument("--confirm-new-column", action="store_true", help="仅在用户明确确认新增列后使用")

    prepare = commands.add_parser("prepare", help="扫描、校验、复制、分片并生成处理清单")
    add_prepare_arguments(prepare)

    finalize = commands.add_parser("finalize", help="校验分层 JSON、执行评分约束、排序并导出 Excel")
    finalize.add_argument("--run-dir", required=True)
    finalize.add_argument("--keyword", help="输出文件名使用的关键词（覆盖prepare阶段所填关键词）")
    finalize.add_argument("--ocr-model")
    finalize.add_argument("--semantic-model")
    finalize.add_argument("--sort", choices=("score", "input"), default="input", help="默认保持输入顺序；score 为分数降序")
    finalize.add_argument("--include-duplicates", action="store_true", help="保留重复图片为独立Excel行，沿用原图评分")

    ingest = commands.add_parser("ingest", help="一次导入多张图片的 OCR/JD/assessment JSON或JSONL，校验并更新状态")
    ingest.add_argument("--run-dir", required=True)
    ingest.add_argument("--file", required=True, help="由Agent一次写入的批量结果JSON/JSONL文件")
    ingest.add_argument("--dry-run", action="store_true", help="全量预检，只汇总错误，不写入状态或评分文件")

    calibrate = commands.add_parser("calibrate", help="全量评分前从纯文本CSV/Excel按岗位类别抽取150–300条校准样本")
    calibrate.add_argument("--input", nargs="+", required=True)
    calibrate.add_argument("--output", required=True)
    calibrate.add_argument("--per-category", type=int, default=50)

    bulk = commands.add_parser("batch-finalize", help="一次进程完成多个已生成评分目录的finalize和check")
    bulk.add_argument("--runs", nargs="+", required=True)
    bulk.add_argument("--workers", type=int, choices=(1,2), default=1)
    bulk.add_argument("--sort", choices=("input", "score"), default="input")

    merge = commands.add_parser("merge", help="跨公司合并已完成运行，保留JD全文+两列卡点，不重新评分")
    merge.add_argument("--runs", nargs="+", required=True)
    merge.add_argument("--output", required=True)
    merge.add_argument("--min-score", type=int, default=4)
    merge.add_argument("--allow-mixed-profiles", action="store_true")

    cached=commands.add_parser("merge-from-cache", help="直接从此前的汇总JSONL快照重建全量/高分/公司分布三表")
    cached.add_argument("--cache", required=True)
    cached.add_argument("--output", required=True)
    cached.add_argument("--min-score", type=int, default=4)

    split=commands.add_parser("split", help="从一次多文件去重评分运行中导出每个源文件各一份Excel（不重新评分）")
    split.add_argument("--run-dir", required=True)
    split.add_argument("--output-root", required=True)
    split.add_argument("--with-merge", action="store_true", help="同时输出全量/高分/公司分布汇总Excel")

    fallback = commands.add_parser("slice-on-failure", help="整图OCR确实失败后，按需裁剪长图一次")
    fallback.add_argument("--run-dir", required=True)
    fallback.add_argument("--item-id", required=True)
    fallback.add_argument("--reason", required=True, help="整图失败原因；禁止无差别重复尝试")

    claim = commands.add_parser("ocr-claim", help="实际调用analyzeImage前领取唯一OCR调用，避免重复")
    claim.add_argument("--run-dir", required=True)
    claim.add_argument("--item-id", required=True)

    failure = commands.add_parser("ocr-fail", help="工具真实失败/EMPTY_OUTPUT_FALLBACK时记录，禁止原样重试")
    failure.add_argument("--run-dir", required=True)
    failure.add_argument("--item-id", required=True)
    failure.add_argument("--reason", required=True)

    local_ocr = commands.add_parser("ocr-local-batch", help="一次Python命令离线批量OCR，不调用原生analyzeImage（可选安装RapidOCR）")
    local_ocr.add_argument("--run-dir", required=True)
    local_ocr.add_argument("--max-items", type=int, help="只处理前N张待办图（用于小批量试验）")

    status = commands.add_parser("status", help="查看批次进度和每张图还缺什么，不需要再运行prepare")
    status.add_argument("--run-dir", required=True)

    metrics = commands.add_parser("record-metrics", help="写入真实实测积分、Token和人工交互成本")
    metrics.add_argument("--run-dir", required=True)
    metrics.add_argument("--input-tokens", type=int)
    metrics.add_argument("--output-tokens", type=int)
    metrics.add_argument("--credits", type=float)
    metrics.add_argument("--confirmations", type=int)
    metrics.add_argument("--active-seconds", type=float)
    metrics.add_argument("--notes")

    standalone = commands.add_parser("standalone", help="使用原腾讯 Hy4 后端执行完整独立链路")
    add_prepare_arguments(standalone)
    standalone.add_argument("--workers", type=int, choices=(1, 2), default=1, help="OCR+解析并发数，1 为串行回退")
    standalone.add_argument("--retries", type=int, default=0, help="模型调用失败时的额外重试次数")

    rerate = commands.add_parser("rerate", help="复用旧运行的 OCR/JD，仅用新画像重新评分")
    rerate.add_argument("--run-dir", required=True, help="已完成的旧运行目录")
    rerate.add_argument("--profile", required=True, help="新用户画像 JSON")
    rerate.add_argument("--output", required=True, help="新的输出目录，必须不同于旧运行目录")

    check = commands.add_parser("check", help="回读校验已有 Excel")
    check.add_argument("--run-dir", required=True)

    bundle = commands.add_parser("bundle", help="打包画像和全部分层结果，便于沙箱关闭前导出")
    bundle.add_argument("--run-dir", required=True)
    bundle.add_argument("--output", help="结果 ZIP 路径")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "welcome":
            print(onboarding_welcome())
            return 0
        if args.command == "profile-preview":
            print(profile_preview(read_json(args.file)))
            return 0
        if args.command == "learn-preview":
            from learning_updates import preview
            print(json.dumps(preview(args.run_dir,args.text_file,args.column,args.min_score,args.limit,args.creator),ensure_ascii=False,indent=2))
            return 0
        if args.command == "learn-apply":
            from learning_updates import apply
            print(json.dumps(apply(args.run_dir,args.file,confirm_new_column=args.confirm_new_column),ensure_ascii=False,indent=2))
            return 0
        if args.command == "prepare":
            from document_input import is_document_source, prepare_documents
            if is_document_source(args.input):
                manifest = prepare_documents(args.input, args.profile, args.output,
                    args.reuse_from, args.rebuild, args.order_file, args.ocr_model, args.semantic_model)
            else:
                manifest = prepare_run(
                    args.input, args.profile, args.output, args.reuse_from, args.rebuild,
                    not args.no_recursive, args.ocr_model, args.semantic_model, args.order_file,
                )
            if args.keyword:
                manifest["output_keyword"] = args.keyword
                write_json(Path(args.output).expanduser().resolve() / "manifest.json", manifest)
            result = {
                "status": "prepared",
                "run_dir": str(Path(args.output).expanduser().resolve()),
                "manifest": str(Path(args.output).expanduser().resolve() / "manifest.json"),
                "items": len(manifest["items"]),
                "input_errors": len(manifest["errors"]),
                "input_mode": manifest.get("input_mode", "images"),
                "next": ("文本行已保存为OCR等价原文；跳过analyzeImage，直接逐行生成JD/评分后finalize"
                         if manifest.get("input_mode") == "text_only" else
                         "Agent 按 manifest 完成 OCR/JD/评分评估后执行 finalize"),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if manifest["items"] else 2
        if args.command == "finalize":
            summary = finalize_run(args.run_dir, args.ocr_model, args.semantic_model,
                                   args.sort, args.include_duplicates, args.keyword)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["excel_rows"] else 2
        if args.command == "slice-on-failure":
            print(json.dumps(activate_slice_fallback(args.run_dir, args.item_id, args.reason), ensure_ascii=False, indent=2))
            return 0
        if args.command == "standalone":
            from document_input import is_document_source
            if is_document_source(args.input):
                raise ValueError("纯文本CSV/Excel请使用 prepare + 原生语义评估 + finalize；standalone 暂不支持文档")
            prepare_run(
                args.input, args.profile, args.output, args.reuse_from, args.rebuild,
                not args.no_recursive, args.ocr_model, args.semantic_model, args.order_file,
            )
            from backend import process_run
            process_run(args.output, args.workers, args.retries)
            if args.keyword:
                manifest_file = Path(args.output).expanduser().resolve() / "manifest.json"
                m = read_json(manifest_file)
                m["output_keyword"] = args.keyword
                write_json(manifest_file, m)
            summary = finalize_run(args.output, args.ocr_model, args.semantic_model, keyword=args.keyword)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["excel_rows"] else 2
        if args.command == "rerate":
            old_dir = Path(args.run_dir).expanduser().resolve()
            new_dir = Path(args.output).expanduser().resolve()
            if old_dir == new_dir:
                raise ValueError("rerate 的 --output 必须不同于旧运行目录，以保留原评分证据")
            old_manifest = read_json(old_dir / "manifest.json")
            if old_manifest.get("input_mode") == "text_only":
                from document_input import prepare_documents
                docs = old_manifest.get("source_documents", [])
                if not docs or not all(Path(d).is_file() for d in docs):
                    raise ValueError("原始文本表格文件已不可用，请重新上传后再重评分")
                manifest = prepare_documents(docs, args.profile, args.output, str(old_dir))
                from output_naming import resolve_output_filename
                manifest["output_keyword"] = old_manifest.get("output_keyword") or resolve_output_filename(old_manifest)[len("AI岗位筛选_"):-len(".xlsx")]
                write_json(new_dir / "manifest.json", manifest)
                print(json.dumps({"status":"prepared_rerate","run_dir":str(new_dir),
                    "items":len(manifest["items"]), "ocr_reused":True,"input_mode":"text_only",
                    "next":"仅重做新画像的 assessment，禁止使用图像OCR"},ensure_ascii=False,indent=2))
                return 0
            inputs = []
            for item in old_manifest["items"]:
                if item.get("status") == "duplicate":
                    continue
                source = Path(item["source_path"])
                if not source.is_file():
                    matches = list((old_dir / "sources").glob(f"{item['item_id']}.*"))
                    if not matches:
                        raise ValueError(f"找不到 {item['item_id']} 的图片副本，无法建立新运行")
                    source = matches[0]
                inputs.append(str(source))
            if not inputs:
                raise ValueError("旧运行没有可重评分的岗位")
            manifest = prepare_run(inputs, args.profile, args.output, str(old_dir), False, True)
            from output_naming import resolve_output_filename
            manifest["output_keyword"] = old_manifest.get("output_keyword") or resolve_output_filename(old_manifest)[len("AI岗位筛选_"):-len(".xlsx")]
            write_json(new_dir / "manifest.json", manifest)
            result = {
                "status": "prepared_rerate", "run_dir": str(new_dir), "ocr_reused": True,
                "items": len(manifest["items"]), "next": "仅生成新画像的 assessment，随后 finalize；禁止重新 OCR",
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "ocr-claim":
            print(json.dumps(claim_ocr(args.run_dir, args.item_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "ocr-fail":
            print(json.dumps(fail_ocr(args.run_dir, args.item_id, args.reason), ensure_ascii=False, indent=2))
            return 0
        if args.command == "ocr-local-batch":
            from ocr_local_batch import run_local_batch
            print(json.dumps(run_local_batch(args.run_dir, max_items=args.max_items), ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            print(json.dumps(status_run(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command == "ingest":
            result = ingest_batch(args.run_dir, args.file, dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["rejected"] == 0 else 2
        if args.command == "calibrate":
            from bulk_ops import calibrate_documents
            print(json.dumps(calibrate_documents(args.input,args.output,args.per_category),ensure_ascii=False,indent=2))
            return 0
        if args.command == "batch-finalize":
            from bulk_ops import batch_finalize
            result=batch_finalize(args.runs,args.workers,args.sort)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result["status"]=="passed" else 2
        if args.command == "merge":
            from bulk_ops import merge_runs
            result=merge_runs(args.runs,args.output,args.min_score,args.allow_mixed_profiles)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result["status"]=="succeeded" else 2
        if args.command == "merge-from-cache":
            from bulk_ops import merge_from_cache
            print(json.dumps(merge_from_cache(args.cache,args.output,args.min_score),ensure_ascii=False,indent=2))
            return 0
        if args.command == "split":
            from bulk_ops import split_run
            result=split_run(args.run_dir,args.output_root,args.with_merge)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result["status"]=="succeeded" else 2
        if args.command == "record-metrics":
            result = record_metrics(args.run_dir, args.input_tokens, args.output_tokens,
                                    args.credits, args.confirmations, args.active_seconds, args.notes)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "check":
            print(json.dumps(check_run(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command == "bundle":
            print(json.dumps(bundle_run(args.run_dir, args.output), ensure_ascii=False, indent=2))
            return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

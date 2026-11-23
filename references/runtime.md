# 运行、接口与限制

## 原生模式

原生模式由 Agent 调用 `analyzeImage` 和语义能力，Python 只做确定性处理，不需要 `.env` 或第三方 Key。

依赖：Python 3.10+（实际验证环境为 3.12.5）、`requirements.txt`。Agent 应在工作区创建隔离 venv；如果目标客户端没有 Python，当前 Skill 不能宣称全端开箱即用。

```bash
python scripts/run.py prepare --input 图片目录 --profile 用户画像.json --output 输出目录
python scripts/run.py status --run-dir 输出目录
# Agent 依 Schema 生成 batch.json（可含多项 OCR/JD/assessment）
python scripts/run.py ingest --run-dir 输出目录 --file batch.json
python scripts/run.py finalize --run-dir 输出目录 --sort input --include-duplicates
python scripts/run.py check --run-dir 输出目录
```

## 已有 JD 换画像重评分

```bash
python scripts/run.py rerate --run-dir 旧运行 --profile 新画像.json --output 新运行
# Agent 只生成新 assessment，不调用 analyzeImage
python scripts/run.py finalize --run-dir 新运行
```

## 重复与评测

- 默认：同输出目录重复 `prepare`，按 SHA256 和画像内容哈希复用结果。
- `--reuse-from 旧运行`：把历史 OCR/JD 复用到新运行。
- `--rebuild`：忽略历史结果，重新 OCR；与 `--reuse-from` 不同时使用。
- 内容重新压缩或格式改变会产生新的字节级 SHA256，不做语义去重。

## 独立 Hy4 后端

仅开发/回归使用，原生 Skill 工作流不依赖它。

```bash
python -m pip install -r requirements-standalone.txt
cp .env.example .env  # 自行安全填写，不上传
python scripts/run.py standalone --input 图片目录 --profile 用户画像.json --output 输出目录 --workers 3 --retries 2
```

`--workers 1` 为串行回退。Hy4 只生成相同 JD/assessment，最终仍由 `core.py` 评分和 `excel.py` 导出。未提供凭证时该后端标记“未验证”。

## 每次运行的分层产物

- `ocr/IMG-*.txt`：OCR 原始文本。
- `jd/IMG-*.json`：结构化 JD。
- `assessments/IMG-*.json`：模型语义判断。
- `decisions/IMG-*.json`：Python 最终评分与红线决策。
- `manifest.json`：输入、切片、哈希、状态和文件关联。
- `run_metadata.json`：Skill、模型、Prompt、规则、参数和依赖版本。
- `prompt_snapshot/`：本次原生指令、评分规则和后端Prompt源文件快照及SHA256，随结果ZIP导出。
- `AI岗位筛选_[关键词].xlsx`：最终 Excel，原 10 列在前并追加证据字段，按用户输入序排列（可用 `--sort score` 降序）。
- `summary.json`、`errors.json`、`backend_errors.json`（如使用独立后端）。
- `profile.json`：本次已确认画像。
- `sources/`、`slices/`：用于缩略图和复现的输入副本。

```bash
python scripts/run.py bundle --run-dir 输出目录
```

结果 ZIP 用于沙箱关闭前导出和后续复现。网页端官方支持任务产物下载；IDE/桌面端通常写入所选本地工作区。沙箱关闭后的画像和文件保留必须实测，不能依赖记忆或承诺永久保存。

## 平台限制

- 原生 `analyzeImage` 的底层模型名称、批量上限和积分换算未公开；记录实际客户端可见模型，不虚构版本。
- 工具返回不是 Schema 强制 JSON，且可能带状态前缀/HTML 转义，必须经 Agent 清洗和 Python 校验。
- 图片上传限制以客户端为准；IDE 智能体模式官方文档列出单张 10MB、总计 20MB，其他端未统一验证。
- 原生并发方式与原 Hy4 线程池不同，优先逐张隔离错误。

## V1.1 新增命令与性能说明

- `prepare --order-file 图片顺序.txt`：按每行一张图片路径编排原始序号；目录扫描默认自然文件名排序，不依赖文件修改时间。
- `status --run-dir 输出目录`：列出所有缺 OCR / JD / assessment / validation 的图片，用于断点继续。
- `ingest --run-dir 输出目录 --file batch.json`：支持 JSON 数组、`{results:[...]}` 或 JSONL；验证通过才写入该岗位分层文件，非法结果返回拒绝原因。
- `finalize --sort input|score --include-duplicates`：原序导出或分数降序，可选择是否把完全重复的截图也保留为独立行。
- `record-metrics --run-dir 输出目录 --input-tokens ... --output-tokens ... --credits ... --confirmations ... --active-seconds ...`：写入真实性能数据。无法从云端自动获取的 Token/积分不估计。
- Excel缩略图先下采样到最大248×288像素后嵌入；缓存命中时不重切长图。
- 码道原生 `analyzeImage` 调用次数/实际模型 Token、权限弹窗策略由平台决定；Skill 的批量导入只减少确定性文件读写/工具往返，真实提速必须在同设备、同模型、同图片下比较。


### V3.0 运行策略

- `prepare` 仅引用整图，不预先切长图；仅整图失败时运行 `slice-on-failure --run-dir ... --item-id ... --reason ...` 更新清单。
- `standalone --workers` 只允许1、2，默认1；OCR对相同视觉输入不做无差别重复尝试，第一次失败仅对长图采用切片策略重试一次。
- 新环境先一张端到端，再全批次；在同一运行目录保留已成功项，避免二次调用。
- 新JD Schema要求 `experience_resolution`，旧JD不可直接沿用，原OCR缓存可复用。


## V1.4.2 可选本地 OCR 模式（免模型 API Key）

`python -m pip install -r requirements-local-ocr.txt`，随后 `python scripts/run.py ocr-local-batch --run-dir <运行目录>`。
不调用 `analyzeImage`，不需要 Agent 对每张图发工具请求；但依赖安装/环境与OCR质量未在码道真实沙箱验证。

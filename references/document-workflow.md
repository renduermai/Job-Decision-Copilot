# 文本招聘文件与Excel输入（V1.4.5）

## 自动选择输入路线

调用原有 `python "$SKILL_ROOT/scripts/run.py" prepare --input <用户文档.csv或.xlsx> --profile <画像.json> --output <运行目录>`。

- **CSV、TSV、无嵌入图片的XLSX/XLSM**：保留工作表及原行号。每个非空数据行按 `字段名：原单元格内容` 组合为独立的原文（`ocr/IMG-xxx.txt`），不上传任何图片，不运行 `analyzeImage`、RapidOCR，也**不**生成占位图片。`status` 显示这些行处于 `needs_jd`，使用原有语义JD提取与assessment流程；`ingest` 的OCR证据须精确引用行内文本。`.txt/.md` 按一个岗位文本处理。
- **Excel带真实嵌入截图**：自动提取内嵌图片，按工作表、行和锚点列排序，走原有图片识别流程。原始来源写入 `original_reference`。这类文档确有图片，不填写「无」。如果一行既有文字又有截图，当前处理以截图为识别主体，用户应核对行内补充文本是否遗漏。
- 不支持的旧 `.xls`、非图片PDF、Word等应说明需先转换为CSV/XLSX/TXT；不可声称已实现解析。图片和表格混合的多个独立附件，请分批跑，防止行号错位。
- 只读入原始单元格文字和实际嵌入图片，不执行宏、不跟随外部链接；不将空值当作岗位。

## 输出契约

- 无图岗位：`AI岗位筛选_[关键词].xlsx` 的 A列「图片」严格为 **无**，不包含任何人工生成缩略图；其他30列表头保留，只把「推荐等级（1-5分）」调至C列。
- 所有模式：`推荐等级（1-5分）` 是第3列，冻结 `D2`（冻结前三列和标题行），便于滚动查看。原始编号保持来源行顺序；最终按 `finalize --sort input` 或用户要求的 `--sort score` 输出。
- 评分/技能矩阵/卡点原有判断流程完全相同，不能因为来源无图片改变打分。
- 本地转换单元格文本不等于调用 OCR，引导界面应该清楚写明。

## CLI

```bash
python "$SKILL_ROOT/scripts/run.py" prepare --input <text.xlsx|jobs.csv|jobs.tsv|jd.txt> --profile <profile.json> --output <run-dir>
python "$SKILL_ROOT/scripts/run.py" status --run-dir <run-dir>
# 由 Agent 对 needs_jd 条目使用原文字段，生成JD、评分 JSON，经 ingest 写入
python "$SKILL_ROOT/scripts/run.py" ingest --run-dir <run-dir> --file <batch.json>
python "$SKILL_ROOT/scripts/run.py" finalize --run-dir <run-dir> --sort input
python "$SKILL_ROOT/scripts/run.py" check --run-dir <run-dir>
```

纯文本行没有实际照片，不需要也不允许为其生成图像。旧任务的 `rerate` 可以按原文档重新导入并复用 JD，再生成新画像的 assessment。若源表格已丢失，请重新上传。

## V1.4.5 下载文件命名

- `AI岗位筛选_[关键词].xlsx`。关键词优先取用户明确指定的 `--keyword`；没有时取输入 CSV/Excel/TXT 的文件名（去扩展名）。含内嵌图片的 Excel 同样取原始上传文件名；图片目录取目录名。
- 示例：`岗位合集_上海.csv` → `AI岗位筛选_岗位合集_上海.xlsx`；`prepare --keyword "金融Agent"` → `AI岗位筛选_金融Agent.xlsx`。
- 文件名不影响内容、C列推荐等级、冻结 D2、Excel 图片处理或评分。下载时使用 `finalize` 返回的 `excel` 实际路径，不拼写固定旧名称。

# V1.4.6｜19份文件、2万+岗位的大批量工作流

## 解决的实际问题

复盘中出现：19份源文件（16份XLSX + 3份CSV），26,777条源记录，重复约4,077条。Agent在首次全量评分之后两次修改规则、重复 `--force` 并重做 Excel；汇总脚本没有输出JD全文和卡点解法；大表Excel导出花费明显。**这些数字是用户此前一次具体运行的复盘，不是本版本性能实测或保证。**

V1.4.6的改造聚焦执行次序及导出：**先抽样校准、预检，再一次全量评分；跨文件先去重；展示派生与跨文件汇总只从已经落盘的 JD/decision 导出，不允许再次调用评分模型。**

## 推荐：纯文字19份文件，只需一次统一评分

1. **用户画像确认**：沿用 `welcome` / `profile-preview` 和抗压偏好。**先冻结用户画像、硬红线和soft_risk规则**（详见 `boundary-regression-v146.md` 的10类边界样本），不要评分2万条后再发现方向偏差。如果需调阈值，优先通过现有 `profile.json` 的可配置规则及 `rule_version` 管理，避免临时改写Python评分引擎。不可编造复盘中外置 rule_engine.py 的 A/B/C 规则：它不属于本 Skill。
2. **抽样校准**：
   ```bash
   python "$SKILL_ROOT/scripts/run.py" calibrate --input <1.xlsx> <2.csv> ... --output <work>/calibration.jsonl --per-category 50
   ```
   产生一个固定、按产品/运营/算法/数据/工程/其他分类的候选样本（实际数量取决于每类样本数量）。这是待人工检查的校准样本，**不是人工标注金标准，也不能拿它宣称泛化准确率**。重点查看被打4/5分的运营/售前/非AI核心职责岗位和可解释证据，必要时调整画像，**调整后重新评估校准样本，而不是全量重跑**。
3. **一次prepare，多文件跨源精确去重**：
   ```bash
   python "$SKILL_ROOT/scripts/run.py" prepare --input <1.xlsx> <2.xlsx> ... <19.csv> --profile <profile.json> --output <work>/all-19
   ```
   仅适用于无真实嵌入图片的CSV/XLSX。**相同的结构化行文本SHA256只保留一个待评分项**，重复的原始行记录 `duplicate_of`，保留各自原始编号及来源。由于来源文本格式/日期差别，语义相同但文本不同的岗位可能不会去重；**不做模糊去重，以防合并不同JD。** 使用 `status` 查看需要评分的唯一记录。原有图片和含截图Excel流程不受影响。
4. **先对生成的 JSONL 批量预检**：
   ```bash
   python "$SKILL_ROOT/scripts/run.py" ingest --run-dir <work>/all-19 --file <batch.jsonl> --dry-run
   ```
   Schema、证据子串、画像SHA256、红线/降档一致性均检查，不写入任何层文件；一次汇总错误，禁止出现“700条报错才改1条→再跑”的循环。预检通过后，执行相同命令（去掉 `--dry-run`）真正写入。大批次会只返回错误样例及统计，而不在聊天里输出几万条逐行状态。
5. **只finalize一次**：
   ```bash
   python "$SKILL_ROOT/scripts/run.py" finalize --run-dir <work>/all-19 --sort input
   python "$SKILL_ROOT/scripts/run.py" check --run-dir <work>/all-19
   ```
   核心评分语义和1–5分约束不变。纯文本大批次自动使用低内存写入Excel，A列「图片」是 **无**，C列「推荐等级」固定且冻结D2；末尾**新增「JD全文」**（保留原「文字」证据列）。`JD全文` 展示公司/岗位/地点/薪资/学历/经验摘要，后接原始文本的业务字段，包括若提供的部门、发布时间、招聘类型和链接。**对于原文没有的字段不补造**。原文超过Excel单元格上限时会标记截断，分层OCR/原始文件和汇总缓存保留完整内容。
6. **一次拆分19份，再做跨公司Top**：
   ```bash
   python "$SKILL_ROOT/scripts/run.py" split --run-dir <work>/all-19 --output-root <work>/deliverables --with-merge
   ```
   仅阅读已落盘的 `jd/*.json`、`decisions/*.json`、`ocr/*.txt`，不重新模型评分；按源文件回填各自Excel，另外生成「跨公司汇总」：**全量整合表 / 高分岗位 / 公司分布**。全量整合表与高分表都包含「JD全文」「卡点判断」「卡点如何解决」，缺证据则明确「待分析/待核实」，不得静默删列。全量表保留重复原始行，高分表按公司+岗位+地点+JD正文计算唯一岗位并按分数排序。
7. **导出改变时使用 JSONL 缓存**：`split --with-merge` 自动写一份 `.merge_data.jsonl`；改展示方式不重评分、也不用再次遍历19个原运行目录：
   ```bash
   python "$SKILL_ROOT/scripts/run.py" merge-from-cache --cache <work>/deliverables/AI岗位筛选_跨公司汇总.merge_data.jsonl --output <work>/deliverables/新版汇总.xlsx
   ```
   **JSONL是某次评分的快照**；若以后真实改变了原始JD、用户画像、规则或决策结果，必须重新做受影响的分析，不能把旧缓存当最新评分。

## 对已经分开跑完的19个旧run

不需要重建评分，也不需要压回一个run：

```bash
python "$SKILL_ROOT/scripts/run.py" merge --runs <run1> <run2> ... <run19> --output <work>/跨公司汇总.xlsx
```

默认拒绝把**不同画像**的分数直接混在一个排行榜；如确为多画像汇总，须显式 `--allow-mixed-profiles` 并注明不同用户的分数不可直接比较。`merge` 自动产出同名 `.merge_data.jsonl` 缓存供后续 `merge-from-cache` 使用。

如果19个run的 `assessment` 都已生成，但还没有finalize，则先：

```bash
python "$SKILL_ROOT/scripts/run.py" batch-finalize --runs <run1> ... <run19> --workers 1 --sort input
```

默认串行，只有沙箱内存/性能允许才考虑 `--workers 2`，不会改变 Agent OCR 并发规则。

## 卡点列质量要求（不能影响既有评分）

- **原始原文**保存：保留 `ocr_text`，岗位内「JD全文」清理技术索引，保留完整业务字段。
- **卡点不能照搬任职资格**：优先从职责、交付物、团队要解决的实际业务流程中抽取问题；任职条件只用于解释为何需要这类能力，而不是把“熟悉Python/具有沟通能力”本身当企业卡点。
- **卡点和解决方式必须针对同一个问题**，形成“职责/交付难点 → JD原文证据 → 期待方案 → 当前画像已有可验证经历”；没有经历证据时，只能写“岗位期待的解决方向”，不能虚构用户背景。
- 新增卡点字段只影响展示，不影响推荐等级；若已有 `assessment.pain_point_analysis` 没有足够证据，沿用 `format_pain_point` 的「待分析/待核实」保护，不应为了美化汇总而擅自补造内容。
- **大规模全量评分前在校准样本抽查卡点**；如果不合格，先修好生成Prompt，再正式批量执行，避免导出19份后返工。

## 技术边界、验证

- 只依赖本地Python与已配置的 Agent 模型能力；**本 Skill 没有内置可以无API调用完成2.6万条语义评估的独立通用规则引擎**。复盘中临时开发的 `rule_engine.py` 并不属于安装包，不能凭空承诺35–45分钟完成。
- 2026年用户提供的93分基线属于此前黄金集实测；本版**没有修改决定最终分数的 `decide_score`**。新增导出、去重和评测前抽样需要再次验收，不能将“单元测试通过”声称为真实26,777行耗时已达标。
- `JD全文`与卡点内容可能含公司内部信息、个人简历、投递链接，汇总JSONL应默认保存在用户授权目录，不上传公开仓库。

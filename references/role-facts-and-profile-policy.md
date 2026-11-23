# V1.3 岗位事实与用户画像解耦判定契约

> 本文是实际处理规范，不是某个用户的评分模板。不得根据企业名、岗位名或黄金集人工标签写死结果。

## 一、第一遍只读取 JD：事实层（跨画像可复用）

生成 `jd.role_analysis`，**绝对不要参照任何用户的喜好**来改变岗位事实：

- `primary_role_family`：按工作职责、主要交付物、任职要求共同判断；不能只看“AI Agent 工程师”之类的招聘标题。
- `secondary_role_families`：允许岗位同时兼具多种职责。
- `responsibility_evidence`：每条实际工作有 `core`、`supporting`、`unclear` 重要程度，必须给 OCR 原文逐字引用；“与算法团队协作”不能被识别为“核心算法研发职责”。
- `skill_requirements`：把每项技能标为 `required` 必须、`preferred` 优先、`alternative` 多选一、`mentioned` 仅提及、`collaboration` 协作需要、`unknown` 无法判断。每项必须对应原文。
- `seniority_evidence`：原文中出现的工作年限、负责范围、带团队、独立上线等资历/交付门槛。
- `ambiguities`：职责权重不明、学历/年限条件不清、图片文字模糊等，不能编造缺失信息。
- `expectation_signals`：从JD中提取 *事实层* 的隐性能力/资历信号，每条记录 `kind`（algorithm_depth / engineering_depth / ownership_depth / strong_wording / compensation_context / experience_openness）、`section`（header / responsibility / requirement / bonus / other）、原文 `evidence_quote`，如果原文有编号可记 `item_index`。不带用户评分；重复跨章节能力只记录真实出现的引用。上限12条，证据不足则空数组。
- 类型枚举：`agent_application`（业务 Agent 应用）、`agent_platform_infrastructure`（运行时/工具协议/沙箱框架）、`model_algorithm`（底层模型研发）、`backend_engineering`（主要后端工程）、`data_analytics_bi`（数据分析/BI）、`business_ai_automation`（业务 AI 自动化交付）、`mixed`、`other`、`unknown`。

**典型对照：**

| 原文写法 | 应记录的事实 | 不允许的错误推断 |
|---|---|---|
| “熟练掌握C++，编写C++代码” | `C++ / required` | 因出现Agent/Python就忽略必需C++ |
| “了解C++优先” | `C++ / preferred` | 断言C++必须 |
| “Python或Java至少掌握一种” | 这两项为 `alternative` | 断言Java必须 |
| “与后训练算法团队协作” | `model_algorithm / supporting` 或 `collaboration` | 断言主要做算法研发 |
| “参与微调和推理优化” | 模型优化属于正式职责，重要程度按上下文判断 | 仅因出现“微调”就断言算法岗；或因“Agent”就完全忽略 |
| “建设Agent Runtime、沙箱、权限、工具协议” | `agent_platform_infrastructure` 很可能为核心 | 仅因标题 Agent 就按简单应用开发评分 |
| “内容洞察/BI报表为主要交付，Agent是加分” | `data_analytics_bi` 为核心，Agent是辅助 | 误判纯 Agent 研发 |
| “将业务需求转成可上线 Agent，赋能业务专家” | `business_ai_automation` + `agent_application` | 自动判为纯项目管理或纯算法 |

## 一点五、隐性能力/资历门槛——高门槛不能靠单个关键词判断

以一份混合 Agent 岗位为例，按下列步骤交叉核查（**示例不是固定公司或分数模板**）：

1. **职责/要求/加分项跨章节关联**：例如职责的“参与模型推理加速、微调与强化学习”，任职第一条“深刻理解Transformer与LLM原理”，加分项“有模型微调/强化学习/推理优化经验”都涉及模型与算法深度。各条单看未必是强制模型研发职责，合在一起却值得审查岗位期望。
2. **位置是线索，不是权重公式**：优先阅读每个章节第一条，看是否是核心考核；不规定“第一条=最重要”，不忽略后续条款。
3. **用词是强度信号，不能脱离职责**：“扎实编程能力”“深刻理解”“精通”等提示能力门槛；但加分项的“精通”仍是加分项，不自动升级为必需。
4. **薪资和经验信息仅作背景**：记录薪酬范围、“经验不限”等原文。无相同地区/岗位/时期的可靠薪资基准，不得声称高于市场均值或推断工作年限；“不限”不证明资浅，也不证明资深。
5. **严格区分模型研发与工程应用**：“参与微调/推理优化”是任务事实，不必然表示以算法研究为主；“了解Transformer原理”也不等于要训练基础模型。

**画像层**：在 `assessment.capability_fit` 中记录 `aligned / partial / major_gap / unknown`，逐一对照用户已经确认的背景和项目证据，不因简历没出现某技能就认定不会。`major_gap` 必须附至少两条不同OCR原文；Python只在JD事实层可核对这些引用来自「任职要求+职责/加分项」两类章节、并且涉及技术/工程深度时最高限为3分。没有充分跨章节证据则保留待核实，**不会自动降档，更不会自动判1**。硬性否决只能来自画像显式配置并确认触发的红线。

## 二、第二遍使用当前已确认画像：决策层（必须重评）

1. 读取当前 `profile.json` 中的目标岗位、资历、能力证据、偏好、`hard_reject_rules`、可选的 `role_preferences` 与 `soft_risk_rules`。
2. 对 **每条用户实际定义的红线** 输出一个 `redline_assessments`，结论为 `true/false/unknown`、逐字证据和理由。**禁止自行添加全局红线**；不确定时 unknown，不得硬排除。
3. 通过 `role_fit` 对比事实层的核心职责与用户意向，输出 `aligned/partial/mismatch/unknown`。**能力差距、资历偏高并不天然代表“岗位方向mismatch”**，应通过能力差距/软风险处理。`mismatch` 只用于核心工作内容确实明显违背用户职业目标的情况。
4. 对照 `role_analysis.expectation_signals` 与用户已确认能力证据生成 `capability_fit`，先检查跨章节相关性、条款位置、用词强度，再判断能力门槛。不得仅因薪资、经验不限、第一条或加分项得出重大差距；有能力缺口的画像可能降档，能力足够的另一画像不能复用旧结果。
5. 对画像显式列出的 `soft_risk_rules` 每条评估一次；命中时由 Python 按该规则 `max_score` 限制得分。没有该规则的另一画像不受此限制。
6. 硬否决优先：**与用户画像明确冲突的硬红线一旦有有效证据，最后无条件1分**；正向的Agent/RAG/Python技能不能抵消。方向 `mismatch` 最高2分，不作为硬否决；其他软性风险只按用户明确的规则降档。
7. 通过前述约束后再考虑正向匹配与岗位工作价值。禁止因为缺一项未知技能便推断“用户没有该技能”；不得把“3年以上优先”当作3年以上必需。
8. `assessment.profile_sha256` 必须来自当前清单的 `profile_sha256`，不可继承前一用户的结果；否则 Python 将拒绝保存/最终化。

## 三、可选的结构化红线（提高复杂红线漏判的召回）

如果用户确认某条硬红线存在**机器可检查的必要条件**，可在该规则下额外配置：

```json
{
  "rule_id": "cpp_is_required",
  "description": "只有 C++ 是必备技能才排除（C++优先不排除）",
  "structured_condition": {"type": "required_skill", "skill": "C++"}
}
```

受支持的条件：
- `required_skill`：事实层要求该技能且明确 `required`。
- `primary_role`：事实层核心岗位类别等于 `role_family`，有主要职责证据。
- `core_responsibility`：事实层有该岗位类别的 `core` 职责证据，允许混合岗位。

Python 仅在结构化JD事实**明确匹配**时，将原模型漏判的该条红线补判 true；如果语义模型判 true、但事实层与配置的机器可检查条件冲突，则拒绝该条评分并要求复核事实层，以防“C++优先”被错误当作“C++必需”。未使用结构化条件的自定义复杂红线仍由语义模型判断。`structured_condition` 是可选的；复杂的“主要从事Java后端”这类双条件场景优先交给语义判别，**不要把单独出现Java就变成硬否决**。

只有当前用户的画像明确启用该条硬红线，结构化条件才会执行；其他用户画像即使读同一份JD也不会受其影响。

## 四、兼容、缓存及评测

- 同一版本事实层缓存可在不同画像之间复用；但 `assessment` 必须重新生成并绑定当前画像 SHA256。
- 从 V1.1 升级到 V1.2 时可以保留 OCR，但须重新生成含 `role_analysis` 的 JD 和新结构 `assessment`。这仅是旧合同迁移，不是 OCR 缓存失效。
- 升级至V1.3时，由于JD新增 `expectation_signals`、评分新增 `capability_fit`，需要重新生成JD事实与评分，但OCR可复用；V1.3内换画像只需重做assessment，不重复OCR/JD。
- 版本V1.2及之后的画像切换：复用 OCR + 事实层 JD，只重做当前画像 assessment。
- 不能把这7个公开分析的 Bad Case 当作未见过的盲测；请使用新岗位作为独立保留集。
- 正文子串一致只证明引用存在，**不能证明语义解读正确**。自动化测试不能冒充真实多模态准确率；需要实际 V1/V2/盲测进行判定。


## V3.0 招聘页面字段与JD正文冲突：经验优先级

1. 页面结构化筛选栏中的「经验不限」「1–3年」不一定代表岗位的真实最低经验门槛。JD正文中的任职资格、岗位要求、职责描述若明确写「3年以上相关开发经验」作为**必需项**，优先据此计算画像匹配，原页面字段也要保留。
2. 正文「3年以上经验优先」属于 `preferred`，**不允许升级为硬性要求**。正文没有数字则保留页面字段并注明来源；若正文有互相冲突的**必需**年限（且无法明确角色分类），标注 `conflicting`、`conflict=true`，等待核实，不自动取最大值。
3. 新JD结构必须输出 `experience_resolution`: `listing_experience`, `detail_experience`, `detail_requirement_level` (`required/preferred/none/conflicting/unknown`), `effective_experience`, `conflict`, `listing_evidence`, `detail_evidence`, `resolution_reason`；所有原文证据必须是 OCR 逐字子串。`experience` 等于 `effective_experience`，当正文 `required` 时等于 `detail_experience`。
4. 经验门槛是JD事实，不会因为画像而变；是否符合及分数根据当前用户背景、明确红线和资历证据判断。**不能凭年限不符就给所有画像1分**。本规则也不意味着所有JD正文都比页面可信：JD正文含明显口号、非强制加分或内部矛盾时需明确分类。

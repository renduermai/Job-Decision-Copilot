# 高分岗位技能覆盖与单技能模拟（V1.4）

- 统计对象：最终得分4–5的**唯一图片SHA256**岗位，重复截图只计一次。
- 数据来自 `jd.role_analysis.skill_requirements`，只取 `required`、`preferred`，同岗位同技能归一化一次；`alternative`/`mentioned`/`collaboration`不得当必需。
- 覆盖率 `demand_coverage_percent = 该技能在高分岗位中出现的唯一岗位数 / 高分唯一岗位总数`，不是岗位获得概率。
- `confirmed_skills`：只能从用户明确确认的实际能力填入。未说到不等于不会。
- `skills_confirmation_complete=true`：仅当用户确认技能清单足以完成本轮静态模拟时启用。
- 静态模拟：只选择有至少一项明确必需技能的高分JD；假设用户新增一项技能，计算**全部明确必需技能已列在确认清单**的岗位数量增量。不计算软条件、年限或实际offer概率。没有完整确认的技能信息，前后变化都写「待确认」，Top5仅按JD需求提示候选项。
- 仅建议未确认已掌握的技能；使用保守同义词表 `scripts/skill_insights.py`，未能归并的术语保持原名。
- `finalize` 写 `skill_insights.json` 与 `chat_summary.md`。Agent务必把 `chat_summary.md` 的内容**显示在聊天里**，并仅把Excel作为默认下载附件。
- 排名只取实际存在的4–5分岗位，少于10个不得凑数；统计结果不得反向修改评分。

## V1.4.3 卡点判断及对应解法（只读展示）

- `assessment.pain_point_analysis` 按 JD 现实职责推断**可能的**业务/技术卡点，不得把推断宣称为企业内幕。引用 OCR 的逐字原文；`candidate_experience` 只能引用户画像中确认的工作/项目/技能来源，缺失时以 `employer_expectation` 输出岗位期待的解决方式。
- **V1.4.6 卡点质量**：必须从明确职责/交付物推断问题，不把“学历、X年、熟悉、沟通能力”当作团队问题；problem 与 solution 同源、同主题成对生成。记录原始证据来源，无法确定则显示待分析/待核实，不为了填满Excel而编造。
- Python 的 `scripts/painpoints.py` 校验原文和画像能力出处；证据不足显示待分析、待核实，不让分析失败阻断一个已评分岗位。卡点和解决建议不得参加打分、降档、技能矩阵或红线判断。
- Excel末尾追加 `卡点判断`、`卡点如何解决` 两列，保留原28列排序和列号；聊天 Top10 表同步增加这两列，低分岗位仍可在Excel查看。聊天结论只显示现有最多10个高分岗位，不额外生成文档。

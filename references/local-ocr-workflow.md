# 无原生图像工具的 OCR 批处理模式（V1.4.2）

目标是将 OCR 工具调度从 Agent 移至 Python，消除 **该模式** 里 `tool_call` 和直接 `analyzeImage` 的双入口可能。**只保证脚本路径本身不会发出原生工具调用。**

## 使用前提

- 云沙箱 Python 3.10+，能在隔离环境安装 `rapidocr` 和 `onnxruntime`（官方发布的开源本地OCR库和执行引擎）。不要求用户提供第三方大模型API密钥。
- 不把模型权重捆绑进公开Skill ZIP，依赖包首次安装可能下载数十MB并耗时、失败。
- 与码道内置视觉模型的识别准确率可能不同，先抽样检查长图、复杂汉字、岗位技能、工作年限，不要直接复用旧版93分作为新OCR模式成绩。

## 首次使用

```bash
python -m pip install -r "$SKILL_ROOT/requirements-local-ocr.txt"
python "$SKILL_ROOT/scripts/run.py" status --run-dir <运行目录>
python "$SKILL_ROOT/scripts/run.py" ocr-local-batch --run-dir <运行目录> --max-items 1
# 检查 ocr/IMG-xxx.txt 文本是否完整，再运行：
python "$SKILL_ROOT/scripts/run.py" ocr-local-batch --run-dir <运行目录>
python "$SKILL_ROOT/scripts/run.py" status --run-dir <运行目录>
```

**整个 OCR 阶段不调用 `analyzeImage`；不需要 Agent 逐张调工具。**

- 同一图片的同一输入策略通过文件原子领取，只会交给本地 OCR 引擎一次；已有OCR文件跳过。
- 已领取未落盘的图片会被阻塞并列入 `blocked`；如果找到之前的工具文字，使用 `ingest` 原样保存。未确认失败不能删掉锁或原样重试。
- 本地结果立即写入 `ocr/IMG-xxx.txt`，之后沿用旧版 `needs_jd` 和 `needs_assessment` 路径。原有评分程序、Schema、画像和Excel没有变。
- 离线OCR若失败，会保存失败标记，不会自动切换到原生工具。按需人工处理或重新选择明确变化的切片输入。
- 这只是新的OCR数据入口，不是新的语义模型。用户实际得到的JD/评分还需 Agent 根据原版Schema分析OCR；模型语义部分仍需真实运行。

## 已有部分结果怎么恢复

以 IMG-012 已得到 OCR 但未写文件为例：先从已有工具输出原样复制文字到 `recover-012.json`：

```json
{"results":[{"item_id":"IMG-012","ocr_text":"工具真实返回的原文，不得自己编造"}]}
```

```bash
python "$SKILL_ROOT/scripts/run.py" ingest --run-dir <旧运行目录> --file recover-012.json
python "$SKILL_ROOT/scripts/run.py" ocr-local-batch --run-dir <旧运行目录>
```

**注意**：JSON示例中的文字只是占位，绝不能作为真实OCR录入。`ocr_inflight` 不能在丢失真实工具输出时自动清除，否则可能再次发送重复工具请求。

## 为什么不是“Skill里写禁止重复”

Agent原生工具调用发生在Python脚本之外；Skill文本以及此前的 `ocr-claim` 都没有能力拦截模型同时发出的两个原生调用。脚本方式改变了调用通路：原生 `analyzeImage` 退出OCR执行链，只有一次 Python 批处理入口。若平台禁止安装依赖，则本模式不可用，需要平台提供可调用OCR接口或执行前工具拦截才能获得同等强约束。

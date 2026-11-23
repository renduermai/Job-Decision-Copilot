# 原 Demo 复用映射

上游：`https://github.com/renduermai/boss_OCR`，Commit `868bced76c99637e985e05ecc7aea6ee81d5e22e`。

| 上游文件/函数 | 本 Skill 对应实现 | 保留或调整原因 |
|---|---|---|
| `quiker_png2excel.py::sha256_file` | `core.py::sha256_file` | 保留字节级 SHA256 去重；改为参数化路径 |
| `make_vision_parts` 的长图分片逻辑 | `core.py::make_image_slices` | 保留3000px阈值、最多8片、35px重叠；切片落盘供原生工具与Hy4共用 |
| `find_images` | `core.py::discover_images` | 保留文件/目录/递归与常见格式；补充空目录和不支持文件错误 |
| `parse_json_object` | `backend.py::parse_json_object` | 保留代码框和杂文本兼容，供独立Hy4使用；原生结果另经JSON Schema |
| `extract_and_rate` 的硬否决/1分约束 | `core.py::decide_score` | 保留有效硬否决=>1、无硬否决模型1分=>2、其余2–5分；语义判断与最终决策分离 |
| `create_or_load_book`、`append_to_excel` | `excel.py` | 保留原10列在前、缩略图、隐藏去重表、筛选、冻结及原子保存；新增证据列和统一降序 |
| `safe_cell_text` | `excel.py::safe_cell_text` | 保留32767字符处理；OCR/JD使用不同sidecar，修复同名覆盖 |
| 原缩略图 `BytesIO` | `excel.py::attach_thumbnail` | 保留，避免PIL对象生命周期错误 |
| `ThreadPoolExecutor` | `backend.py::process_run` | 仅独立Hy4保留并发；`--workers 1`提供串行回退；原生逐张隔离错误 |
| `models.py::get_tx_model_client` | `models.py` | 只保留实际使用的腾讯OpenAI兼容客户端，惰性加载，删除密钥打印和无关平台 |
| 串行 `png2excel.py` | `standalone --workers 1` | 不在包内维护两份近重复流水线，以同一核心串行执行保留回退能力 |

## 未直接携带的内容

- 硬编码的 `D:\...` 路径和个人简历/原则：改为用户输入及聊天确认画像。
- FastAPI、uvicorn、SQLAlchemy 等未使用依赖：不进入最小依赖。
- 真实 `.env`、密钥、测试用户图片和画像：不进入公共 Skill。
- 黄金评测、LangGraph、数据库、独立云服务：不属于V1.0。

本轮只做封装和平台适配，不声称对上游评分算法进行了优化。

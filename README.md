# 编程记忆开发候选

展示名称 Qianora（千绪），内部代号 Heguang / 包名 heguang_code，本地候选版本 `0.2.0.dev5`，当前云端为固定 dev4。公开 dev3/dev4 固定提交保留。这是独立的 Coding Agent Memory 项目，由当前对话单独开发。文本赛道视为已冻结，不修改其结果、版本或线上服务。

**2026-10-23：编程接口已部署，合成接入检查通过；独立 Coding 申请仍是草稿，官方 Smoke / Full 均未启动。** `SUBMISSION_POLICY.json` 已允许申请、开源、独立部署和 Smoke，继续禁止 Full。当前结果属于公开数据检索代理实验和工程检查，不能当成官方 CAMBench 分数或代码任务解决率。dev5 是部署后的线程配置改进候选，未覆盖旧冻结 ZIP 或改写 dev4 固定 Commit。

## 从哪里开始学习

1. [开发学习文档](docs/开发学习文档.md)：从一次命令、一次返回结果，走完整个 Add/Search 流程。
2. [实验记录](docs/实验记录.md)：基线、有效改进、失败尝试和仍未解决的问题。
3. [数据与复现](docs/数据与复现.md)：如何下载固定版本的公开数据、复现指标、保持留出集不被调参使用。
4. [来源与许可](docs/来源与许可.md)：数据、论文、模型和代码的来源。
5. [上下文选择实验](docs/上下文选择实验.md)：候选池、排序与最终预算的区别，以及扩大上下文的成本。
6. [真实编程案例](docs/真实编程案例.md)：保留首轮失败，区分后续开发通过与盲测成绩。
7. [资源与对照试验](docs/资源与对照试验.md)：免费模型权限现状，外部权限现状及本地模型对照工具。
8. [封存集首次验证](docs/封存集首次验证.md)：7 道任务的首次留出结果及其限制。
9. [大请求内存与并发](docs/大请求内存与并发.md)：降低内存峰值、保持旧请求幂等，以及大请求准入。

10. [本地模型对照](docs/本地模型对照.md)：两轮实际测试均失败，保留草稿与协议修订。
11. [部署准备](deploy/README.md)：独立进程、数据、鉴权、保留期和现有 HTTPS 路由。

12. [容量与元数据压缩](docs/容量与元数据压缩.md)：存储收益、控制组、签名升级与检索一致性。
13. [云端部署实录](docs/云端部署实录.md)：Linux 部署、资源限制、模型启动失败、持久化与实际 16 并发接入结果。
14. [运行时性能对照](docs/运行时性能对照.md)：相同分数下的线程对照、失败的图优化方向，以及本机与云端资源约束的差异。

## 当前实现

- 接收命令、工具输出、历史补丁及原始结构化消息；只按明确的 `tool_call_id` 配对。
- 命令与输出跨 Add、跨进程重启时，仍可在相同用户和会话内配对。冲突或重复身份保持不配对。
- 用户独立 SQLite；原文压缩分块保存，相同内容去重；Search 按原始字符区间恢复内容。
- 正文、文件路径、符号分别检索，再融合排序。`rarity` 配置按语料频率调整技术特征的权重。
- 同步 Add、幂等重试、事务回滚、鉴权、请求体上限、并发限制、保留期清理，以及不占处理名额的 `/health`。

当前 API 不调用 Embedding 或 LLM。学术榜对这两类组件有指定模型；本地 BGE 向量尝试已撤出候选。固定 cross-encoder 重排已在云端启用，此无 LLM/Embedding 配置正在申请材料中如实披露，尚未取得新 Coding 申请的审核结论。[赛事模型规则](https://agentmemoryleaderboard.ai/competition/)

## 本地运行

需要 Python 3.12+、支持 FTS5/contentless_delete 的 SQLite 3.43+。本地验证环境是 Windows；云端已验证 Ubuntu 24.04 / Python 3.12.3 / SQLite 3.45.1。完整规模容量和官方超时条件仍未验证。

在项目根目录运行 PowerShell：

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
$env:HCM_API_KEY = .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:HCM_DATA_DIR = "data/my-coding-run"
$env:HCM_PROFILE = "rarity"
$env:HCM_SESSION_PENALTY = "0"
.venv/Scripts/python.exe -m uvicorn heguang_code.api:create_app --factory --host 127.0.0.1 --port 8072 --workers 1
```

密钥只保存在这个终端的环境变量中；不要输出、截图或提交它。示例绑定本机回环地址。关闭终端服务就会停止。省略 `HCM_PROFILE` 时使用 `rarity` 配置。dev4 索引签名已升级，旧库请使用新目录重建，不会自动迁移。

接口为 `GET /health`、`POST /add`、`POST /search`。后两者接受 `Authorization: Bearer <本机专用密钥>`。`Add` 返回 `success/request_id/user_id/session_id`；`Search` 返回 `data: [{id, content, score}]`。`top_k` 为 1..100；纯检索模式总返回预算为 32,000 字符，启用重排后默认 128,000 字符，因此可能返回更少记录。

## 可选本地重排

完成基础运行后，可安装重排依赖并下载固定模型。模型文件只从公开站点下载一次；服务推理不联网，也不保存查询/片段得分缓存。

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-rerank.lock
.venv/Scripts/python.exe tools/acquire_reranker.py --model-dir C:/aml-models/minilm
$env:HCM_RERANKER_DIR = "C:/aml-models/minilm"
```

在同一终端重启服务即可启用。每次 Search 先建立最多 100 条、有字符上限的内部候选池，重排前 16 条，再按请求的 top_k 和最终字符预算选择完整记录。`HCM_RERANK_THREADS` 允许 1 或 2，默认 2 保留本机兼容；目标云端单核额度建议设置 1，53 项测试与有限分数一致性已通过，完整 API 提速尚未复测。`HCM_RERANKER_WEIGHT` 默认 4，`HCM_RERANK_RESPONSE_CHARS` 默认 128000；实际解题收益尚未证明。模型缺失或摘要不符会拒绝启动。清空模型目录环境变量后重启可恢复纯检索模式。

## 验证

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.lock
.venv/Scripts/python.exe -m pytest -q
```

当前 dev5 的 53 项测试通过；真实单线程构造的 10 例、29 个片段分数及最终排序与冻结旧值完全一致。下述存储/大请求证据来自原版本，未因线程配置修改重跑。大请求试验同时发送两份约 64 MB 的 JSON，原文均完整恢复，采样服务内存峰值从约 695 MiB 降至约 377 MiB。原有较大公开样本逐条恢复了 13,093 条消息、验证 289 组跨 Add 配对；其历史报告是 `results/dev8-capacity-http.json`。最新工程并发复测另存 `dev9` 报告。这些都不是正式全量容量或代码任务解决率。复现命令见学习文档。测试数据、模型权重、数据库和真实凭据不随开发包分发。

## 提交前还缺什么

首次留出验证只有 7 道任务，历史池很小，不能证明大噪声泛化。公开 SymPy 案例首次冻结补丁未通过完整测试，后续开发修复通过，本地 Qwen 量化模型的两轮同模型对照均未解决该案例，尚无记忆提升解题率的证据。外部免费模型权限未取得。完整 Coding 规模与长历史稳定性仍未验证；云端短合成输入接入已验证。完成这些验证前，不能称为已优化完成或一等奖水平。

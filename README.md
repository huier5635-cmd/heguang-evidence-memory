# Heguang Evidence Memory

可复现的 Agent Memory Challenge Add/Search 研究原型。当前已完成本地运行、契约检查及公开数据检索诊断；**已在零元试用服务器完成内部合成接口预检；尚未验证公网接口、未获赛事 Key、未提交正式 Full，也没有官方排名或获奖成绩**。

## 中文学习文档

从[开发与学习手册](docs/开发与学习手册.md)开始，再读[算法与实验学习](docs/算法与实验学习.md)、[本地动手实验](docs/本地动手实验.md)和[部署与运维记录](docs/部署与运维记录.md)。文档对照 v0.1.1 固定源码，区分已验证行为与后续计划；不包含密钥或私有评测数据。

## 方法

- Add 同步完成：原始发言按四句邻域建索引，长发言按有重叠的原始字符区间切分；保留来源、角色和时间。
- 默认无付费模型：词项 BM25 与字符三元组 BM25 做 Reciprocal Rank Fusion；可切换窗口步长、来源头长度及基于原文实体的有限反馈，支持可解释消融。
- `hybrid` 增加 `text-embedding-v4`（1024 维）的密集召回；`research` 另用 `gpt-4o-mini` 按同批 Add 的唯一原文分段提取标签，再按可验证引用映射到相应窗口，不对重叠窗口逐个调用模型。
- Search 返回存储的原始证据，不生成最终答案，不读金标，不包含针对数据集问题编写的查询规则。选项只作为低权重检索线索。
- 每个 `user_id` 使用独立 SQLite 文件；词频统计、向量和记忆均不跨用户共享。`session_id` 组织来源，不限制 Search 的用户内检索。
- 同一 `request_id` + 相同请求体重试无重复写入；相同 ID 使用不同请求体返回 409。模型失败返回 503，完整事务提交前不返回 Add 成功。

## 本地运行（不产生模型费用）

Python 3.11+，建议 3.12。以下命令在仓库目录执行。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe -m pip install 'pytest>=8,<10'
# 在本地生成，仅存在于当前进程环境；不输出到终端。
$env:AML_API_KEY = .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:AML_MODE = 'lexical'
$env:AML_DATA_DIR = 'data/local'
.venv/Scripts/python.exe -m uvicorn aml_memory.api:create_app --factory --host 127.0.0.1 --port 8750 --workers 1 --no-access-log
```

Linux/macOS 可使用 `.venv/bin/python` 并以本地 secret 管理工具设置相同环境变量。只运行一个 Uvicorn worker；多进程需要外部写入锁，当前版本未实现。

## 接口

GET `/health` 无需鉴权，返回运行版本和模式。POST `/add`、`/search` 接受 `Authorization: Bearer`、`Authorization: Token` 或 `X-Api-Key`。

```json
{
  "request_id": "unique-write-id",
  "user_id": "isolated-user-id",
  "session_id": "source-session-id",
  "messages": [{"role": "user", "timestamp": 1704067200000, "content": "Original memory text"}]
}
```

Add 仅在写入完成并可立即检索后返回 HTTP 200，原样回传三个 ID 和布尔 `success: true`。

```json
{"query": "Original question", "user_id": "isolated-user-id", "top_k": 100}
```

Search 返回 `{"data": [{"id": "stable-id", "content": "source evidence", "score": 0.02}]}`，相关性降序，条数不超过 `top_k`；没有该用户的记忆时返回 `{"data": []}`。可选顶层 `options` 是选项文本数组，不含金标。

输入支持文本或官方 `text` / `image_url` 有序数组；图片仅接收 inline Base64 PNG/JPEG/WebP，每图最多10 MiB。原图可原样返回，当前原型**不做视觉理解**，只按 caption 等文本检索，因此多模态接收兼容不等于已验证多模态竞争力。

## 配置与模型

参见 `.env.example`。正式使用必须设置随机 API 密钥。`AML_ALLOW_UNAUTHENTICATED=1` 仅用于本地/公开 Smoke 调试，不用于正式部署。

| 参数 | 默认 | 作用 |
|---|---:|---|
| `AML_MODE` | `lexical` | 无模型 / `hybrid` / `research` |
| `AML_WINDOW_STRIDE` | 2 | 四句窗口的步长，可选2或4 |
| `AML_COMPACT_HEADERS` | 0 | 1时使用紧凑来源标识，完整来源仍存数据库 |
| `AML_BRIDGE_WEIGHT` | 0.15 | 0时关闭原文实体反馈 |
| `AML_MAX_CONCURRENCY` | 4 | 本地并发上限，未经过官方全量容量认证 |
| `AML_CHUNK_CHARS` | 1800 | 长消息切块大小 |
| `AML_OVERLAP_CHARS` | 200 | 长消息块重叠大小 |

所有涉及索引的模式/参数变更请使用新的 `AML_DATA_DIR`。不允许在正式 Full 已冻结的版本上切换配置或更换实现。

0.1.1 将同一 Add 的标签处理改为最多16,000字符的原文分段，模型请求受全局有界并发控制。Search 先召回候选ID再取对应证据；密集召回按256向量批次精确扫描，只保留所需高分ID，避免全量加载用户正文或向量矩阵。仍需真实账户、完整数据和所用机器上的吞吐验证。

独立行为回归使用原始公开LoCoMo前2个样本、各前50道有效证据题，共100题。旧版0.1.0 ZIP与0.1.1使用独立数据库和相同写入标识；无模型词法模式下，全部100题的完整Search数组（ID、内容、次序和分数）完全一致。此结果只验证该固定公开小集的行为保持，不代表全部数据或真实模型模式的质量验证。

`hybrid` 需本地配置 `AML_EMBEDDING_KEY` 和账户对应的 `AML_EMBEDDING_URL`（OpenAI 兼容 base URL）。`research` 还需 `AML_LLM_KEY` / `AML_LLM_URL`。模型名称固定为学术榜要求的 `text-embedding-v4` 与 `gpt-4o-mini`，不自动替换其他模型，不在模型出错时悄悄降级。模型适配层只通过模拟网络测试；**尚未使用真实额度验证**。如使用 Azure 等部署别名网关，须先确保其确实映射官方允许模型且请求格式兼容，当前未声称已支持所有云厂商认证方式。

纯词法模式目前用于本地公开集诊断，不能据此认定已满足学术榜参评资格：官网 Full 清单还写明开源方法在 Add 中 expected to use gpt-4o-mini。已向主办方书面确认“完全不使用任何模型是否可参评及获奖”，答复前不勾选该声明、不提交正式 Full。若必须使用模型，则须开通并真实验证 `research` 模式后再冻结版本。

## 验证与公开数据诊断

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe tools/preflight.py --base-url http://127.0.0.1:8750
.venv/Scripts/python.exe tools/manifest.py
```

已有18项测试覆盖同步契约、幂等与冲突、用户隔离、跨会话召回、持久化、并发重试、长文本、多模态格式、模型失败原子性、向量解析、数据到期清理和满并发时健康检查。新增容量路径检查包括：20条发言的同批标签调用只进行一次、精确引用映射、2,000文档检索只加载命中正文、610个1024维向量的分批精确排序及全局模型并发上限。独立HTTP预检通过；这些测试不消耗官方 Smoke/Full 次数。模型路径的这些测试使用模拟服务，没有证明真实模型质量或官方全量吞吐。

同一公开 LoCoMo-Refined 的1,375道可映射证据题回放结果如下，全部使用无模型词法模式：

下面的完整回放针对0.1.0固定版本；0.1.1的读取方式优化另做固定输入行为回归，不将旧版本结果冒充新版本重跑结果。

| 配置 | 全证据@5 | 全证据@100 | 前8,000字符内全证据 |
|---|---:|---:|---:|
| stride2，实体反馈，完整来源头（默认） | 72.87% | 93.89% | 74.25% |
| stride4，无反馈，完整来源头 | 71.35% | 95.20% | 72.80% |
| stride4，无反馈，紧凑来源头 | 71.49% | 95.05% | 75.85% |

紧凑来源候选提高了高K覆盖与前8k字符证据覆盖，但前5条覆盖略低，因此保留默认配置用于复现，以 `candidate.env.example` 单独记录候选。一次同时改动窗口步长和反馈时，不能将两者的效果单独归因。代理指标只表示标注证据出现，既不保证足够作答，也不验证平台 Answer 准确率。Refined 共1,382问，7问证据无法映射而未纳入上述分母；原始LoCoMo共1,986问，纳入1,973问。本阶段未将公开集按开发/留出拆分，因此消融不构成对新数据的泛化证据。

`tools/public_eval.py` 来自本项目并行研究工作，使用标准库，支持公开 LoCoMo 及 LoCoMo-Refined 数据的检查、无模型基线、HTTP 接口证据回放。

```powershell
$env:AML_MEMORY_SYSTEM_KEY = $env:AML_API_KEY
.venv/Scripts/python.exe tools/public_eval.py api --dataset PATH_TO_LOCOMO_JSON --add-url http://127.0.0.1:8750/add --search-url http://127.0.0.1:8750/search --auth bearer --samples 1 --questions 10 --output run-public.json
```

请先用 `inspect` 确认样本数与证据覆盖；模型模式下公开回放也会消耗模型额度。脚本通过原文/证据ID匹配计算检索代理指标，不生成或评分答案，**不能与 AML 官方 Overall 相比较**。公开结果摘要放在 `results/`，不把整套数据或逐题失败文本打包到仓库。启动其他模式、改变参数或数据时，应使用新的用户命名空间并记录配置和提交哈希。

## 部署与数据处理

提供 `Dockerfile` 与 `compose.yaml`，映射到服务器的回环端口8000；公网部署还需独立 HTTPS 反向代理、固定域名、持久化存储、足够磁盘与稳定网络。官方要求完整部署后再申请 Key，不能仅提交仓库让赛事平台代部署。

- 当前请求大小上限64 MiB；超出明确返回413，不静默截断。单个 Search 响应图片合计超过30 MiB返回422。
- 关闭 Uvicorn access log，不记录消息、问题、模型请求体、密钥或评测数据正文。代理也须关闭请求体/响应体记录。
- 持久库每小时清理29天前的写入，并在启动时清理；生产主机必须保持清理任务运行。服务关闭时也须另行安排主机清理，确保评测数据在30天内删除，禁用长期自动备份/快照。
- 私有正式评测数据仅用于对应评测，不做训练、离线调试分析或共享。此仓库中的所有诊断都使用公开上游数据。
- 尚未进行约3亿Token文本赛道、约18亿Token代码赛道的容量压力测试。不可将默认并发4误写为已证明可承载官方全量的声明。

## 来源与原创性披露

本实现从零编写，没有复制现有参赛系统源码。方法背景包括 BM25、RRF、原始证据邻域检索。公开方法调研参考 [ReFind](https://github.com/imlrz/ReFind) 和 [InvMem / vanilla-rag-memory](https://github.com/wenxiaof345-ctrl/vanilla-rag-memory)；相同技术思想不代表代码复用或成绩复现。本原型增加了用户物理隔离、原子幂等写入、词项/字符双索引、可开关的来源压缩、严格模型配置和可独立运行的公开证据诊断。相较 ReFind，本版本没有实现其 GPT 迭代检索/记笔记机制；相较 InvMem，不采用其公开旧版本的 BGE 配置或旧赛制代部署流程。

公开数据只从作者源获取：[LoCoMo](https://github.com/snap-research/LoCoMo)、[LoCoMo-Refined](https://github.com/mem-eval-suite/LoCoMo_refined)。使用数据须另行遵循其许可证与署名要求；本代码 MIT 许可证不覆盖外部数据。

规则依据（2026-10-20核查）：[比赛](https://agentmemoryleaderboard.ai/competition/)、[接入规则](https://agentmemoryleaderboard.ai/rules)、[API指南](https://agentmemoryleaderboard.ai/api-guide)。本届材料截止2026-10-31 23:59、评测停止2026-11-04 23:59，均为UTC+8；具体资格、奖金及最终结果以官方审核为准。


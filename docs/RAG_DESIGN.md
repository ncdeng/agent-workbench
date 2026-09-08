# RAG 知识检索系统

## 架构总览

CST Agent 的 RAG 系统有三层知识源，通过统一检索接口为 planner 和 optimizer 提供上下文：

```
用户 query
    │
    ▼
┌─────────────────────────────────────────────┐
│           unified_recall / retrieve          │
│         (confidence 加权 + 去重 + 排序)        │
└──────┬──────────┬──────────┬────────────────┘
       │          │          │
       ▼          ▼          ▼
  ┌────────┐ ┌────────┐ ┌────────────┐
  │ 第一层  │ │ 第二层  │ │   第三层    │
  │领域先验 │ │Chroma HNSW│ │  自增长经验  │
  │57条规则 │ │13,242块  │ │ reflection  │
  │conf=1.0│ │bge-base-en│ │StructuredMemory│
  └────────┘ └────────┘ └────────────┘
```

### 第一层：领域先验规则（curated rules）

57 条手写的微带天线设计物理因果（频率公式、阻抗匹配、馈电设计、带宽近似等），写在 `knowledge_base.py` 的 `KNOWLEDGE_ENTRIES` 列表里。confidence=1.0，作为 baseline 始终保留。

这不是 RAG 检索出来的——是工程知识库（curated expert rules），和 EDA-Copilot 的做法一致。

### 第二层：CST 官方文档检索（真正的 RAG）

从 CST Studio Suite 2025 安装目录扫描 4,480 个 HTML 在线帮助文档；3,886 个页面产生有效正文，
切成 13,242 个 chunk。当前工程索引使用 bge-base-en-v1.5，存入 D 盘纯 ASCII 路径下的
ChromaDB cosine HNSW。主接口是结构化 `query_document_knowledge()`；旧的
`query_pdf_knowledge()` 仅作为兼容包装保留。

> **索引状态（2026-08）**：schema v4 collection 为
> `cst_official_docs_bge_base_en_v1`，路径
> `D:\cst_agent_rag_data\chromadb_v7_bge_base_en`。全量构建后由独立 Python 子进程完成
> 冷启动查询，`get_collection_stats()` 返回 healthy/ready、13,242 chunks、模型与 schema 身份。
> 仓库里的旧 numpy 向量文件和 D 盘 v2/v3 损坏索引是历史实验产物，不是生产读取路径。

**作用域约束**：文档 chunk 只能作为"资料"进入 rule/strategy 类检索；
`filter_type="history"`（取运行时学到的经验）时不并入文档库；兼容文本接口使用
`[CST官方文档]` 标明来源，Agent 主链使用结构化 provenance。
手册正文没有 confidence、没有 design_signature，混进"历史经验"会污染来源可信度
（见 `knowledge_base._doc_store_applies`）。

### 第三层：运行时自增长经验（StructuredMemory）

reflection 产出的 lesson/failure 经过 confidence/evidence 门控、project/design_signature 作用域和回滚轮
confidence 压低后，写入 `AgentSession` 持有并持久化的 canonical `StructuredMemory`。旧
`dynamic_entries.json` 只在显式 `include_legacy_dynamic=True` 的迁移兼容路径读取；新 reflection 不再双写。

---

## CST 文档发现与导入过程

### 文档来源

CST Studio Suite 2025 安装目录下有两套文档：

| 来源 | 路径 | 格式 | 文件数 | 特点 |
|------|------|------|--------|------|
| Documentation | `Documentation/*.pdf` | PDF | 11 | 官方手册（高频仿真、入门指南等） |
| Online Help | `Online Help/mergedProjects/**/*.htm` | HTML | 4480 | 在线帮助（VBA 命令、求解器配置、边界条件等） |

### 从 PDF 到 HTML 的转折

**最初尝试 PDF 导入**：用 PyMuPDF（fitz）从 11 个 PDF（60MB）提取文本。结果发现 CST 的 PDF 文档使用多栏布局 + 大量表格和截图，PyMuPDF 提取出的文本**碎片化严重**——每个 chunk 只有 30 个字符（如 `"an waveguide or discrete ports."`），完全无法用于语义检索。

**发现问题**：PDF 的文本提取质量不取决于 chunk_size 参数，而取决于**文档格式本身的 DOM 结构**。PDF 是排版格式，文本流按视觉位置排列，多栏布局下 PyMuPDF 会把左栏第一行和右栏第一行交错提取，破坏语义连续性。

**转向 HTML**：CST 的 Online Help 是 HTML 格式——DOM 结构天然提供段落边界（`<p>`, `<li>`, `<td>`, `<h1>`-`<h6>`）。用 Python 标准库 `html.parser` 写了一个轻量 HTML 正文提取器，剥离 `script/style/nav` 噪声标签，按 block-level tag 分段保留正文。

**质量对比**：

| 查询 | PDF chunk（chunk_size=500） | HTML chunk（chunk_size=1200） |
|------|---------------------------|------------------------------|
| waveguide port setup | 31 chars（碎片） | 166 chars（完整段落） |
| S11 farfield monitor | 78 chars（残句） | 157 chars（完整描述） |

---

## 切分策略

### 分隔符优先的滑窗切片器

使用自研的分隔符优先滑窗切片器（`split_text`，无 langchain 依赖）：

```python
_SEPARATORS = ["\n\n", "\n", "。", "；", ". ", "; "]
```

切片逻辑：
1. 如果文本 ≤ `chunk_size`，整段返回（不切）
2. 否则在 `[start+min_chunk_length, start+chunk_size]` 窗口内从后往前找最优分割点
3. 优先级：段落（`\n\n`）> 换行（`\n`）> 中文句号（`。`）> 英文句号（`. `）> 分号
4. 找到分割点后在该位置切分；找不到则硬切
5. 下一块起点 = 当前结束位置 - `chunk_overlap`；要求 `0 <= overlap < chunk_size`

最小切片长度约束是一次真实修复：旧实现允许在 overlap-only 前缀里选分隔符，导致下一轮只前进
1 个字符。8,493 字符的帮助页曾被切成 210 个近重复块；修复后为 10 块。

### 参数选择

| 参数 | 值 | 理由 |
|------|-----|------|
| `chunk_size` | 1200 | CST 文档的段落通常 200-500 词，1200 字符（约 200 词）能完整包含 1-2 个段落 |
| `chunk_overlap` | 200 | 保证跨 chunk 的上下文连续性（约 30 词的重叠窗口） |

用 `bge-base-en-v1.5` tokenizer 审计全部 13,242 个实际 embedding passage：平均 242 tokens、
p95=416、p99=494，113 个（0.85%）超过 512-token 上限。这个结果说明 1200/200 对 99.15% 的
passage 不会触发截断，但少量长表格/代码块仍需 token-aware fallback；不能声称字符切分完全等价于 token 切分。

### 为什么不用固定大小切割

纯固定大小切割容易在句子中间切断。当前切片器优先在段落/句子边界切割，只有在安全窗口内找不到
分隔符时才硬切；它提高语义完整性，但不声称每个 chunk 都天然等于一个完整语义单元。

---

## HTML 正文提取器

使用 Python 标准库 `html.parser`（无 BeautifulSoup 依赖），按 block-level tag 分段提取：

**跳过的噪声标签**：`script`, `style`, `nav`, `header`, `footer`, `form`, `input`, `select`, `button`

**块级边界标签**：`p`, `li`, `td`, `th`, `h1`-`h6`, `div`, `pre`, `dd`, `dt`,
`blockquote`, `main`, `article`, `section`。`span/code` 作为 inline 内容并入父块，不单独 flush。

**跳过的框架文件**：CST Online Help 的 `wh*.htm`（WebHelp 框架）、`index*.htm`（索引页）、`menu.htm`（导航菜单）等 24 个非内容文件。

每个内容标签结束时，收集的文本如果 ≥ 20 字符就作为一段保留，否则丢弃（过滤导航碎片）。

---

## 切分策略演进

### V1：固定大小切割 + PDF 提取（失败）

**切分方式**：`chunk_size=500, chunk_overlap=80`，从 PDF 逐页提取文本后按固定字符数切割。

**问题**：
1. **PDF 多栏碎片化**：CST 的 PDF 手册使用多栏排版（左栏+右栏），PyMuPDF 按视觉位置提取文本时会把左右栏的第一行交错读取，导致提取出的"句子"实际是两个不相关片段的拼接
2. **chunk 过短**：碎片化文本经过 500 字符切割后，每个 chunk 只有 30 个字符（如 `"an waveguide or discrete ports."`），embedding 向量无法捕捉有效语义
3. **重复 chunk**：`overlap=80` 导致同一句话被切进多个 chunk，检索时返回 3 个几乎相同的结果

**检索质量**：waveguide port 查询返回 `"an waveguide or discrete ports."` × 3，完全不可用

### V2：增大 chunk_size + PDF 提取（仍然失败）

**切分方式**：`chunk_size=1200, chunk_overlap=200`

**问题**：chunk_size 调大了，但 PDF 多栏碎片化的根源没解决——提取出来的文本本身就是残句，切大切小都一样。这验证了一个关键结论：**检索质量的上限由文档提取质量决定，不由 chunk 参数决定**。

### V3：转向 HTML 提取 + 边界感知切分（方向正确，但暴露实现 bug）

**核心改变**：放弃 PDF，转向 CST 的 HTML Online Help（4480 个 `.htm` 文件）。

**为什么 HTML 更好**：
- HTML 有 DOM 结构，`<p>`, `<li>`, `<td>`, `<h1>`-`<h6>` 等标签天然提供段落边界
- 不存在"多栏交错"问题——DOM 树的遍历顺序就是阅读顺序
- 噪声标签（`script/style/nav`）可以精确剥离，不像 PDF 的表格和图片混在文本流里

**HTML 正文提取器**：用 Python 标准库 `html.parser`（无 BeautifulSoup 依赖），按 block-level tag 分段：
- 跳过：`script`, `style`, `nav`, `header`, `footer`, `form` 等噪声标签
- 保留：`p`, `li`, `td`, `th`, `h1`-`h6`, `div`, `pre`, `code` 等内容标签
- 过滤：每个段落 < 20 字符的丢弃（导航碎片）

**切分参数**：`chunk_size=1200, chunk_overlap=200`
- 1200 字符 ≈ 200 词，能完整包含 1-2 个 CST 文档段落
- 200 字符 overlap ≈ 30 词，保证跨 chunk 的上下文连续性
- 边界感知切片器优先在段落边界（`\n\n`）和句子边界（`. `, `。`）切割，只在安全窗口内找不到分隔符时才硬切

V3 解决了 PDF 多栏问题，但后续全量构建暴露两类工程 bug：splitter 在 overlap 区域可能 1 字符步进；
Windows native HNSW 在含中文 persist 路径下没有落完整二进制文件。

### V4：schema v3 全量生产链路

- splitter 增加参数校验和最小前进约束；
- embedding 文本加入规范化 CST 相对路径标题；
- Windows persist 路径强制 ASCII，所有缓存放 D 盘；
- building marker 在模型初始化前写入；
- 构建完成后必须通过独立子进程冷启动查询；
- 查询侧做 CST 中英术语 expansion、最大 cosine 融合和 source-level 去重；
- Planner、Executor、Trace 保留结构化 source/chunk/score。

### V5：schema v4 英文语料契约

- 将中文 `bge-small-zh-v1.5` 降级为历史 baseline，英文 CST passage 改用
  `BAAI/bge-base-en-v1.5`（109M、768 维、512 tokens）；
- 按官方模型契约：passage 不加 instruction，短 query 加
  `Represent this sentence for searching relevant passages:`；
- 中文/混合用户问题先生成英文 retrieval query，翻译层与 embedding 层分开评测；
- 修复官方文档 multi-query 在第一个 query 填满 Top-k 后提前返回、导致后续英文 query 未参与融合的问题；
- collection identity 增加 `document_language` 与 `embedding_query_instruction`；
- 全量构建 3,886 页面、13,242 chunks，CPU 耗时约 43 分 47 秒，独立进程冷启动通过。

**历史检索质量对比（smoke，不是最终 benchmark）**：

| 指标 | V1（PDF, 500） | V3（HTML, 1200） |
|------|---------------|-----------------|
| waveguide port | 31 chars, score N/A | **166 chars, score=0.81** |
| farfield monitor | 78 chars, score N/A | **129 chars, score=0.75** |
| VBA macro | 未命中 | **114 chars, score=0.78** |
| 语义完整性 | 残句拼接 | 完整段落 |
| 重复 chunk | 3 个近似重复 | 去重后独立 |

### 总结：切分策略的三条原则

1. **文档格式 > chunk 参数**：HTML 的 DOM 结构比 PDF 的多栏排版更适合 RAG chunking。调 chunk_size 治标不治本，换文档格式才治本
2. **语义边界切割 > 纯固定大小切割**：边界感知切片器优先在段落和句子边界切，降低语义被截断的概率
3. **噪声过滤 > 全量保留**：HTML 提取器剥离 script/style/nav 噪声标签，过滤 < 20 字符的导航碎片，只保留有语义价值的内容

---

## 检索流程

### 统一检索（unified_recall）

`unified_recall()` 默认只检索 canonical StructuredMemory；显式迁移开关才补充旧 RAG dynamic entries：

1. 从 StructuredMemory `recall_memory` 检索 lesson/failure（embedding 语义排序或 token-overlap 降级）
2. 仅当 `include_legacy_dynamic=True` 时，从旧 RAG `retrieve_antenna_rules(filter_type="history")` 读取迁移数据
3. 合并、按文本前缀 bigram Jaccard 相似度 > 0.7 去重
4. 按 score 排序取 top-k

### 规则检索（retrieve_antenna_rules）

`retrieve_antenna_rules()` 保留硬编码规则 + 官方文档的兼容接口；Agent 主链另取结构化文档 hits：

1. 从 `KNOWLEDGE_ENTRIES` 检索硬编码规则（confidence-weighted cosine）
2. 从 ChromaDB 对原 query 和 CST 英文术语 query 多路检索（cosine overfetch）
3. 按最大 cosine 融合并按 source 去重；兼容文本结果带 `[CST官方文档]` 前缀
4. embedding 失败时降级到关键词 token-overlap 匹配

### 评估

20 case ablation（`benchmarks/reports/agent_ablation_fake_cst_20case_diverse.json`，
provider = `deterministic_proxy`，**未调用真实 LLM**）：

| 组 | 成功率 | 说明 |
|------|-----|------|
| heuristic_only | 70% | 纯启发式策略 |
| algorithm_baseline | 100% | 局部候选搜索 |
| llm_no_memory | 100% | LLM 形状的提案器，**无记忆** |
| llm_with_memory | 100% | 同上 + 记忆 |

| 其他指标 | 值 | 说明 |
|------|-----|------|
| memory_enforced_rate | 0.0 | 20/20 case 中记忆未强制改写过任何提案 |
| memory_recall_hit_rate | 0.10 | 召回命中率 |
| PDF chunk 质量 | 31 chars | PyMuPDF 多栏碎片化 |
| HTML chunk 质量 | 166 chars | DOM 段落边界提取 |

**诚实结论**：70% → 100% 的差距来自"启发式 vs LLM 形状的提案器"，**不是记忆的贡献**——
`llm_no_memory` 同样是 100%，且 `memory_enforced_rate = 0.0` 说明这批 case 里记忆一次都没生效。
把这个 delta 说成"记忆带来的提升"是可以被仓库里的 JSON 直接证伪的。

记忆价值应引用真实 LLM 报告（`*_deepseek_20case.json`：with-memory 组
`memory_recall_hit_rate=1.0`、`memory_enforced_rate=1.0`，但三组成功率同为 100%，
即在该难度下记忆未改变最终结果）。

> 历史 keyword fallback 回归位于 `tests/test_rag_eval.py::TestExtendedAnnotatedCases`：
> 10 对手写 query-keyword、非 embedding，只能证明降级机制。当前正式检索证据是下文 30 条
> frozen held-out v1；两类测试不能混用，也不要把关键词回归表述为 embedding Recall。

新增的官方文档 development pilot 位于 `benchmarks/rag_official_docs_cases.json`，12 条覆盖中文、英文、
中英混合 query。真实结果：

| 配置 | Recall@3 | MRR | 中文 Recall@3 | provenance | 重复来源槽位 | p95 |
|---|---:|---:|---:|---:|---:|---:|
| 原始 BGE-small | 0.500 | 0.389 | 0.000 | 1.000 | 0.028 | 35 ms |
| multilingual MiniLM | 0.417 | 0.278 | 0.000 | 1.000 | 0.028 | 69 ms |
| schema v3 领域适配 BGE-small | 0.500 | 0.417 | 0.333 | 1.000 | 0.000 | 38 ms |

schema v4 进一步把候选召回和 query 转换分开测量：

| query 模式 | Recall@3 | Recall@5 | Recall@10 | MRR@3 | 说明 |
|---|---:|---:|---:|---:|---|
| 原始用户 query + 术语 fallback | 0.750 | 0.750 | 1.000 | 0.653 | 中文问题仍受 fallback 完整性限制 |
| 控制英文 retrieval query | 0.917 | 1.000 | 1.000 | 0.778 | Top-10 候选已全召回，优先做 rerank |

pilot 已参与迭代，只能作为开发集；控制英文 query 也不是对真实 LLM 翻译质量的证明。

### 冻结 held-out v1 与 cross-encoder

`benchmarks/rag_official_docs_heldout_v1.json` 在查看检索排名前按“先选文档、后写问题”的方式冻结，含 30 条：
中文、英文、中英混合各 10 条，使用 0–3 graded qrels，并与 12 条 development pilot 的目标来源零重叠。
评测器额外计算 nDCG、hard-negative intrusion、family duplicate、provenance 与 warm latency；同一来源的重复
chunk 不会重复获得 DCG gain，nDCG 不可能因重复 chunk 大于 1。

生产检索顺序为：

```text
英文 retrieval query
→ BGE-base dense Top-20 chunks（保留同 source 多 chunk）
→ English cross-encoder rerank
→ 最后按 source 去重
→ Top-3 注入 Planner / Executor / Trace
```

`score` 始终保留 cosine 语义；观测字段另存 `dense_score/rerank_score/rank_before/rank_after/
reranker_model`。模型加载和预测失败时按 dense 顺序降级，不能让 Agent 因可选 reranker 失效。

冻结 held-out v1、控制英文 retrieval query 的 canonical 复跑结果（2026-08-10）：

| 排序器 | Recall@3 | MRR | nDCG@3 | warm mean | warm p95 | rerank fallback |
|---|---:|---:|---:|---:|---:|---:|
| dense BGE-base | 0.900 | 0.750 | 0.754 | 56.41 ms | 63.29 ms | — |
| MiniLM-L6 cross-encoder | **0.967** | **0.794** | **0.817** | 741.21 ms | 1019.50 ms | 0/30 |
| BGE-reranker-base | 0.967 | 0.772 | 0.799 | 4477.33 ms | 6328.98 ms | 0/30 |

MiniLM 在质量上略优于 BGE-reranker-base，本轮 CPU p95 约为后者的 16%，所以选为生产默认。延迟是
同机当次负载下的观测值，不作为跨机器稳定常数；检索质量在重跑中与旧报告完全一致。胜出模型的
Recall@5=0.967、Recall@10=1.000；这说明 Top-20 候选池足够，剩余主要问题在 Top-3 排序和 qrel 覆盖。
数据集 SHA、索引契约和三套参数固定在 `benchmarks/rag_official_canonical.json`；报告见
`benchmarks/reports/rag_official_docs_heldout_v1_*.json`。正式复现命令为：

```powershell
python -m benchmarks.rag_official_eval --preset heldout_v1_dense_top3 --output benchmarks/reports/rag_official_docs_heldout_v1_dense_top3.json
python -m benchmarks.rag_official_eval --preset heldout_v1_minilm_top3 --output benchmarks/reports/rag_official_docs_heldout_v1_minilm_top3.json
python -m benchmarks.rag_official_eval --preset heldout_v1_bge_reranker_base_top3 --output benchmarks/reports/rag_official_docs_heldout_v1_bge_reranker_base_top3.json
```

preset 会在检索前校验数据集逐字节 SHA 和 collection/chunk/model/query-instruction identity。该集合由开发者
可见，`blinded=false`；“frozen”表示字节身份固定，不代表人工双盲。

### Agent 有据性

`benchmarks/agent_rag_groundedness_eval.py` 使用真实 `CSTAgent.chat()`、真实 Planner/translation/RAG/Executor/Trace，
只把 CST controller 置为无副作用离线模式。它同时测确定性 citation/provenance/trace 指标，并用“只允许依据
检索片段”的 LLM judge 统计 claim support。全部 session、模型和临时文件重定向到 D 盘。

development 3 条首先暴露 Executor 会在正确证据外补常识：有据性 0.697、unsupported claim rate 30.8%。
收紧“只陈述片段直接支持内容、证据不足明说、逐句引用”契约后，复测为 0.900 和 7.7%。冻结后的 held-out
Agent 子集按数据顺序取每种语言前 2 条，共 6 条，不按结果挑题：

| 指标 | 结果 |
|---|---:|
| Agent 完成率 | 6/6 |
| qrel retrieval Recall@3 | 0.833（5/6） |
| citation presence / precision | 1.000 / 1.000 |
| provenance / Trace completeness | 1.000 / 1.000 |
| 有据性 / answer relevance | 0.863 / 0.883 |
| unsupported claim rate | 0.172 |
| Agent E2E latency mean / p95 | 37.0 s / 60.8 s |

这组 `0.863/0.172` 是旧 v1 报告的**单次、未校准 LLM judge 开发诊断**，不是人工金标准，也不作为
对外的定量效果主张。v2 runner 已把 judge 输出升级为逐条原子 claim，并对 claim 数量、支持标签、Top-3
证据引用和有据性比例做 fail-closed 校验；报告同时绑定 dataset/code/execution-contract、answer、evidence、
judge prompt/raw response 的 SHA。真实 Terra v2 连续 6-case run 中 Agent 完成 6/6，但 judge schema 仅 5/6
有效；有效的 55 claims 上 宏平均有据性=0.766、unsupported=17/55（30.9%）。citation presence/precision
可在 6/6 保存的 answer/evidence 上复算为 1.000；旧失败行未保存完整 qrel/provenance/Trace 布尔值，所以这三项
只能报告 2 条可验证 denominator，不能沿用旧版“6/6 都为 1.000”的宽泛表述。

`benchmarks/agent_rag_claim_review.py` 可从 offline-revalidated 报告导出隐藏机器 verdict/reason 与实验字段的
review pack，并在两名独立 reviewer + adjudicator 完成标注后计算 claim-level Cohen's kappa、
precision/recall/F1 和逐回答有据性误差。当前 D 盘 A/B pack 均含 55 个空白 judgment，绑定同一
`pack_id`、`rubric_sha256` 并用 SHA-bound permutation 打散源报告顺序；自然 case/claim ID 与源身份仍可见，所以准确口径是
verdict-blind 而不是完全 provenance-blind。尚无真实双人独立标注产物，因此只能说“校准基础设施已完成”，
不能说 judge 已校准。完整流程见 `docs/HUMAN_EVALUATION_RUNBOOK.md`。

注意：未命中 qrel 的 farfield-source case 实际召回了 analytical-farfield macro 与 VBA FarfieldSource 文档，
答案也得到 0.87 有据性；这提示单一正例 qrel 可能不够穷尽替代相关文档。正式报告不因此回改 qrels。

---

## 配置

```bash
# 导入 CST 文档（一次性操作）
python -m cst_agent_workbench.rag.pdf_ingest \
  --pdf-dir "D:\Program Files (x86)\CST Studio Suite 2025\Documentation" \
  --html-dir "D:\Program Files (x86)\CST Studio Suite 2025\Online Help\mergedProjects" \
  --reset --chunk-size 1200 --chunk-overlap 200

# 环境变量（可选）
export EMBEDDING_PROVIDER=local                             # local=sentence-transformers, openai=OpenAI API
export MEMORY_RECALL_MIN_SCORE=0.30                         # 记忆检索最低 cosine 分数
export RAG_LESSON_MIN_CONFIDENCE=0.6                        # lesson 入库最低置信度
export RAG_RERANK_ENABLED=true                              # 官方文档 cross-encoder 精排
export RAG_RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L6-v2
export RAG_RERANK_CANDIDATE_K=20
export RAG_RERANK_CACHE_DIR=D:\cst_agent_rag_data\reranker_models
```

Windows 上建议先构建到新的 staging 目录和 collection，验证 `/api/rag/status` 与
`/api/rag/query` 后再写入 `.env`，避免先删除旧索引：

```powershell
$env:CHROMA_PERSIST_DIR = "D:\cst_agent_rag_data\chromadb_v7_bge_base_en"
$env:CHROMA_COLLECTION_NAME = "cst_official_docs_bge_base_en_v1"
$env:EMBEDDING_LOCAL_MODEL = "BAAI/bge-base-en-v1.5"
$env:EMBEDDING_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages:"
python -m cst_agent_workbench.rag.pdf_ingest `
  --html-dir "D:\Program Files (x86)\CST Studio Suite 2025\Online Help\mergedProjects" `
  --reset
```

快速验证可加 `--max-files 80`；正式构建不要设置文件上限。检索调试接口返回
`source_path/source_type/page/chunk_idx/distance/score/dense_score`；生产 Agent Trace 还包含
`rerank_score/rank_before/rank_after/reranker_model`，可直接用于演示排序与 provenance。

Windows 下 `CHROMA_PERSIST_DIR` 必须使用纯 ASCII 路径。构建进程内 smoke query 不足以证明持久化；
当前 ingestion 会额外启动独立 Python 子进程冷启动查询，只有成功后才移除 building marker 并写 ready manifest。

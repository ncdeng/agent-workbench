# Model protocols and cache boundaries

## Upstream model protocols

The CST Agent keeps one canonical message/tool contract inside Python and translates only at the provider boundary.

| `MODEL_API_PROTOCOL` | Wire API | Adapter |
|---|---|---|
| `chat_completions` | OpenAI `POST /v1/chat/completions` | OpenAI SDK Chat Completions |
| `responses` | OpenAI `POST /v1/responses` | OpenAI SDK Responses |
| `anthropic_messages` | Anthropic `POST /v1/messages` | Anthropic SDK Messages |

The adapter normalizes text, function calls, tool-result history, finish reasons, token usage, cache-read tokens, and cache-write tokens back to the existing executor contract. Planner, Executor, Reflection, S11 analysis, and RAG query helpers therefore do not each maintain three protocol implementations.

`AGENT_BRAIN=native` uses the Python adapter. `AGENT_BRAIN=pi` sends the selected `PI_API_PROTOCOL` to the restricted Pi sidecar, which selects Pi's corresponding `openai-completions`, `openai-responses`, or `anthropic-messages` API adapter. Protocol selection does not change the Python tool allowlist, approval gate, recovery, trace, or CST execution boundary.

The protocol describes the server endpoint, not the model family. A gateway serving Claude through an OpenAI-compatible `/v1/chat/completions` endpoint still uses `chat_completions`; choose `anthropic_messages` only when the gateway implements the Anthropic Messages wire contract.

## Model-call caching

The main Agent response is deliberately not memoized. A model turn can request CST modeling, solver, export, or approved VBA side effects. Reusing a previous response could repeat or skip work against a different project state. Native and Pi therefore always execute a fresh provider turn and route every requested tool through the current Host checks.

The supported model cache is provider-side prompt-prefix caching:

- OpenAI Chat/Responses receives a stable `prompt_cache_key` and retention preference when enabled.
- Anthropic Messages receives ephemeral cache control with a 5-minute or 1-hour TTL.
- Pi passes `none`, `short`, or `long` cache retention to its selected provider adapter.
- Traces retain `cached_tokens` and `cache_write_tokens`; cumulative stats expose `cached` and `cache_write` separately from total prompt tokens.

Cache-read tokens are already part of prompt/input tokens and must not be added a second time when calculating total tokens. A cache hit reduces provider-side prefix processing and cost/latency where supported; it does not bypass the model call and does not guarantee identical output. Unsupported OpenAI-compatible gateways should set `MODEL_PROMPT_CACHE_ENABLED=false`.

## Other reuse mechanisms

These stores solve different problems and must not be presented as one generic “LLM cache”:

| Mechanism | Scope and invalidation | What a hit skips |
|---|---|---|
| RAG query rewrite/translation cache | Process-local; keyed by query/model/count; no TTL or capacity limit | The auxiliary rewrite/translation LLM call |
| Curated KB corpus embedding cache | Persistent `.npy`; content/provider/model-addressed; no TTL | Re-embedding corpus entries; query embedding still runs |
| Official-document Chroma index | Persistent collection with schema/embedding identity metadata; explicit reset | Re-embedding indexed documents; query embedding still runs |
| StructuredMemory entry embedding memo | Process-local 512-entry FIFO; provider/model/text hash key | Re-embedding known memory entries; query embedding still runs |
| Reranker/model-weight cache | Process-local model object plus configured D-drive model files | Reloading model weights, not inference |
| Tool result store | Session-local FIFO, default 100; cleared on session/project lifecycle transitions | Only an explicit `recall_tool_result`; normal tools are not deduplicated |
| StructuredMemory / ToolUseMemory | Scoped state and procedural experience with bounded persistence | Nothing directly; they guide later planning/tool ordering |

All runtime-owned cache and model directories should be configured under `D:\cst_agent_rag_data` on this workstation. Tests must set pytest `--basetemp` and `TEMP`/`TMP` to a D-drive directory.

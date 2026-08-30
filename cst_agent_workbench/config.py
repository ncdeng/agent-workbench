import os
from pathlib import Path

# User configuration.
# Sensitive values must come from environment variables and should never be
# committed into the repository.


_PROCESS_ENV_KEYS = set(os.environ.keys())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_local_env_file(env_path: Path, allow_override: bool = False) -> None:
    """Load simple KEY=VALUE pairs from a repo-local .env file.

    Existing process environment variables keep higher priority.
    """
    if not env_path.exists():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key in _PROCESS_ENV_KEYS:
                continue
            if allow_override or key not in os.environ:
                os.environ[key] = value
    except OSError:
        # Keep startup resilient if the local .env file cannot be read.
        pass


_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_DIR = _PACKAGE_DIR.parent
_load_local_env_file(_REPO_DIR / ".env")
_load_local_env_file(_REPO_DIR / ".env.local", allow_override=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4").strip()

# Upstream generation protocol. MODEL_* is the provider-neutral interface;
# OPENAI_* remains a backwards-compatible fallback and is still used by the
# independent embedding path when EMBEDDING_* is not configured.
MODEL_API_PROTOCOL = os.environ.get("MODEL_API_PROTOCOL", "chat_completions").strip().lower()
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "").strip() or OPENAI_API_KEY
MODEL_BASE_URL = os.environ.get("MODEL_BASE_URL", "").strip() or OPENAI_BASE_URL
MODEL_NAME = os.environ.get("MODEL_NAME", "").strip() or OPENAI_MODEL
MODEL_MAX_OUTPUT_TOKENS = max(1, int(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "8192")))
# Official OpenAI enables the explicit key by default. Custom gateways are
# conservative-by-default because many only implement a Chat-compatible subset.
MODEL_PROMPT_CACHE_ENABLED = _env_bool("MODEL_PROMPT_CACHE_ENABLED", not bool(MODEL_BASE_URL))
MODEL_PROMPT_CACHE_KEY = os.environ.get("MODEL_PROMPT_CACHE_KEY", "cst-agent").strip()
MODEL_PROMPT_CACHE_TTL = os.environ.get("MODEL_PROMPT_CACHE_TTL", "5m").strip().lower()

# Agent harness. ``native`` keeps the established Python/OpenAI tool loop;
# ``pi`` delegates only that loop to the restricted Node sidecar. The Python
# host remains authoritative for AgentSession, tool execution, recovery and CST.
AGENT_BRAIN = os.environ.get("AGENT_BRAIN", "native").strip().lower()
PI_NODE_EXECUTABLE = os.environ.get("PI_NODE_EXECUTABLE", "node").strip() or "node"
PI_SIDECAR_PATH = os.environ.get(
    "PI_SIDECAR_PATH",
    str(_REPO_DIR / "integrations" / "pi_agent_core" / "sidecar.mjs"),
).strip()
PI_SIDECAR_TIMEOUT_SEC = max(1, int(os.environ.get("PI_SIDECAR_TIMEOUT_SEC", "180")))
PI_API_KEY = os.environ.get("PI_API_KEY", "").strip() or MODEL_API_KEY
PI_BASE_URL = os.environ.get("PI_BASE_URL", "").strip() or MODEL_BASE_URL
PI_MODEL = os.environ.get("PI_MODEL", "").strip() or MODEL_NAME
PI_API_PROTOCOL = os.environ.get("PI_API_PROTOCOL", "").strip().lower() or MODEL_API_PROTOCOL
PI_CONTEXT_WINDOW = max(1024, int(os.environ.get("PI_CONTEXT_WINDOW", "128000")))
PI_MAX_OUTPUT_TOKENS = max(1, int(os.environ.get("PI_MAX_OUTPUT_TOKENS", "8192")))
PI_THINKING_LEVEL = os.environ.get("PI_THINKING_LEVEL", "off").strip().lower()
PI_PROMPT_CACHE_ENABLED = _env_bool("PI_PROMPT_CACHE_ENABLED", MODEL_PROMPT_CACHE_ENABLED)
PI_PROMPT_CACHE_TTL = os.environ.get("PI_PROMPT_CACHE_TTL", "").strip().lower() or MODEL_PROMPT_CACHE_TTL

# Optional default CST project path. Leave empty to open an existing project or
# create a new blank project automatically.
CST_DEFAULT_PROJECT = ""

# CST connection behavior.
CST_CONNECT_TIMEOUT_SEC = int(os.environ.get("CST_CONNECT_TIMEOUT_SEC", "90"))
CST_CONNECT_RETRIES = int(os.environ.get("CST_CONNECT_RETRIES", "1"))
CST_CONNECT_RETRY_DELAY_SEC = int(os.environ.get("CST_CONNECT_RETRY_DELAY_SEC", "5"))
CST_PYTHON_EXECUTABLE = (
    os.environ.get("CST_PYTHON_EXECUTABLE", "").strip()
    or os.environ.get("CST_PYTHON_EXE", "").strip()
)

# Fast-path temporary project retention.
FAST_PATH_KEEP_LATEST = int(os.environ.get("FAST_PATH_KEEP_LATEST", "1"))

# Agent session snapshots and cross-session structured memory. Keep this
# separate from the RAG cache: the two stores have different retention and
# migration semantics. Set AGENT_MEMORY_DIR to a D: drive path on Windows
# machines where the user profile drive is space-constrained.
AGENT_MEMORY_DIR = os.environ.get(
    "AGENT_MEMORY_DIR",
    str(Path.home() / ".cache" / "cst_agent_workbench"),
).strip()
AGENT_CONTEXT_MAX_TOKENS = max(512, int(os.environ.get("AGENT_CONTEXT_MAX_TOKENS", "8192")))
AGENT_HISTORY_RETENTION = max(20, int(os.environ.get("AGENT_HISTORY_RETENTION", "200")))
AGENT_TOOL_EVENT_RETENTION = max(20, int(os.environ.get("AGENT_TOOL_EVENT_RETENTION", "500")))
AGENT_TOOL_RESULT_RETENTION = max(10, int(os.environ.get("AGENT_TOOL_RESULT_RETENTION", "100")))
AGENT_MAX_REPLAN_RETRIES = max(0, min(2, int(os.environ.get("AGENT_MAX_REPLAN_RETRIES", "1"))))

# Local CST Python package repository path used by pip --find-links.
CST_INSTALL_ROOT = os.environ.get(
    "CST_INSTALL_ROOT",
    "",
).strip()
CST_PYTHON_REPO = os.environ.get(
    "CST_PYTHON_REPO",
    os.path.join(CST_INSTALL_ROOT, "Library", "Python", "repo", "simple"),
).strip()
FARFIELD_TEMPLATE_R0D_SOURCE = os.environ.get("FARFIELD_TEMPLATE_R0D_SOURCE", "").strip()

# Runtime-owned CST projects and exports must not fall back to the Windows user
# TEMP directory because it commonly lives on the space-constrained C: drive.
# Keep one configurable D: root and derive the individual stores from it.
CST_RUNTIME_ROOT = os.environ.get(
    "CST_RUNTIME_ROOT",
    r"D:\cst_agent_rag_data\runtime\cst_agent_workbench",
).strip()
CST_FAST_PATH_DIR = os.environ.get(
    "CST_FAST_PATH_DIR",
    str(Path(CST_RUNTIME_ROOT) / "projects" / "fast_path"),
).strip()

# CST bridge scripts can be large as well. Keep them below the same D: root;
# callers may still provide a more specific D: location.
CST_TEMP_DIR = os.environ.get(
    "CST_TEMP_DIR",
    str(Path(CST_RUNTIME_ROOT) / "tmp" / "bridge"),
).strip()

# Possible CST installation paths for auto-detection. Use semicolon-separated
# CST_EXECUTABLE_PATHS to override on another machine.
_DEFAULT_CST_POSSIBLE_PATHS = [
    *(
        [os.path.join(CST_INSTALL_ROOT, "CST DESIGN ENVIRONMENT.exe")]
        if CST_INSTALL_ROOT else []
    ),
    r"D:\Program Files (x86)\CST Studio Suite 2025\CST DESIGN ENVIRONMENT.exe",
    r"D:\Program Files (x86)\CST Studio Suite 2024\CST DESIGN ENVIRONMENT.exe",
    r"C:\Program Files (x86)\CST Studio Suite 2025\AMD64\CST Design Environment.exe",
    r"C:\Program Files\CST Studio Suite 2025\AMD64\CST Design Environment.exe",
]
_CST_EXECUTABLE_PATHS_ENV = os.environ.get("CST_EXECUTABLE_PATHS", "").strip()
CST_POSSIBLE_PATHS = [
    path.strip()
    for path in (_CST_EXECUTABLE_PATHS_ENV.split(";") if _CST_EXECUTABLE_PATHS_ENV else _DEFAULT_CST_POSSIBLE_PATHS)
    if path.strip()
]

# Token cost estimation in USD per 1M tokens.
TOKEN_COST_PROMPT = float(os.environ.get("TOKEN_COST_PROMPT", "10.0"))
TOKEN_COST_COMPLETION = float(os.environ.get("TOKEN_COST_COMPLETION", "30.0"))

# Optimization settings.
# OPT_TARGET_MODE:
# - "at_f0": use the S11 value at target frequency f0 as the stop criterion
# - "min_s11": use the minimum S11 over the full curve as the stop criterion
OPT_TARGET_MODE = os.environ.get("OPT_TARGET_MODE", "at_f0").strip().lower()
OPT_TARGET_S11_DB = float(os.environ.get("OPT_TARGET_S11_DB", "-10.0"))
OPT_MAX_ROUNDS = int(os.environ.get("OPT_MAX_ROUNDS", "20"))
OPT_STAGNATION_LIMIT = int(os.environ.get("OPT_STAGNATION_LIMIT", "3"))

# RAG cache and document knowledge-base settings.
PDF_KNOWLEDGE_DIR = os.environ.get("PDF_KNOWLEDGE_DIR", "").strip()
RAG_CACHE_DIR = os.environ.get(
    "RAG_CACHE_DIR",
    str(Path.home() / ".cache" / "cst_agent_rag"),
).strip()
CHROMA_PERSIST_DIR = os.environ.get(
    "CHROMA_PERSIST_DIR",
    str(Path(RAG_CACHE_DIR) / "chromadb"),
).strip()
CHROMA_COLLECTION_NAME = os.environ.get(
    "CHROMA_COLLECTION_NAME",
    "cst_pdf_knowledge",
).strip()

# RAG dynamic-entry write gate: reflection-generated lessons must reach this
# confidence to be persisted into the dynamic knowledge base. Prevents low-quality
# / hallucinated lessons from poisoning RAG retrieval. Set to 0.0 to disable.
RAG_LESSON_MIN_CONFIDENCE = float(os.environ.get("RAG_LESSON_MIN_CONFIDENCE", "0.6"))

# Semantic recall gate for memory/lesson injection (raw cosine floor).
# The historical value 0.05 was effectively "no filter" (near-random similarity);
# unrelated pairs under bge-small-zh typically score below ~0.3, so 0.30 removes
# noise while keeping genuinely related lessons. Set to 0.0 to disable.
MEMORY_RECALL_MIN_SCORE = float(os.environ.get("MEMORY_RECALL_MIN_SCORE", "0.30"))

# RAG query rewrite (HyDE-lite): when enabled, RAG retrieval will ask the chat
# LLM to paraphrase the query into 2-3 alternates and union results. Improves
# recall on terse or jargon-mismatched queries at the cost of one extra LLM call.
RAG_QUERY_REWRITE = os.environ.get("RAG_QUERY_REWRITE", "false").strip().lower() in {"1", "true", "yes", "on"}
RAG_QUERY_REWRITE_N = int(os.environ.get("RAG_QUERY_REWRITE_N", "2"))

# CST Online Help is an English corpus. Chinese/mixed user questions therefore
# need an English retrieval query before they reach an English-only embedding
# model. This translation is kept separate from the generic curated-rule
# paraphraser so the language contract is explicit and independently testable.
RAG_DOCUMENT_LANGUAGE = os.environ.get("RAG_DOCUMENT_LANGUAGE", "en").strip().lower()
RAG_DOCUMENT_QUERY_TRANSLATION = os.environ.get(
    "RAG_DOCUMENT_QUERY_TRANSLATION",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}

# Production document retrieval first recalls a wider dense candidate pool,
# then applies an English cross-encoder before source-level deduplication.
RAG_RERANK_ENABLED = os.environ.get("RAG_RERANK_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RAG_RERANK_MODEL = os.environ.get(
    "RAG_RERANK_MODEL",
    "cross-encoder/ms-marco-MiniLM-L6-v2",
).strip()
RAG_RERANK_CANDIDATE_K = max(1, int(os.environ.get("RAG_RERANK_CANDIDATE_K", "20")))
RAG_RERANK_BATCH_SIZE = max(1, int(os.environ.get("RAG_RERANK_BATCH_SIZE", "16")))
RAG_RERANK_CACHE_DIR = os.environ.get(
    "RAG_RERANK_CACHE_DIR",
    str(Path(RAG_CACHE_DIR) / "reranker_models"),
).strip()

# Embedding provider: "local" uses sentence-transformers (free, offline);
# "api" uses remote embedding API (can differ from chat API).
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local").strip().lower()
EMBEDDING_LOCAL_MODEL = os.environ.get("EMBEDDING_LOCAL_MODEL", "BAAI/bge-base-en-v1.5").strip()
# BGE's official retrieval contract adds this instruction to short queries but
# not to passages. Leave it empty for models that use symmetric inputs.
EMBEDDING_QUERY_INSTRUCTION = os.environ.get(
    "EMBEDDING_QUERY_INSTRUCTION",
    "Represent this sentence for searching relevant passages: ",
)
# Separate embedding API config (falls back to OPENAI_* if not set).
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "").strip() or OPENAI_API_KEY
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "").strip() or OPENAI_BASE_URL
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small").strip()

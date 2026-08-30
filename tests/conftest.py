"""测试基础设施：snapshot fixture + opt-in primitives reset fixture。

snapshot 用法：
    def test_xxx(snapshot, reset_primitives):
        snapshot(some_vba_string)

首次跑会在 tests/snapshots/<module>/<test_name>.vba 创建 snapshot 并 fail 提示。
后续跑差异会输出 unified diff。环境变量 UPDATE_SNAPSHOTS=1 时强制覆写。
"""
import difflib
import os
import pathlib

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live-cst",
        action="store_true",
        default=False,
        help="Run tests that connect to real CST Studio Suite.",
    )


def pytest_collection_modifyitems(config, items):
    run_live_cst = config.getoption("--run-live-cst") or os.environ.get("RUN_LIVE_CST") == "1"
    if run_live_cst:
        return
    skip_live_cst = pytest.mark.skip(reason="requires explicit --run-live-cst or RUN_LIVE_CST=1")
    for item in items:
        if "cst" in item.keywords:
            item.add_marker(skip_live_cst)


@pytest.fixture(autouse=True)
def _no_real_embedding_model(monkeypatch, request):
    """离线测试不得加载真实 embedding 权重或打开真实向量库。

    两条真实资源路径都会在全量离线套件里炸内存（本机实测 OOM
    `memory allocation of 67108864 bytes failed`，解释器直接 rc=127）：

    1. `.env` 默认 `EMBEDDING_PROVIDER=local`，`recall_memory` / RAG 检索经
       `knowledge_base._local_embed` 懒加载 bge-small-zh；
    2. `chroma_store._get_collection()` 打开 ~1GB 的 chroma.sqlite3 并构造
       `SentenceTransformerEmbeddingFunction`（该库当前 hnsw 索引已损坏，
       每次调用都白付一次模型加载再抛错）。

    CI 只是因为没装 sentence-transformers/chromadb 才侥幸走了兜底路径，
    所以这是「本地必炸、CI 看不见」的不可复现套件。

    这里让本地 embedding 抛 ImportError —— 与「模型未安装」完全同一条代码路径：
    `_semantic_rank` 吞掉异常回退 token-overlap，`retrieve_antenna_rules` 回退
    `_keyword_fallback`。于是离线套件与 CI 行为一致，且不伪造相似度分数（伪造会
    静默改变排序语义，让 min_score 之类的门槛测出假结果）。

    文档库则复用 chroma_store 自己的懒初始化门（`_init_attempted`），保持
    「未初始化」状态而不替换函数，这样自带临时 collection 的测试仍可注入。
    需要真实资源的测试加 `@pytest.mark.real_embeddings` 显式退出。
    """
    if request.node.get_closest_marker("real_embeddings"):
        return

    from cst_agent_workbench.rag import chroma_store, knowledge_base

    def _unavailable_local_embed(texts):
        raise ImportError("local embedding model disabled in offline tests")

    monkeypatch.setattr(knowledge_base, "_local_embed", _unavailable_local_embed)
    monkeypatch.setattr(knowledge_base, "_local_model", None)
    monkeypatch.setattr(chroma_store, "_collection", None)
    monkeypatch.setattr(chroma_store, "_client", None)
    monkeypatch.setattr(chroma_store, "_init_attempted", True)


@pytest.fixture
def snapshot(request):
    test_module = request.node.module.__name__.split(".")[-1]
    test_name = request.node.name
    snap_dir = pathlib.Path(__file__).parent / "snapshots" / test_module
    snap_path = snap_dir / f"{test_name}.vba"

    def _check(actual: str):
        actual = actual.replace("\r\n", "\n").rstrip("\n") + "\n"
        update = os.environ.get("UPDATE_SNAPSHOTS") == "1"
        if update or not snap_path.exists():
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_path.write_text(actual, encoding="utf-8", newline="\n")
            if not update:
                pytest.fail(
                    f"Snapshot created: {snap_path}. Review and rerun.",
                    pytrace=False,
                )
            return
        expected = snap_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if expected != actual:
            diff = "".join(
                difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile=str(snap_path),
                    tofile="actual",
                    n=3,
                )
            )
            pytest.fail(
                f"Snapshot mismatch:\n{diff}\nRun UPDATE_SNAPSHOTS=1 to update.",
                pytrace=False,
            )

    return _check


@pytest.fixture
def reset_primitives():
    """Opt-in：每个 snapshot 测试前后清空 primitives 全局 registry。

    复用 primitives.reset_created_objects() 单一来源，避免漏 reset 某个 registry。
    """
    from cst_agent_workbench.cst.primitives import reset_created_objects

    reset_created_objects()
    yield
    reset_created_objects()

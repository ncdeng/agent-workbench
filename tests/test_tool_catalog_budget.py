from cst_agent_workbench.agent.runtime import _estimate_tokens
from cst_agent_workbench.agent.tools import TOOLS


def test_full_canonical_catalog_retains_context_headroom():
    """Dynamic filtering is primary, but the full catalog must not exceed the hard budget."""

    estimated = _estimate_tokens([], TOOLS, model="gpt-5.6-terra")
    assert len(TOOLS) == 42
    assert estimated <= 7200

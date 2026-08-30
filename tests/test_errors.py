"""cst_agent_workbench.errors:classify_error 单元测试。

每种 ErrorType 都用一条**真实代码里出现过**的 message 样本测试，
样本来自 controller.py / patch_fast_executor.py / tool_runtime.py。
"""
import pytest

from cst_agent_workbench.errors import ErrorType, classify_error


@pytest.mark.parametrize(
    "message,expected",
    [
        # CST_TIMEOUT
        ("CST 操作超时（300秒）", ErrorType.CST_TIMEOUT),
        ("solver timeout", ErrorType.CST_TIMEOUT),
        # OFFLINE_MODE
        ("当前为离线模式，将只生成脚本，不会在 CST 中自动执行", ErrorType.OFFLINE_MODE),
        # CST_CONNECTION
        ("无法连接 CST", ErrorType.CST_CONNECTION),
        ("无法启动 CST", ErrorType.CST_CONNECTION),
        # RESULT_READ
        ("Farfield RunScript 导出失败: COM error", ErrorType.RESULT_READ),
        ("EvaluateResultTemplates 失败: ", ErrorType.RESULT_READ),
        ("ASCIIExport 失败", ErrorType.RESULT_READ),
        # VBA_EXECUTION（按 fast path 各阶段）
        ("矩形贴片快速路径在初始化阶段失败: VBA error", ErrorType.VBA_EXECUTION),
        ("矩形贴片快速路径在几何阶段失败", ErrorType.VBA_EXECUTION),
        ("矩形贴片快速路径在端口阶段失败", ErrorType.VBA_EXECUTION),
        ("矩形贴片快速路径在远场监视器阶段失败", ErrorType.VBA_EXECUTION),
        ("当前 fast path 仅支持非优化模式下的 microstrip 矩形贴片。",
         ErrorType.VBA_EXECUTION),
        # PARAMETER_INVALID
        ("原语参数错误: missing arg", ErrorType.PARAMETER_INVALID),
        ("缺少 farfield 导出路径", ErrorType.PARAMETER_INVALID),
        ("模板参数缺失：feed_w", ErrorType.PARAMETER_INVALID),
        # RESOURCE_MISSING
        ("工程文件不存在: C:/foo.cst", ErrorType.RESOURCE_MISSING),
        ("未找到 farfield_template.r0d", ErrorType.RESOURCE_MISSING),
        # PLANNER_FAILED
        ("planner 输出无法解析", ErrorType.PLANNER_FAILED),
        ("Plan 解析失败: invalid JSON", ErrorType.PLANNER_FAILED),
        # PHYSICS_VALIDATION（A-1 引入后会真正出现）
        ("物理 sanity 失败：patch_w/lambda 超出区间", ErrorType.PHYSICS_VALIDATION),
    ],
)
def test_classify_error_known_messages(message, expected):
    assert classify_error(message) == expected


def test_classify_error_empty_returns_unknown():
    assert classify_error("") == ErrorType.UNKNOWN


def test_classify_error_none_returns_unknown():
    assert classify_error(None) == ErrorType.UNKNOWN


def test_classify_error_unrecognized_returns_unknown():
    assert classify_error("totally unrelated error: 42") == ErrorType.UNKNOWN


def test_error_type_values_are_strings():
    """ErrorType 值用字符串：保证 JSON 序列化、tool_events dict 兼容。"""
    for et in ErrorType:
        assert isinstance(et.value, str)


def test_timeout_takes_precedence_over_other_keywords():
    """优先级：超时关键词在前面，应优先匹配。"""
    msg = "CST 操作超时（300秒）：farfield 导出"
    assert classify_error(msg) == ErrorType.CST_TIMEOUT


def test_offline_takes_precedence_over_resource_missing():
    """OFFLINE_MODE 应在 RESOURCE_MISSING 之前命中（避免被'文件不存在'误吃）。"""
    msg = "当前为离线模式，无法导出 farfield ASCII"
    assert classify_error(msg) == ErrorType.OFFLINE_MODE

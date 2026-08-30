"""错误分类。基于 message 关键词推断 error_type，给 reflection / UI / 未来决策路径用。

不引入 raise 异常类：项目当前所有 controller / executor / tool 返回都是
{"success": bool, "message": str} dict，引入 raise 需要改全部调用方，违反
"加 20 行能解决就别引依赖" 原则。

用法：
    from cst_agent_workbench.errors import classify_error, ErrorType
    error_type = classify_error(result.get("message"))
    if error_type == ErrorType.CST_TIMEOUT:
        ...
"""
from enum import Enum
from typing import Optional, Tuple


class ErrorType(str, Enum):
    OFFLINE_MODE = "offline_mode"            # CST 未连接的信息态（多数情况下不算 hard error）
    CST_CONNECTION = "cst_connection"        # 连接 / 启动 CST 失败
    CST_TIMEOUT = "cst_timeout"              # 操作超时
    VBA_EXECUTION = "vba_execution"          # VBA 执行失败（含 fast path 各阶段）
    RESULT_READ = "result_read"              # farfield / S-param 读取或导出失败
    RESOURCE_MISSING = "resource_missing"    # 工程文件 / 模板 / 路径不存在
    PARAMETER_INVALID = "parameter_invalid"  # 参数缺失或无效
    PLANNER_FAILED = "planner_failed"        # planner 输出无法解析
    PHYSICS_VALIDATION = "physics_validation"  # 物理 sanity 失败（A-1 启用后会用）
    UNKNOWN = "unknown"


# 关键词 → ErrorType。前面的优先匹配（更具体的放前面）。
# 关键排序约束：
# - OFFLINE_MODE 必须在 RESULT_READ 前面（"当前为离线模式，无法导出 farfield ASCII" 应分类为 OFFLINE）
# - RESOURCE_MISSING / PARAMETER_INVALID 必须在 RESULT_READ 前面（"未找到 farfield_template.r0d"
#   和 "缺少 farfield 导出路径" 都不应被 RESULT_READ 吃掉）
# - RESULT_READ 不要用裸 "farfield" 关键词，太宽，用更具体的子串（RunScript / ASCIIExport / 无法导出 等）
_PATTERNS: Tuple[Tuple[Tuple[str, ...], ErrorType], ...] = (
    (("超时", "timeout", "timed out"), ErrorType.CST_TIMEOUT),
    (("离线模式", "offline mode"), ErrorType.OFFLINE_MODE),
    (
        ("未连接", "无法连接", "not connected", "连接 CST 失败", "无法启动 CST", "未启动 CST"),
        ErrorType.CST_CONNECTION,
    ),
    (
        ("参数错误", "参数无效", "参数缺失", "参数不合法", "缺少", "模板参数"),
        ErrorType.PARAMETER_INVALID,
    ),
    (
        ("工程文件不存在", "文件不存在", "未找到", "missing", "Missing", "找不到"),
        ErrorType.RESOURCE_MISSING,
    ),
    (
        ("RunScript", "ASCIIExport", "EvaluateResultTemplates", "导出失败",
         "无法导出", "无法提取"),
        ErrorType.RESULT_READ,
    ),
    (("planner", "Planner", "Plan 解析", "plan 解析"), ErrorType.PLANNER_FAILED),
    (("物理", "physics", "sanity"), ErrorType.PHYSICS_VALIDATION),
    (
        ("VBA", "vba", "几何阶段", "端口阶段", "初始化阶段", "远场监视器阶段",
         "fast path", "fast_path"),
        ErrorType.VBA_EXECUTION,
    ),
)


def classify_error(message: Optional[str]) -> ErrorType:
    """根据 message 关键词分类。message 为空或 None 返回 UNKNOWN。"""
    if not message:
        return ErrorType.UNKNOWN
    for keywords, etype in _PATTERNS:
        if any(kw in message for kw in keywords):
            return etype
    return ErrorType.UNKNOWN

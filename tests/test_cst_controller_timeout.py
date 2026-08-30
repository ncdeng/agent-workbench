"""CSTController 子进程超时与错误信息处理测试（G6.1 + G6.3）。

CSTController 通过 subprocess.run 调用 CST Python 子进程执行 VBA / 求解。
这两个测试覆盖子进程异常路径的两条已知债：
  - G6.1：超时不 abort CST DE（无法跨进程），但 controller 应标记 connected=False
    返回 timeout=True，让上层知道下次命令需走 reconnect 路径；错误信息也要
    诚实说明 "DE 可能仍在跑上一次求解"。
  - G6.3：returncode != 0 时 CST/Python traceback 在 stderr，应优先取 stderr
    而不是把 stderr-when-empty 退化成 stdout-when-nonempty（旧逻辑会让
    "stdout warning + 空 stderr" 误报为 "stdout 内容即错误"）。
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from cst_agent_workbench.cst.controller import CSTController


def _fake_completed_process(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    """构造一个简易的 subprocess.CompletedProcess 替身，字段足够 controller 使用。"""
    return subprocess.CompletedProcess(
        args=["fake"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_run_com_script_timeout_marks_controller_disconnected_and_signals_caller():
    """超时后 controller.connected 应为 False，返回 dict 带 timeout=True 与诚实提示。

    场景：run_solver 调用卡住超过 timeout。subprocess.run 抛 TimeoutExpired，
    Controller 无法跨进程 abort CST DE，但要：(1) 标记自身 connected=False
    让下次命令强制重连；(2) 在 result 里放 timeout=True 给调用方判断；
    (3) message 里包含 "CST Design Environment 可能仍在执行" 的诚实说明。
    """
    controller = CSTController()
    controller.connected = True  # 模拟已连接状态
    controller.cst_python_command = ["fake-python"]

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="fake", timeout=kwargs.get("timeout", 1))

    with patch("cst_agent_workbench.cst.controller.subprocess.run", side_effect=_raise_timeout):
        result = controller._run_com_script(["run_solver"], timeout=5)

    assert result["success"] is False
    assert result.get("timeout") is True
    assert controller.connected is False
    # 诚实提示：不要假装 CST 已 abort，要让用户知道 DE 可能还在跑
    assert "CST Design Environment" in result["message"]
    assert "可能仍在执行" in result["message"]


def test_run_com_script_returncode_nonzero_prefers_stderr_over_stdout():
    """returncode != 0 时 CST traceback 在 stderr，应优先展现 stderr。

    回归：旧逻辑 `stderr_text = result.stderr.strip() or result.stdout.strip()`
    在 stderr 空而 stdout 有内容时退化到 stdout，这是对的；但更早版本的 `or`
    顺序反了容易误报。这里同时验证 stderr 非空时不会被 stdout 顶替。
    """
    controller = CSTController()
    controller.connected = True
    controller.cst_python_command = ["fake-python"]

    fake_result = _fake_completed_process(
        returncode=1,
        stdout='{"success": true, "ignore": "stdout warning before crash"}',
        stderr="Traceback (most recent call last):\n  File \"...\", line 1\n    cst.interface.fail()\ncst.interface.SomeError: boom",
    )

    with patch("cst_agent_workbench.cst.controller.subprocess.run", return_value=fake_result):
        result = controller._run_com_script(["execute"], timeout=5)

    assert result["success"] is False
    # stderr 中的 Python traceback 应进入错误信息，而不是被 stdout 的 JSON 顶替
    assert "Traceback" in result["message"]
    assert "boom" in result["message"]
    # stdout 里的 JSON 不应被误当作"错误输出"
    assert "ignore" not in result["message"]


def test_run_com_script_returncode_nonzero_falls_back_to_stdout_when_stderr_empty():
    """stderr 完全为空时回退到 stdout，避免错误信息为空字符串。

    边界场景：子进程在 stdout print 一个错误 JSON 后 sys.exit(1)，stderr 完全空。
    旧逻辑 `stderr or stdout` 已经能处理；这条测试锁定该行为不退化。
    """
    controller = CSTController()
    controller.connected = True
    controller.cst_python_command = ["fake-python"]

    fake_result = _fake_completed_process(
        returncode=1,
        stdout="some stdout-only error text",
        stderr="",
    )

    with patch("cst_agent_workbench.cst.controller.subprocess.run", return_value=fake_result):
        result = controller._run_com_script(["execute"], timeout=5)

    assert result["success"] is False
    assert "some stdout-only error text" in result["message"]

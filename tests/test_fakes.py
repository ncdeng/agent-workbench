"""tests/fakes.py:FakeCSTController 的单元测试 + drift 防御。"""
from fakes import FakeCSTController


# ── A. 默认行为 ─────────────────────────────────────────────────────────


def test_default_execute_vba_returns_success_and_records_call():
    fake = FakeCSTController()
    result = fake.execute_vba("With Brick\n.Reset\n.Create\nEnd With", label="brick", timeout=30)
    assert result["success"] is True
    assert result["executed"] is True
    assert result["vba_code"].startswith("With Brick")
    assert result["message"]
    assert fake.calls["execute_vba"] == [
        {"label": "brick", "vba_code": result["vba_code"], "timeout": 30}
    ]


def test_offline_mode_execute_vba_returns_executed_false_and_echoes_vba():
    fake = FakeCSTController(offline_mode=True)
    result = fake.execute_vba("StoreParameter \"f0\", \"2.4\"", label="param")
    assert result["success"] is True
    assert result["executed"] is False
    assert result["vba_code"] == "StoreParameter \"f0\", \"2.4\""
    assert "未实际执行" in result["message"]


def test_is_connected_returns_false_when_offline():
    online = FakeCSTController()
    offline = FakeCSTController(offline_mode=True)
    disconnected = FakeCSTController(connected=False)
    assert online.is_connected() is True
    assert offline.is_connected() is False
    assert disconnected.is_connected() is False


def test_get_status_distinguishes_online_and_offline():
    assert "在线" in FakeCSTController().get_status()
    assert "离线" in FakeCSTController(offline_mode=True).get_status()


def test_new_project_updates_project_path_when_provided():
    fake = FakeCSTController()
    result = fake.new_project(project_path="C:/tmp/foo.cst", timeout=10)
    assert result["success"] is True
    assert result["project_file"] == "C:/tmp/foo.cst"
    assert fake.project_path == "C:/tmp/foo.cst"


def test_call_history_tracks_multiple_methods_independently():
    fake = FakeCSTController()
    fake.execute_vba("a")
    fake.execute_vba("b")
    fake.run_solver(timeout=200)
    fake.export_farfield_ascii("Farfields\\f1", "C:/tmp/f1.txt")
    assert len(fake.calls["execute_vba"]) == 2
    assert len(fake.calls["run_solver"]) == 1
    assert len(fake.calls["export_farfield_ascii"]) == 1
    assert len(fake.calls["new_project"]) == 0


def test_export_farfield_ascii_returns_required_fields():
    fake = FakeCSTController()
    result = fake.export_farfield_ascii(
        item_path="Farfields\\f1",
        output_path="C:/tmp/f1.txt",
    )
    assert result["success"] is True
    assert result["output_path"] == "C:/tmp/f1.txt"
    assert result["item_path"] == "Farfields\\f1"
    assert "vba_code" in result
    assert "project_file" in result


# ── B. 注入行为 ─────────────────────────────────────────────────────────


def test_queue_response_overrides_next_call():
    fake = FakeCSTController()
    fake.queue_response("execute_vba", {"success": False, "executed": False, "message": "syntax error", "vba_code": ""})
    result = fake.execute_vba("bad vba")
    assert result["success"] is False
    assert result["message"] == "syntax error"


def test_queue_failure_is_shorthand_for_failure_response():
    fake = FakeCSTController()
    fake.queue_failure("run_solver", message="solver crashed")
    result = fake.run_solver()
    assert result["success"] is False
    assert result["message"] == "solver crashed"


def test_queue_consumes_fifo_and_falls_back_to_default():
    fake = FakeCSTController()
    fake.queue_failure("execute_vba", message="first")
    fake.queue_failure("execute_vba", message="second")
    first = fake.execute_vba("a")
    second = fake.execute_vba("b")
    third = fake.execute_vba("c")
    assert first["message"] == "first"
    assert second["message"] == "second"
    assert third["success"] is True  # 默认响应


def test_queue_response_rejects_unknown_method():
    fake = FakeCSTController()
    try:
        fake.queue_response("not_a_real_method", {"success": True})
    except ValueError as e:
        assert "not_a_real_method" in str(e)
    else:
        raise AssertionError("expected ValueError")


# ── C. drift 防御 ───────────────────────────────────────────────────────


def test_fake_cst_controller_matches_real_public_interface():
    """如果 CSTController 增删 public 方法，这里立刻 fail 提醒维护 fake。"""
    from cst_agent_workbench.cst.controller import CSTController

    def public_methods(cls):
        return {
            n for n in dir(cls)
            if not n.startswith("_") and callable(getattr(cls, n))
        }

    real = public_methods(CSTController)
    fake = public_methods(FakeCSTController)
    fake_extra_allowed = {"queue_response", "queue_failure"}

    missing_in_fake = real - fake
    extra_in_fake = (fake - real) - fake_extra_allowed

    assert not missing_in_fake, (
        f"FakeCSTController 缺少以下 public 方法（CSTController 上有）：{sorted(missing_in_fake)}"
    )
    assert not extra_in_fake, (
        f"FakeCSTController 多了非预期 public 方法：{sorted(extra_in_fake)}"
    )

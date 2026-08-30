from cst_agent_workbench.results import reader as reader_mod
from cst_agent_workbench.results.contracts import ResultKind, validate_result_envelope
from cst_agent_workbench.results.reader import ResultsReader


class _FakeCurveData:
    def __init__(self, x_values, y_values):
        self.x_data = x_values
        self.y_data = y_values


class _FakeResultItem:
    def __init__(self, data):
        self._data = data

    def get_data(self):
        return self._data


class _FakeResultModule:
    def __init__(self, items, data_map):
        self._items = items
        self._data_map = data_map

    def get_tree_items(self):
        return list(self._items)

    def get_result_item(self, item_path):
        if item_path not in self._data_map:
            raise KeyError(item_path)
        return _FakeResultItem(self._data_map[item_path])


def _make_reader(items, data_map=None):
    reader = ResultsReader()
    reader._result_module = _FakeResultModule(items, data_map or {})
    return reader


def test_list_results_reports_farfield_plot_and_raw_monitor_items():
    items = [
        r"1D Results\S-Parameters\S1,1",
        r"Farfields\farfield (f=9.4)",
        r"Farfields\1D Results\phi=0\farfield (f=9.4)",
        r"Farfields\1D Results\theta=90\farfield (f=9.4)",
    ]
    reader = _make_reader(items)

    result = reader.list_results()

    assert result["success"] is True
    assert result["farfield"]["plot_count"] == 2
    assert result["farfield"]["raw_monitor_count"] == 1
    assert "可读取的远场 1D 切线结果" in result["farfield"]["message"]
    assert "items" not in result["farfield"]
    assert "plot_items" not in result["farfield"]


def test_list_results_paginates_and_filters_without_returning_full_tree():
    items = [
        r"1D Results\S-Parameters\S1,1",
        r"1D Results\S-Parameters\S2,1",
        r"Tables\Summary",
        r"Farfields\farfield (f=9.4)",
    ]
    reader = _make_reader(items)

    first = reader.list_results(offset=0, limit=1, category="1D Results", query="S")
    second = reader.list_results(offset=1, limit=1, category="1d results", query="s")

    assert first["items"] == [items[0]]
    assert first["total"] == 2
    assert first["has_more"] is True
    assert first["next_offset"] == 1
    assert second["items"] == [items[1]]
    assert second["has_more"] is False
    assert "all_items" not in first


def test_read_result_explains_raw_farfield_monitor_limitation():
    items = [r"Farfields\farfield (f=9.4)"]
    reader = _make_reader(items)

    result = reader.read_result(r"Farfields\farfield (f=9.4)")

    assert result["success"] is False
    assert result["type"] == "farfield_monitor_only"
    assert result["result_kind"] == ResultKind.UNSUPPORTED_3D.value
    assert "原始远场监视器节点" in result["message"]
    assert "无法把原始 Farfields 监视器节点直接转换为方向图曲线" in result["message"]


def test_get_farfield_plot_prefers_phi_zero_cut_and_returns_plot_data():
    items = [
        r"Farfields\farfield (f=9.4)",
        r"Farfields\1D Results\theta=90\farfield (f=9.4)",
        r"Farfields\1D Results\phi=0\farfield (f=9.4)",
    ]
    data_map = {
        r"Farfields\1D Results\theta=90\farfield (f=9.4)": _FakeCurveData([0.0, 45.0], [1.0, 2.0]),
        r"Farfields\1D Results\phi=0\farfield (f=9.4)": _FakeCurveData([0.0, 90.0], [3.0, 4.0]),
    }
    reader = _make_reader(items, data_map)

    result = reader.get_farfield_plot()

    assert result["success"] is True
    assert result["result_kind"] == ResultKind.CURVE_1D.value
    assert result["selected_item"] == r"Farfields\1D Results\phi=0\farfield (f=9.4)"
    assert result["plot_data"] == [{"x": 0.0, "y": 3.0}, {"x": 90.0, "y": 4.0}]
    validate_result_envelope(result)


def test_read_curve_downsamples_deterministically_and_preserves_extrema():
    item = r"1D Results\Curve"
    values = list(range(101))
    y_values = [float(index) for index in values]
    y_values[47] = -999.0
    y_values[83] = 999.0
    reader = _make_reader([item], {item: _FakeCurveData(values, y_values)})

    result = reader.read_result(item, max_points=12)

    assert result["result_kind"] == ResultKind.CURVE_1D.value
    assert result["total_points"] == 101
    assert result["returned_points"] == 12
    assert result["downsampled"] is True
    assert result["plot_data"][0]["x"] == 0.0
    assert result["plot_data"][-1]["x"] == 100.0
    assert {point["y"] for point in result["plot_data"]}.issuperset({-999.0, 999.0})
    validate_result_envelope(result)


def test_s_parameter_contract_uses_full_curve_for_summary_and_bounded_plot():
    item = r"1D Results\S-Parameters\S1,1"
    data = [(float(index), complex(1.0, 0.0)) for index in range(40)]
    data[17] = (17.0, complex(0.01, 0.0))
    reader = _make_reader([item], {item: data})

    result = reader.get_s_parameter(max_points=8)

    assert result["result_kind"] == ResultKind.S_PARAMETER.value
    assert result["total_points"] == 40
    assert result["returned_points"] == 8
    assert result["s_db_min"] == -40.0
    assert any(point["freq"] == 17.0 for point in result["plot_data"])
    validate_result_envelope(result)


def test_get_farfield_plot_reports_monitor_only_when_no_cut_exists():
    items = [r"Farfields\farfield (f=9.4)"]
    reader = _make_reader(items)

    result = reader.get_farfield_plot()

    assert result["success"] is False
    assert result["type"] == "farfield_monitor_only"
    assert "不能可靠地把这些节点直接转换成可绘制的 1D 方向图" in result["message"]


def test_reader_falls_back_to_cst_python_subprocess_when_results_package_unavailable(monkeypatch, tmp_path):
    cst_path = tmp_path / "demo.cst"
    cst_path.write_text("placeholder", encoding="utf-8")
    calls = []

    def fake_run_subprocess(self, action, **payload):
        calls.append((action, payload))
        if action == "open":
            return {"success": True, "message": "opened remotely"}
        if action == "read_result":
            return {"success": True, "item": payload["item_path"], "plot_data": [{"freq": 9.4, "s_db": -12.0}]}
        return {"success": False, "message": action}

    monkeypatch.setattr(reader_mod, "ProjectFile", None)
    monkeypatch.setattr(reader_mod.ResultsReader, "_run_subprocess", fake_run_subprocess)
    reader = ResultsReader()

    open_result = reader.open(str(cst_path))
    s11 = reader.get_s_parameter()

    assert open_result == {"success": True, "message": "opened remotely"}
    assert reader._uses_subprocess is True
    assert s11["success"] is True
    assert s11["plot_data"] == [{"freq": 9.4, "s_db": -12.0}]
    assert calls == [
        ("open", {"cst_path": str(cst_path)}),
        ("read_result", {"cst_path": str(cst_path), "item_path": r"1D Results\S-Parameters\S1,1"}),
    ]

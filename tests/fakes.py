"""统一 Fake CSTController：drop-in 替代真实 controller，用于 CI 离线测试。

不引入新依赖，不修改生产代码。测试用 `agent.cst = FakeCSTController(...)` 注入。

接口对齐 cst_agent_workbench/cst/controller.py:CSTController 的 public 方法。
方法签名严格一致；存在 drift 时 tests/test_fakes.py 的 compat 测试会立刻失败。
"""
from collections import deque
from typing import Any, Deque, Dict, List


class FakeCSTController:
    DEFAULT_PROJECT_PATH = "C:/fake/project.cst"

    _METHOD_NAMES = (
        "connect",
        "evaluate_result_templates",
        "run_solver_with_templates",
        "reopen_project_and_solve",
        "run_solver",
        "new_project",
        "open_project",
        "save_project",
        "save_project_as",
        "close_project",
        "set_mesh_by_frequency",
        "set_global_hexahedral_mesh",
        "get_mesh_signature",
        "execute_vba",
        "list_solids",
        "list_farfield_tree_items",
        "probe_farfield_tree_items",
        "export_farfield_ascii",
        "export_result_ascii",
        "get_farfield_numeric",
    )

    def __init__(
        self,
        project_path: str = DEFAULT_PROJECT_PATH,
        offline_mode: bool = False,
        connected: bool = True,
    ):
        self.connected = connected
        self.offline_mode = offline_mode
        self.cst_exe = "FAKE"
        self.last_message = ""
        self.project_path = project_path

        self.calls: Dict[str, List[Dict[str, Any]]] = {
            name: [] for name in self._METHOD_NAMES
        }
        self._response_queues: Dict[str, Deque[Dict[str, Any]]] = {
            name: deque() for name in self._METHOD_NAMES
        }

    # ── 注入 API（测试专用，不在 CSTController 上） ─────────────────────
    def queue_response(self, method: str, response: Dict[str, Any]) -> None:
        if method not in self._response_queues:
            raise ValueError(f"unknown method: {method}")
        self._response_queues[method].append(response)

    def queue_failure(self, method: str, message: str = "fake failure") -> None:
        self.queue_response(method, {"success": False, "message": message})

    def _consume(self, method: str, default: Dict[str, Any]) -> Dict[str, Any]:
        q = self._response_queues[method]
        return q.popleft() if q else default

    # ── 状态查询 ──────────────────────────────────────────────────────
    def is_connected(self) -> bool:
        return self.connected and not self.offline_mode

    def get_status(self) -> str:
        return "已连接 CST（fake 在线）" if self.is_connected() else "fake 离线模式"

    # ── 连接 ──────────────────────────────────────────────────────────
    def connect(self) -> dict:
        self.calls["connect"].append({})
        return self._consume(
            "connect",
            {
                "success": True,
                "message": "fake connected",
                "project_file": self.project_path,
                "mode": "fake",
            },
        )

    # ── 工程管理 ──────────────────────────────────────────────────────
    def new_project(self, project_path: str = "", timeout: int = 60) -> dict:
        self.calls["new_project"].append(
            {"project_path": project_path, "timeout": timeout}
        )
        if project_path:
            self.project_path = project_path
        return self._consume(
            "new_project",
            {
                "success": True,
                "message": "fake new project",
                "project_file": self.project_path,
            },
        )

    def close_project(self, project_path: str, timeout: int = 30) -> dict:
        self.calls["close_project"].append(
            {"project_path": project_path, "timeout": timeout}
        )
        result = self._consume(
            "close_project",
            {
                "success": True,
                "message": "fake close project",
                "project_file": project_path,
            },
        )
        if result.get("success") and self.project_path == project_path:
            self.project_path = ""
        return result

    def open_project(self, project_path: str, timeout: int = 60) -> dict:
        self.calls["open_project"].append(
            {"project_path": project_path, "timeout": timeout}
        )
        result = self._consume(
            "open_project",
            {
                "success": True,
                "message": "fake open project",
                "project_file": project_path,
            },
        )
        if result.get("success"):
            self.project_path = str(result.get("project_file") or project_path)
        return result

    def save_project(self, include_results: bool = True, timeout: int = 60) -> dict:
        self.calls["save_project"].append(
            {"include_results": include_results, "timeout": timeout}
        )
        return self._consume(
            "save_project",
            {
                "success": True,
                "message": "fake save project",
                "project_file": self.project_path,
                "project_saved": True,
            },
        )

    def save_project_as(
        self,
        target_path: str,
        include_results: bool = True,
        timeout: int = 90,
    ) -> dict:
        self.calls["save_project_as"].append(
            {
                "target_path": target_path,
                "include_results": include_results,
                "timeout": timeout,
            }
        )
        result = self._consume(
            "save_project_as",
            {
                "success": True,
                "message": "fake save project as",
                "project_file": target_path,
                "project_saved": True,
            },
        )
        if result.get("success"):
            self.project_path = str(result.get("project_file") or target_path)
        return result

    # ── VBA 执行 ──────────────────────────────────────────────────────
    def execute_vba(
        self, vba_code: str, label: str = "cst_agent", timeout: int = 60
    ) -> dict:
        self.calls["execute_vba"].append(
            {"label": label, "vba_code": vba_code, "timeout": timeout}
        )
        if self.offline_mode:
            return {
                "success": True,
                "executed": False,
                "message": "fake offline：未实际执行",
                "vba_code": vba_code,
            }
        return self._consume(
            "execute_vba",
            {
                "success": True,
                "executed": True,
                "message": "fake ok",
                "vba_code": vba_code,
            },
        )

    def execute_vba_immediate(self, vba_code: str, timeout: int = 60) -> dict:
        return self.execute_vba(vba_code, label="immediate", timeout=timeout)

    def list_solids(self, timeout: int = 60) -> dict:
        self.calls["list_solids"].append({"timeout": timeout})
        return self._consume(
            "list_solids",
            {"success": True, "message": "fake solid inventory", "solids": []},
        )

    # ── 求解 ──────────────────────────────────────────────────────────
    def run_solver(self, timeout: int = 300) -> dict:
        self.calls["run_solver"].append({"timeout": timeout})
        return self._consume(
            "run_solver",
            {
                "success": True,
                "message": "fake solver done",
                "project_file": self.project_path,
            },
        )

    def run_solver_with_templates(self, timeout: int = 300) -> dict:
        self.calls["run_solver_with_templates"].append({"timeout": timeout})
        return self._consume(
            "run_solver_with_templates",
            {
                "success": True,
                "message": "fake solver+templates done",
                "project_file": self.project_path,
            },
        )

    def reopen_project_and_solve(self, timeout: int = 600) -> dict:
        self.calls["reopen_project_and_solve"].append({"timeout": timeout})
        return self._consume(
            "reopen_project_and_solve",
            {
                "success": True,
                "message": "fake reopen+solve done",
                "project_file": self.project_path,
            },
        )

    def evaluate_result_templates(self, timeout: int = 60) -> dict:
        self.calls["evaluate_result_templates"].append({"timeout": timeout})
        return self._consume(
            "evaluate_result_templates",
            {
                "success": True,
                "message": "fake eval templates done",
                "project_file": self.project_path,
            },
        )

    # ── 网格 ──────────────────────────────────────────────────────────
    def set_mesh_by_frequency(
        self,
        f0_ghz: float,
        epsilon_r: float = 1.0,
        steps: int = 15,
        timeout: int = 30,
        project_path: str = "",
    ) -> dict:
        self.calls["set_mesh_by_frequency"].append(
            {
                "f0_ghz": f0_ghz,
                "epsilon_r": epsilon_r,
                "steps": steps,
                "timeout": timeout,
                "project_path": project_path,
            }
        )
        return self._consume(
            "set_mesh_by_frequency",
            {"success": True, "message": "fake mesh set"},
        )

    def set_global_hexahedral_mesh(
        self,
        lines_per_wavelength: int = 15,
        minimum_step_number: int = 5,
        timeout: int = 30,
        project_path: str = "",
    ) -> dict:
        self.calls["set_global_hexahedral_mesh"].append(
            {
                "lines_per_wavelength": lines_per_wavelength,
                "minimum_step_number": minimum_step_number,
                "timeout": timeout,
                "project_path": project_path,
            }
        )
        return self._consume(
            "set_global_hexahedral_mesh",
            {"success": True, "message": "fake global hexahedral mesh set"},
        )

    def get_mesh_signature(self, timeout: int = 30) -> dict:
        self.calls["get_mesh_signature"].append({"timeout": timeout})
        return self._consume(
            "get_mesh_signature",
            {
                "success": False,
                "error_type": "unsupported_mesh_signature",
                "message": "fake has no realized mesh getter",
                "configured_mesh_is_not_signature": True,
            },
        )

    # ── farfield ──────────────────────────────────────────────────────
    def list_farfield_tree_items(self, timeout: int = 30) -> dict:
        self.calls["list_farfield_tree_items"].append({"timeout": timeout})
        return self._consume(
            "list_farfield_tree_items",
            {
                "success": True,
                "message": "fake list ok",
                "items": [],
                "project_file": self.project_path,
            },
        )

    def probe_farfield_tree_items(self, output_path: str, timeout: int = 60) -> dict:
        self.calls["probe_farfield_tree_items"].append(
            {"output_path": output_path, "timeout": timeout}
        )
        return self._consume(
            "probe_farfield_tree_items",
            {
                "success": True,
                "message": "fake probe ok",
                "output_path": output_path,
                "vba_code": "",
                "project_file": self.project_path,
            },
        )

    def export_farfield_ascii(
        self,
        item_path: str,
        output_path: str,
        theta_step_deg: float = 5.0,
        phi_step_deg: float = 5.0,
        plot_mode: str = "gain",
        use_db: bool = True,
        timeout: int = 120,
    ) -> dict:
        self.calls["export_farfield_ascii"].append(
            {
                "item_path": item_path,
                "output_path": output_path,
                "theta_step_deg": theta_step_deg,
                "phi_step_deg": phi_step_deg,
                "plot_mode": plot_mode,
                "use_db": use_db,
                "timeout": timeout,
            }
        )
        return self._consume(
            "export_farfield_ascii",
            {
                "success": True,
                "message": "fake export ascii ok",
                "output_path": output_path,
                "item_path": item_path,
                "vba_code": "",
                "project_file": self.project_path,
            },
        )

    def export_result_ascii(
        self,
        item_path: str,
        output_path: str,
        timeout: int = 120,
    ) -> dict:
        self.calls["export_result_ascii"].append(
            {"item_path": item_path, "output_path": output_path, "timeout": timeout}
        )
        return self._consume(
            "export_result_ascii",
            {
                "success": True,
                "message": "fake result ascii ok",
                "output_path": output_path,
                "item_path": item_path,
                "vba_code": "",
                "project_file": self.project_path,
            },
        )

    def get_farfield_numeric(
        self,
        item_path: str,
        output_path: str,
        theta_step_deg: float = 5.0,
        phi_step_deg: float = 5.0,
        plot_mode: str = "gain",
        use_db: bool = True,
        timeout: int = 120,
    ) -> dict:
        self.calls["get_farfield_numeric"].append(
            {
                "item_path": item_path,
                "output_path": output_path,
                "theta_step_deg": theta_step_deg,
                "phi_step_deg": phi_step_deg,
                "plot_mode": plot_mode,
                "use_db": use_db,
                "timeout": timeout,
            }
        )
        return self._consume(
            "get_farfield_numeric",
            {
                "success": True,
                "message": "fake farfield numeric ok",
                "output_path": output_path,
                "item_path": item_path,
                "vba_code": "",
                "project_file": self.project_path,
            },
        )

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from pydantic import BaseModel

from cst_agent_workbench.cst.solver_safety import validate_solver_ready

logger = logging.getLogger(__name__)


class FarfieldReadRequest(BaseModel):
    item: str = ""
    cutType: str = "phi"
    cutValueDeg: float = 0.0


def register_cst_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    operation_lock: asyncio.Lock,
    run_exclusive: Callable[[asyncio.Lock, Callable[[], Any]], Any],
    coerce_action_result: Callable[..., dict[str, Any]],
    result_success: Callable[[dict[str, Any]], bool],
    record_direct_event: Callable[..., None],
) -> None:
    @app.post("/api/cst/reconnect")
    async def cst_reconnect():
        def _run():
            state = get_app_state(dry_run=dry_run)
            try:
                result = coerce_action_result(state.cst.connect(), default_message="CST reconnect returned no result")
            except Exception as exc:
                logger.exception("cst_reconnect error")
                result = {"success": False, "message": str(exc)}
            record_direct_event(
                state.session,
                phase="0_CST连接",
                tool_name="connect",
                result=result,
                description="重新连接 CST",
            )
            return {"success": result_success(result), "message": result.get("message", "")}

        return await run_exclusive(operation_lock, _run)

    @app.post("/api/cst/simulate")
    async def cst_simulate():
        def _run():
            state = get_app_state(dry_run=dry_run)
            try:
                if not state.cst.is_connected():
                    result = {"success": False, "message": "CST not connected"}
                else:
                    preflight = validate_solver_ready(state.cst, source="web.cst_simulate")
                    if not result_success(preflight):
                        result = preflight
                    else:
                        result = coerce_action_result(
                            state.cst.run_solver(timeout=300),
                            default_message="Solver returned no result",
                        )
            except Exception as exc:
                logger.exception("cst_simulate error")
                result = {"success": False, "message": str(exc)}
            record_direct_event(
                state.session,
                phase="4_运行仿真",
                tool_name="run_solver_direct",
                result=result,
                description="手动运行仿真",
            )
            return result

        return await run_exclusive(operation_lock, _run)

    @app.post("/api/cst/read-s11")
    async def cst_read_s11():
        def _run():
            state = get_app_state(dry_run=dry_run)
            try:
                from cst_agent_workbench.results.service import read_s11
                result = coerce_action_result(
                    read_s11(state.results_reader, state.cst.project_path, 1, 1),
                    default_message="S11 reader returned no result",
                )
            except Exception as exc:
                logger.exception("cst_read_s11 error")
                result = {"success": False, "message": str(exc)}
            if result_success(result):
                state.session.artifacts.last_results = result
            record_direct_event(
                state.session,
                phase="5_读取结果",
                tool_name="read_s11_direct",
                result=result,
                description="手动读取 S11",
            )
            return result

        return await run_exclusive(operation_lock, _run)

    @app.post("/api/cst/read-farfield")
    async def cst_read_farfield(req: FarfieldReadRequest):
        def _run():
            state = get_app_state(dry_run=dry_run)
            try:
                if not state.cst.is_connected():
                    result = {"success": False, "message": "CST not connected"}
                    record_direct_event(
                        state.session,
                        phase="5_读取结果",
                        tool_name="read_farfield_direct",
                        result=result,
                        description="手动读取远场",
                    )
                    return result
                from cst_agent_workbench.results.service import read_farfield_result
                result = coerce_action_result(
                    read_farfield_result(
                        state.results_reader,
                        state.cst.project_path,
                        item_path=req.item or None,
                        cut_type=req.cutType,
                        cut_value_deg=req.cutValueDeg,
                        cst=state.cst,
                    ),
                    default_message="Farfield reader returned no result",
                )
                if result_success(result):
                    state.session.artifacts.last_farfield_results = result
            except Exception as exc:
                logger.exception("cst_read_farfield error")
                result = {"success": False, "message": str(exc)}
            record_direct_event(
                state.session,
                phase="5_读取结果",
                tool_name="read_farfield_direct",
                result=result,
                description="手动读取远场",
            )
            return result

        return await run_exclusive(operation_lock, _run)

    # POST：会触发 COM 调用并写入 session.tool_events，不是安全方法
    @app.post("/api/cst/refresh-results")
    async def cst_refresh_results():
        def _run():
            state = get_app_state(dry_run=dry_run)
            try:
                if not state.cst.is_connected():
                    result = {"success": False, "items": [], "message": "CST not connected"}
                else:
                    result = coerce_action_result(
                        state.cst.list_result_items(timeout=30),
                        default_message="Result tree refresh returned no result",
                    )
            except Exception as exc:
                logger.exception("cst_refresh_results error")
                result = {"success": False, "items": [], "message": str(exc)}
            record_direct_event(
                state.session,
                phase="5_读取结果",
                tool_name="refresh_results_direct",
                result=result,
                description="刷新 CST 结果树",
            )
            return result

        return await run_exclusive(operation_lock, _run)

    # POST：需要抢互斥锁并驱动 COM，不是安全方法
    @app.post("/api/cst/farfield-items")
    async def cst_farfield_items():
        def _run():
            state = get_app_state(dry_run=dry_run)
            if not state.cst.is_connected():
                return {"success": False, "items": [], "message": "CST not connected"}
            try:
                result = state.cst.list_farfield_tree_items(timeout=30)
                return {
                    "success": result_success(result),
                    "items": result.get("items", []),
                    "message": result.get("message", ""),
                }
            except Exception as exc:
                return {"success": False, "items": [], "message": str(exc)}

        return await run_exclusive(operation_lock, _run)

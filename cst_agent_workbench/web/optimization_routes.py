from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RollbackRequest(BaseModel):
    target: str = "best"


def register_optimization_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    operation_lock: asyncio.Lock,
    run_blocking: Callable[[Callable[[], Any]], Any],
    run_exclusive: Callable[[asyncio.Lock, Callable[[], Any]], Any],
    stream_produced_frames: Callable[..., Any],
    result_success: Callable[[dict[str, Any]], bool],
    record_direct_event: Callable[..., None],
) -> None:
    stop_continuous = {"stop": False}

    @app.post("/api/optimize/once")
    async def optimize_once():
        def _run():
            try:
                from cst_agent_workbench.web.optimization_helpers import run_programmatic_optimization_round
                state = get_app_state(dry_run=dry_run)
                result = run_programmatic_optimization_round(state, algorithm="auto")
                if result is None:
                    return {"ok": False, "error": "Programmatic optimizer not available for this setup"}
                return {"ok": True, "strategy": result.get("strategy"), "rolledBack": result.get("rolled_back", False)}
            except Exception as exc:
                logger.exception("optimize_once error")
                return {"ok": False, "error": str(exc)}

        return await run_exclusive(operation_lock, _run)

    @app.post("/api/optimize/continuous")
    async def optimize_continuous(req: dict):
        state = get_app_state(dry_run=dry_run)
        max_rounds = int(req.get("maxRounds", state.opt_settings.get("max_rounds", 20)))
        algorithm = req.get("algorithm", "auto")
        stop_continuous["stop"] = False

        async def event_generator():
            async def producer(emit):
                # 连续优化期间把 opt_state.active 置真：Dashboard 的
                # optimization.active 才反映真实运行状态，图路由的 reflect
                # 判定读到的也是真实信号。
                opt_state = getattr(state.agent, "opt_state", None)
                if opt_state is not None:
                    opt_state.reset()
                    opt_state.target_mode = state.opt_settings.get("mode", "at_f0")
                    opt_state.target_freq = float(state.opt_settings.get("target_freq") or 0.0)
                    opt_state.target_db = float(state.opt_settings.get("target_db") or -10.0)
                    opt_state.active = True
                try:
                    for i in range(max_rounds):
                        if stop_continuous["stop"]:
                            emit(f"data: {json.dumps({'type': 'stopped', 'round': i})}\n\n")
                            break

                        def _run_round():
                            from cst_agent_workbench.web.optimization_helpers import run_programmatic_optimization_round
                            return run_programmatic_optimization_round(state, algorithm=algorithm)

                        async with operation_lock:
                            result = await run_blocking(_run_round)
                        if result is None:
                            emit("data: " + json.dumps({"type": "error", "message": "Optimizer not available"}) + "\n\n")
                            break

                        round_data = {
                            "type": "round", "round": i + 1,
                            "result": {
                                "strategy": result.get("strategy", ""),
                                "rolledBack": result.get("rolled_back", False),
                                "success": result.get("success", False),
                                "message": result.get("message", ""),
                            },
                        }
                        emit("data: " + json.dumps(round_data) + "\n\n")
                        stop_reason = ""
                        if not result.get("success", False):
                            stop_reason = "round_failed"
                        elif result.get("target_met"):
                            stop_reason = "target_met"
                        elif result.get("stagnation_hit"):
                            stop_reason = "stagnation_limit"
                        elif result.get("no_change"):
                            stop_reason = "no_parameter_update"
                        if stop_reason:
                            emit("data: " + json.dumps({
                                "type": "completed",
                                "round": i + 1,
                                "reason": stop_reason,
                            }) + "\n\n")
                            break
                except Exception as exc:
                    logger.exception("optimize_continuous error")
                    emit("data: " + json.dumps({"type": "error", "message": str(exc)}) + "\n\n")
                finally:
                    if opt_state is not None:
                        opt_state.active = False

            async for frame in stream_produced_frames(producer):
                yield frame

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/optimize/stop")
    def optimize_stop():
        stop_continuous["stop"] = True
        return {"ok": True}

    @app.post("/api/optimize/rollback")
    async def optimize_rollback(req: RollbackRequest):
        def _run():
            state = get_app_state(dry_run=dry_run)
            agent = state.agent
            opt_state = agent.opt_state
            history = getattr(opt_state, "history", []) or []
            if not history:
                return {"ok": False, "error": "No optimization history to rollback"}

            if req.target == "best":
                best_round = getattr(opt_state, "best_round", 0)
                target_round = best_round
            else:
                try:
                    target_round = int(req.target)
                except ValueError:
                    return {"ok": False, "error": f"Invalid target: {req.target}"}

            if target_round < 1 or target_round > len(history):
                return {"ok": False, "error": f"Round {target_round} out of range (1-{len(history)})"}

            entry = history[target_round - 1]
            param_snapshot = entry.get("param_snapshot") or {}
            if not param_snapshot:
                return {"ok": False, "error": f"Round {target_round} has no parameter snapshot"}

            try:
                from cst_agent_workbench.cst.primitives import store_parameter, register_parameter
                vba_lines = []
                for name, value in param_snapshot.items():
                    _, vba_line = store_parameter(name, str(value))
                    vba_lines.append(vba_line)
                result = state.cst.execute_vba("\n".join(vba_lines), timeout=120)
                if result_success(result):
                    for name, value in param_snapshot.items():
                        register_parameter(name, str(value))
                # 回滚会真实改写工程参数，必须和其它直接操作端点一样留下可审计事件，
                # 否则 Trace 上会出现一次没有来源的参数变化。
                record_direct_event(
                    state.session,
                    phase="6_优化调参",
                    tool_name="rollback_parameters_direct",
                    result=result,
                    description=f"回滚到第 {target_round} 轮参数（{len(param_snapshot)} 个）",
                )
                return {"ok": result_success(result), "message": result.get("message", ""), "round": target_round}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return await run_exclusive(operation_lock, _run)

    @app.get("/api/optimize/history")
    def optimize_history():
        state = get_app_state(dry_run=dry_run)
        opt_state = state.agent.opt_state
        history = getattr(opt_state, "history", []) or []
        return {"rounds": history, "bestRound": getattr(opt_state, "best_round", 0)}

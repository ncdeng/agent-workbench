from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    images: list[str] | None = None


class ChatControlRequest(BaseModel):
    action: str
    message: str = ""


def register_chat_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    operation_lock: asyncio.Lock,
    run_exclusive: Callable[[asyncio.Lock, Callable[[], Any]], Any],
    serialize_chat_history: Callable[[list[dict]], list[dict]],
    serialize_tool_event: Callable[[dict[str, Any]], dict[str, Any]],
    chat_status_payload: Callable[..., dict[str, Any]],
    stream_produced_frames: Callable[..., Any],
) -> None:
    def _run_turn(agent: Any, message: str, images: list[str] | None) -> str:
        """生产 chat 路径：直接调 agent.chat，内部走 planner → tool loop。

        agent.chat 内部依次：build_initial_plan_with_usage（planner）→
        run_agent_turn（tool loop + plan 更新 + replan 评估）。
        期间往 session.tool_events 追加事件。

        注意：普通对话轮不触发 reflection。反思只绑定在程序化优化轮
        （optimization/optimizer.py 调 reflect_on_round），chat 路径没有调用点。
        """
        return agent.chat(message, images=images)

    @app.get("/api/chat/history")
    def chat_history():
        state = get_app_state(dry_run=dry_run)
        return {"messages": serialize_chat_history(state.session.history)}

    @app.get("/api/chat/harness-events")
    def harness_events(after: int = 0):
        state = get_app_state(dry_run=dry_run)
        events = state.session.metadata.get("harness_events") or []
        return {
            "events": [
                event
                for event in events
                if isinstance(event, dict) and int(event.get("sequence", 0) or 0) > max(0, after)
            ]
        }

    @app.post("/api/chat/control")
    def chat_control(req: ChatControlRequest):
        state = get_app_state(dry_run=dry_run)
        brain = getattr(state.agent, "_pi_brain", None)
        if brain is None:
            raise HTTPException(status_code=409, detail="The active Agent brain does not support Pi controls")
        action = req.action.strip().lower()
        if action == "cancel":
            accepted = brain.cancel(session_id=state.session.session_id, reason="user_cancelled")
        elif action == "steer":
            if not req.message.strip():
                raise HTTPException(status_code=422, detail="Steering message must not be empty")
            accepted = brain.steer(req.message, session_id=state.session.session_id)
        elif action == "follow_up":
            if not req.message.strip():
                raise HTTPException(status_code=422, detail="Follow-up message must not be empty")
            accepted = brain.follow_up(req.message, session_id=state.session.session_id)
        else:
            raise HTTPException(status_code=422, detail=f"Unsupported chat control action: {req.action}")
        if not accepted:
            raise HTTPException(status_code=409, detail="No matching Pi run is active")
        return {"accepted": True, "action": action, "sessionId": state.session.session_id}

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        state = get_app_state(dry_run=dry_run)
        agent = state.agent
        session = state.session

        def _run():
            try:
                reply = _run_turn(agent, req.message, req.images)
                return chat_status_payload(agent, reply, tool_event_count=len(session.tool_events))
            except Exception as exc:
                logger.exception("chat error")
                return {"ok": False, "error": str(exc), "reply": ""}

        return await run_exclusive(operation_lock, _run)

    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest):
        state = get_app_state(dry_run=dry_run)
        agent = state.agent
        session = state.session

        async def event_generator():
            yield f"data: {json.dumps({'type': 'start'})}\n\n"

            async def producer(emit):
                # 锁被其他操作（chat/仿真/优化）占用时给客户端一个排队提示，
                # 避免 start 帧之后长时间静默悬挂、用户无从判断状态。
                if operation_lock.locked():
                    emit(
                        f"data: {json.dumps({'type': 'status', 'message': '当前有其他操作正在执行，本请求正在排队等待'}, ensure_ascii=False)}\n\n"
                    )
                async with operation_lock:
                    turn_start = len(session.tool_events)
                    turn_history_start = len(session.history or [])
                    initial_count = turn_start
                    harness_events = session.metadata.get("harness_events") or []
                    harness_count = len(harness_events) if isinstance(harness_events, list) else 0

                    def _run():
                        return _run_turn(agent, req.message, req.images)

                    task = asyncio.get_running_loop().run_in_executor(None, _run)

                    try:
                        idle_polls = 0
                        while not task.done():
                            await asyncio.sleep(0.5)
                            current_count = len(session.tool_events)
                            if current_count > initial_count:
                                idle_polls = 0
                                for evt in session.tool_events[initial_count:current_count]:
                                    emit(
                                        f"data: {json.dumps({'type': 'tool_event', 'event': serialize_tool_event(evt)}, ensure_ascii=False)}\n\n"
                                    )
                                initial_count = current_count
                            else:
                                # 求解器长跑（可达 300s）期间无新事件时发 SSE 注释帧
                                # 心跳，防止代理/浏览器把静默连接掐断。
                                idle_polls += 1
                                if idle_polls >= 30:  # ~15s
                                    idle_polls = 0
                                    emit(": ping\n\n")
                            current_harness_events = session.metadata.get("harness_events") or []
                            if isinstance(current_harness_events, list) and len(current_harness_events) > harness_count:
                                for event in current_harness_events[harness_count:]:
                                    emit(
                                        f"data: {json.dumps({'type': 'harness_event', 'event': event}, ensure_ascii=False)}\n\n"
                                    )
                                harness_count = len(current_harness_events)

                        reply = await task
                        current_count = len(session.tool_events)
                        if current_count > initial_count:
                            for evt in session.tool_events[initial_count:current_count]:
                                emit(
                                    f"data: {json.dumps({'type': 'tool_event', 'event': serialize_tool_event(evt)}, ensure_ascii=False)}\n\n"
                                )
                        current_harness_events = session.metadata.get("harness_events") or []
                        if isinstance(current_harness_events, list) and len(current_harness_events) > harness_count:
                            for event in current_harness_events[harness_count:]:
                                emit(
                                    f"data: {json.dumps({'type': 'harness_event', 'event': event}, ensure_ascii=False)}\n\n"
                                )

                        turn_events = [
                            serialize_tool_event(evt)
                            for evt in session.tool_events[turn_start:current_count]
                        ]
                        if turn_events:
                            for msg in reversed((session.history or [])[turn_history_start:]):
                                if isinstance(msg, dict) and msg.get("role") == "assistant" and not msg.get("tool_calls"):
                                    msg["_tool_events"] = turn_events
                                    break

                        status_payload = chat_status_payload(agent, reply, tool_event_count=current_count)
                        if status_payload["ok"]:
                            emit(f"data: {json.dumps({'type': 'reply', 'content': reply})}\n\n")
                        else:
                            emit(f"data: {json.dumps({'type': 'error', 'message': status_payload['error'] or reply})}\n\n")
                    except Exception as exc:
                        emit(f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n")

            async for frame in stream_produced_frames(producer):
                yield frame

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

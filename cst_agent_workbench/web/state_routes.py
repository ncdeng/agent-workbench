from __future__ import annotations

import asyncio
from typing import Any, Callable


def register_state_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    operation_lock: asyncio.Lock,
    run_exclusive: Callable[[asyncio.Lock, Callable[[], Any]], Any],
) -> None:
    @app.post("/api/state/clear")
    async def state_clear():
        def _run():
            state = get_app_state(dry_run=dry_run)
            try:
                state.agent.clear_history()
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        return await run_exclusive(operation_lock, _run)

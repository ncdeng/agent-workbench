from __future__ import annotations

import asyncio
from typing import Any, Callable

from pydantic import BaseModel


class OptSettings(BaseModel):
    mode: str | None = None
    targetFreqGhz: float | None = None
    targetDb: float | None = None
    maxRounds: int | None = None
    stagnation: int | None = None


def register_settings_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    operation_lock: asyncio.Lock,
) -> None:
    @app.get("/api/settings/optimization")
    def get_opt_settings():
        state = get_app_state(dry_run=dry_run)
        s = state.opt_settings
        return {
            "mode": s.get("mode", "at_f0"),
            "targetFreqGhz": float(s.get("target_freq") or 0.0),
            "targetDb": float(s.get("target_db") or -10.0),
            "maxRounds": int(s.get("max_rounds") or 20),
            "stagnation": int(s.get("stagnation") or 3),
        }

    @app.put("/api/settings/optimization")
    async def update_opt_settings(body: OptSettings):
        async with operation_lock:
            state = get_app_state(dry_run=dry_run)
            s = state.opt_settings
            if body.mode is not None:
                s["mode"] = body.mode
            if body.targetFreqGhz is not None:
                s["target_freq"] = body.targetFreqGhz
            if body.targetDb is not None:
                s["target_db"] = body.targetDb
            if body.maxRounds is not None:
                s["max_rounds"] = body.maxRounds
            if body.stagnation is not None:
                s["stagnation"] = body.stagnation
            return {"ok": True}

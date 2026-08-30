"""Atomic local persistence for Antenna Model-IR documents."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .models import AntennaModelIR


_IR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PERSIST_LOCK = threading.Lock()


class ModelIRRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def path_for(self, ir_id: str) -> Path:
        if not _IR_ID_RE.fullmatch(str(ir_id or "")):
            raise ValueError("ir_id must be a safe filename identifier")
        return self.root / f"{ir_id}.json"

    def save(self, model: AntennaModelIR) -> Path:
        target = self.path_for(model.ir_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(model.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        with _PERSIST_LOCK:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(target)
        return target

    def load(self, ir_id: str) -> AntennaModelIR:
        target = self.path_for(ir_id)
        if not target.is_file():
            raise FileNotFoundError(target)
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Model-IR root must be an object")
        model = AntennaModelIR.from_dict(data)
        if model.ir_id != ir_id:
            raise ValueError("Model-IR filename and payload id do not match")
        return model

    def list_ids(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(path.stem for path in self.root.glob("*.json") if _IR_ID_RE.fullmatch(path.stem)))

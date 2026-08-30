import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import MethodType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.cst.controller import CSTController


DEFAULT_PROMPT = (
    "帮我创建一个中心频率为18.8GHz的矩形微带贴片天线，"
    "基板用RO4350B，Dk=3.48，Df=0.0037，厚度0.51mm，"
    "导体用铜，厚度1盎司"
)


def _install_tool_timing(agent: CSTAgent):
    """Patch the agent instance so each tool call records wall-clock duration."""
    tool_timings = []
    original_execute_tool = agent._execute_tool

    def timed_execute_tool(self, tool_name: str, arguments: dict) -> str:
        started = time.perf_counter()
        result = original_execute_tool(tool_name, arguments)
        duration = time.perf_counter() - started
        tool_timings.append(
            {
                "tool_name": tool_name,
                "duration_sec": round(duration, 3),
                "arguments": arguments,
            }
        )
        return result

    agent._execute_tool = MethodType(timed_execute_tool, agent)
    return tool_timings


def _build_report(
    prompt: str,
    connect_time: float,
    connect_result: dict,
    chat_time: float,
    reply_text: str,
    agent: CSTAgent,
    tool_timings: list,
):
    stats = agent.get_token_stats()
    return {
        "timestamp_local": datetime.now().isoformat(timespec="seconds"),
        "prompt": prompt,
        "connect": {
            "duration_sec": round(connect_time, 3),
            "success": bool(connect_result.get("success", False)),
            "message": connect_result.get("message", ""),
            "project_path": agent.cst.project_path,
        },
        "chat": {
            "duration_sec": round(chat_time, 3),
            "ok": agent.last_chat_status.get("ok", True),
            "error": agent.last_chat_status.get("error", ""),
            "had_tool_failure": agent.last_chat_status.get("had_tool_failure", False),
            "mode": agent.last_chat_status.get("mode", "llm"),
            "reply_preview": reply_text[:1000],
        },
        "token_stats": stats,
        "tool_event_count": len(agent.tool_events),
        "tool_events": agent.tool_events,
        "tool_timings": tool_timings,
        "runtime_snapshot": agent.get_runtime_snapshot(),
    }


def _write_experiment_artifacts(report: dict, output_path: Path) -> None:
    experiments_dir = PROJECT_ROOT / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    latest_snapshot_path = experiments_dir / "latest_runtime_snapshot.json"
    latest_snapshot_path.write_text(
        json.dumps(report["runtime_snapshot"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    history_record = {
        "timestamp_local": report["timestamp_local"],
        "report_file": str(output_path.resolve()),
        "prompt": report["prompt"],
        "connect_success": report["connect"]["success"],
        "chat_ok": report["chat"]["ok"],
        "mode": report["chat"]["mode"],
        "project_path": report["connect"]["project_path"],
        "results": report["runtime_snapshot"]["results"],
        "token_stats": report["token_stats"],
    }
    history_path = experiments_dir / "benchmark_history.jsonl"
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(history_record, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark one natural-language CST request and record timing/token breakdown."
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt to send to the CST agent.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "benchmarks" / "reports" / "benchmark_prompt_latency.json"),
        help="Path to write the JSON report.",
    )
    args = parser.parse_args()

    cst = CSTController()
    agent = CSTAgent(cst)
    tool_timings = _install_tool_timing(agent)

    connect_started = time.perf_counter()
    connect_result = cst.connect()
    connect_time = time.perf_counter() - connect_started

    if not connect_result.get("success", False):
        report = _build_report(
            prompt=args.prompt,
            connect_time=connect_time,
            connect_result=connect_result,
            chat_time=0.0,
            reply_text="",
            agent=agent,
            tool_timings=tool_timings,
        )
        output_path = Path(args.output)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_experiment_artifacts(report, output_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    chat_started = time.perf_counter()
    reply_text = agent.chat(args.prompt)
    chat_time = time.perf_counter() - chat_started

    report = _build_report(
        prompt=args.prompt,
        connect_time=connect_time,
        connect_result=connect_result,
        chat_time=chat_time,
        reply_text=reply_text,
        agent=agent,
        tool_timings=tool_timings,
    )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_experiment_artifacts(report, output_path)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

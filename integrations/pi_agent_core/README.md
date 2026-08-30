# Restricted Pi Agent Core Harness

This directory contains the optional Pi execution harness for CST-Agent. It
uses only `@earendil-works/pi-agent-core` and `@earendil-works/pi-ai`; the Pi
coding-agent CLI and its file, shell, network, and arbitrary MCP tools are not
loaded.

## Boundary

Pi owns:

- model streaming and assistant turns;
- sequential tool-loop continuation;
- OpenAI-compatible message conversion;
- per-turn tool catalog replacement requested from the Python host.

Python remains authoritative for:

- `AgentSession`, Planner state, Memory and final history;
- active-step tool filtering and fail-closed execution allowlists;
- `tool_runtime.execute_tool`, Recovery and result summarization;
- Trace lifecycle and token accounting;
- CST solver safety, COM subprocess isolation and the operation lock.

The sidecar never receives a controller, COM handle, primitive registry, shell,
or writable session object. Tool calls cross stdio as JSONL and are executed by
the Python host. Pi is forced to `toolExecution: "sequential"` because one CST
project is stateful and must not be mutated concurrently.

Every wire message uses Harness protocol v1 with `protocol`, `type`,
`request_id`, and `payload` fields. Python performs a `hello` capability
handshake and health check before `run`; every model/tool event is correlated
to the same run and host session. One idle Node worker is reused across runs,
but each run creates and then disposes its own Pi Agent, pending tool map and
dynamic catalog. Python performs an idle heartbeat before leasing the worker,
restarts a crashed worker only before a new run, and can send a correlated
`abort`; it never replays the failed run automatically. Version, capability,
lifecycle, timeout, crash, and malformed-message failures are fail-closed and
retain stable Harness error codes. There is no automatic Native fallback.

The `run` payload contains a separate Context Envelope v1. It snapshots only
the trimmed conversation, current dynamic tool catalog, host Plan projection,
token-budget metrics and execution flags. Model credentials and lifecycle
control remain outside that envelope, while mutable Python Session/CST objects
never cross the process boundary.

Python also publishes a bounded Harness Event Stream with monotonically
increasing per-session sequence numbers. It projects context-budget, model
usage, tool, recovery, Plan, catalog and run-lifecycle events to SSE and a
read-only API; the richer Trace remains the durable audit source.

The control plane exposes correlated `steer`, `follow_up`, and `abort`
messages. Pi queues steering after the current tool batch and follow-up after
the run would otherwise stop. Abort cancels the Pi/model operation; it does not
pretend to terminate a CST solver call already executing inside the external
Design Environment. The Python operation lock remains the authority preventing
concurrent CST mutations.

## Install on D drive

From the repository root:

```powershell
& .\scripts\install_pi_sidecar.ps1
```

The script installs `node_modules` under this directory and pins the npm cache
to `<repo>\.cache\npm`, both on the repository's D drive.

## Enable

Native remains the default and rollback baseline:

```text
AGENT_BRAIN=native
```

Enable Pi explicitly:

```text
AGENT_BRAIN=pi
PI_NODE_EXECUTABLE=node
PI_SIDECAR_TIMEOUT_SEC=180
PI_API_KEY=
PI_BASE_URL=
PI_MODEL=
PI_CONTEXT_WINDOW=128000
PI_MAX_OUTPUT_TOKENS=8192
PI_THINKING_LEVEL=off
```

Empty `PI_API_KEY`, `PI_BASE_URL`, and `PI_MODEL` values inherit `OPENAI_*`.
Pi configuration or protocol failures are surfaced as turn failures; the host
does not silently fall back to Native because that would invalidate A/B evals.

## Protocol smoke

The smoke uses Pi's Faux provider and performs no network or CST calls:

```powershell
npm run smoke --prefix .\integrations\pi_agent_core
python -m pytest tests/test_pi_brain.py -q --basetemp=.cache\pytest-pi
```

The smoke asserts protocol negotiation, health, two isolated runs through the
same process, repeated tool-call IDs, sequential continuation, and final
answers. Python tests additionally cover History/Trace projection, failed tool
observations, dynamic tool-catalog refresh, request/session correlation,
heartbeat reuse, crash-before-next-run restart, cancellation, stable
timeout/malformed-JSONL errors, same-turn replan reuse, and
`CSTAgent.chat()` wiring.

## Current evaluation status

The integration is mechanically verified, but Native-vs-Pi real-model quality,
latency, token cost, and failure recovery have not yet been measured on the
frozen Agent dataset. Do not claim a Pi quality improvement until that paired
evaluation exists.

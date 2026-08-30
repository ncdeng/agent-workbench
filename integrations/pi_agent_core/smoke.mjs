import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const sidecar = fileURLToPath(new URL("./sidecar.mjs", import.meta.url));
const child = spawn(process.execPath, [sidecar], {
  cwd: fileURLToPath(new URL(".", import.meta.url)),
  stdio: ["pipe", "pipe", "pipe"],
  windowsHide: true,
});

const protocol = { name: "cst-agent-harness", version: 1 };
let expectedRequestId = "smoke-hello";
const send = (type, payload = {}, requestId = expectedRequestId) => {
  child.stdin.write(`${JSON.stringify({ protocol, type, request_id: requestId, payload, session_id: "pi-smoke" })}\n`);
};

const runRequest = {
  context: {
    version: 1,
    messages: [{ role: "user", content: "Check CST status.", timestamp: Date.now() }],
    tools: [
      {
        type: "function",
        function: {
          name: "check_cst_status",
          description: "Check CST connection status",
          parameters: { type: "object", properties: {}, additionalProperties: false },
        },
      },
    ],
    plan: null,
    budget: { max_context_tokens: 6000 },
    execution: { continuation_prompt: "Continue the task.", optimization_mode: false },
  },
  model: { model: "faux", thinking_level: "off" },
  limits: { max_turns: 4 },
  scripted_responses: [
    {
      tool_call: { id: "call-smoke-1", name: "check_cst_status", arguments: {} },
      stop_reason: "toolUse",
    },
    { text: "CST is connected.", stop_reason: "stop" },
  ],
};
const controlRunRequest = {
  ...runRequest,
  scripted_responses: [
    {
      tool_call: { id: "call-smoke-1", name: "check_cst_status", arguments: {} },
      stop_reason: "toolUse",
    },
    { text: "Steering handled.", stop_reason: "stop" },
    { text: "Follow-up handled.", stop_reason: "stop" },
  ],
};

let toolRequests = 0;
let assistantMessages = 0;
const results = [];
let controlsSent = false;
const stderr = [];
child.stderr.on("data", (chunk) => stderr.push(String(chunk)));
const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });

send("hello", {
  host: { name: "pi-smoke", version: "1" },
  protocol_versions: [1],
  required_capabilities: [
    "sequential_tools",
    "dynamic_tool_catalog",
    "structured_errors",
    "healthcheck",
    "cancel",
    "steering",
    "follow_up",
  ],
});
for await (const line of lines) {
  const event = JSON.parse(line);
  assert.equal(event.protocol?.name, protocol.name);
  assert.equal(event.protocol?.version, protocol.version);
  assert.equal(event.request_id, expectedRequestId);
  const payload = event.payload ?? {};
  if (event.type === "hello_ack") {
    assert.equal(payload.runtime?.name, "pi-agent-core");
    expectedRequestId = "smoke-health-1";
    send("health");
    continue;
  }
  if (event.type === "health_result") {
    assert.equal(payload.ok, true);
    expectedRequestId = `pi-smoke-${results.length + 1}`;
    send("run", results.length === 2 ? controlRunRequest : runRequest);
    continue;
  }
  if (event.type === "assistant_message") assistantMessages += 1;
  if (event.type === "tool_request") {
    toolRequests += 1;
    assert.equal(payload.name, "check_cst_status");
    if (expectedRequestId === "pi-smoke-3" && !controlsSent) {
      controlsSent = true;
      send("steer", { message: "Use the latest target instead." });
      send("follow_up", { message: "Then summarize what changed." });
    }
    send("tool_result", {
      tool_call_id: payload.tool_call_id,
      content: JSON.stringify({ success: true, connected: true }),
      is_error: false,
    });
  }
  if (event.type === "prepare_next_turn") {
    send("turn_update", { update_id: payload.update_id, tools: runRequest.context.tools });
  }
  if (event.type === "result") {
    results.push(payload);
    if (results.length < 3) {
      expectedRequestId = `smoke-health-${results.length + 1}`;
      send("health");
      continue;
    }
    break;
  }
  if (event.type === "error") throw new Error(payload.message ?? JSON.stringify(payload));
}

child.stdin.end();
const exitCode = await new Promise((resolve) => child.on("close", resolve));
assert.equal(exitCode, 0, stderr.join(""));
assert.equal(toolRequests, 3);
assert.equal(assistantMessages, 7);
assert.equal(results.length, 3);
for (const result of results.slice(0, 2)) {
  assert.equal(result?.ok, true);
  assert.equal(result?.final_text, "CST is connected.");
  assert.equal(result?.provider_calls, 2);
}
assert.equal(results[2]?.ok, true);
assert.equal(results[2]?.final_text, "Follow-up handled.");
assert.equal(results[2]?.provider_calls, 3);
process.stdout.write(`${JSON.stringify({ ok: true, runs: results.length, toolRequests, assistantMessages })}\n`);

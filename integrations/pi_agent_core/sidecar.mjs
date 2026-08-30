import readline from "node:readline";

import { Agent } from "@earendil-works/pi-agent-core";
import {
  Type,
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxText,
  fauxToolCall,
} from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";
import { anthropicMessagesApi } from "@earendil-works/pi-ai/api/anthropic-messages.lazy";

const ZERO_COST = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
const PROTOCOL_NAME = "cst-agent-harness";
const PROTOCOL_VERSION = 1;
const CAPABILITIES = [
  "sequential_tools",
  "dynamic_tool_catalog",
  "structured_errors",
  "healthcheck",
  "cancel",
  "steering",
  "follow_up",
];
const pendingToolResults = new Map();
const pendingTurnUpdates = new Map();
let activeAgent = null;
let activeRun = null;
let handshakeComplete = false;
let workerState = "starting";
let streamClosed = false;
const queuedRuns = [];
let resolveNextRun = null;

const API_FACTORIES = new Map([
  ["openai-completions", openAICompletionsApi],
  ["openai-responses", openAIResponsesApi],
  ["anthropic-messages", anthropicMessagesApi],
]);

function resolveApiName(value) {
  const name = String(value ?? "openai-completions").trim().toLowerCase();
  if (!API_FACTORIES.has(name)) {
    throw new Error(`Unsupported Pi model API: ${name}`);
  }
  return name;
}

function cacheRetentionFor(spec) {
  if (spec?.prompt_cache_enabled === false) return "none";
  const ttl = String(spec?.prompt_cache_ttl ?? "5m").trim().toLowerCase();
  return ["1h", "24h", "long"].includes(ttl) ? "long" : "short";
}

function envelope(type, requestId, payload = {}, sessionId = "") {
  const message = {
    protocol: { name: PROTOCOL_NAME, version: PROTOCOL_VERSION },
    type,
    request_id: String(requestId ?? ""),
    payload,
  };
  if (sessionId) message.session_id = String(sessionId);
  return message;
}

function sendEnvelope(type, requestId, payload = {}, sessionId = "") {
  process.stdout.write(`${JSON.stringify(envelope(type, requestId, payload, sessionId))}\n`);
}

function harnessError(code, message, details = {}, requestId = "") {
  return {
    layer: "harness",
    code,
    message: String(message),
    retryable: false,
    request_id: String(requestId || "") || undefined,
    cause_type: "PiSidecarProtocolError",
    details,
  };
}

function sendHarnessError(requestId, code, message, details = {}) {
  const correlationId = String(requestId || `sidecar-${Date.now()}`);
  const sessionId = activeRun?.requestId === correlationId ? activeRun.sessionId : "";
  sendEnvelope("error", correlationId, harnessError(code, message, details, correlationId), sessionId);
}

function providerErrorEnvelope(error, requestId) {
  const message = error instanceof Error ? error.message : String(error ?? "Provider call failed");
  const statusRaw = error?.status ?? error?.statusCode ?? error?.response?.status;
  const matchedStatus = /\b(401|403|408|429|5\d\d)\b/.exec(message);
  const status = Number(statusRaw ?? matchedStatus?.[1] ?? 0) || undefined;
  const lowered = message.toLowerCase();
  let code = "unknown";
  let retryable = false;
  if (status === 401 || status === 403) code = "auth";
  else if (status === 429) {
    code = "rate_limit";
    retryable = true;
  } else if ((status ?? 0) >= 500) {
    code = "server";
    retryable = true;
  } else if (status === 408 || lowered.includes("timeout") || lowered.includes("timed out")) {
    code = "timeout";
    retryable = true;
  } else if (
    lowered.includes("econn")
    || lowered.includes("network")
    || lowered.includes("fetch failed")
    || lowered.includes("socket")
  ) {
    code = "network";
    retryable = true;
  }
  return {
    layer: "provider",
    code,
    message,
    retryable,
    status_code: status,
    request_id: String(requestId || "") || undefined,
    cause_type: error instanceof Error ? error.name : "PiProviderError",
    details: {},
  };
}

function sendRunEvent(type, payload = {}) {
  if (!activeRun) throw new Error("No active run correlation is available");
  sendEnvelope(type, activeRun.requestId, payload, activeRun.sessionId);
}

function parseEnvelope(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw Object.assign(new Error("Harness message must be an object"), { code: "invalid_message" });
  }
  if (value.protocol?.name !== PROTOCOL_NAME) {
    throw Object.assign(new Error("Harness protocol name is missing or invalid"), {
      code: "invalid_message",
    });
  }
  if (value.protocol?.version !== PROTOCOL_VERSION) {
    throw Object.assign(
      new Error(`Unsupported Harness protocol version: ${String(value.protocol?.version)}`),
      { code: "unsupported_version" },
    );
  }
  const type = String(value.type ?? "");
  const requestId = String(value.request_id ?? "").trim();
  if (!type || !requestId || !value.payload || typeof value.payload !== "object" || Array.isArray(value.payload)) {
    throw Object.assign(new Error("Harness type, request_id and object payload are required"), {
      code: "invalid_message",
    });
  }
  return {
    type,
    requestId,
    sessionId: String(value.session_id ?? ""),
    payload: value.payload,
  };
}

function enqueueRun(message) {
  if (resolveNextRun) {
    const resolve = resolveNextRun;
    resolveNextRun = null;
    resolve(message);
  } else {
    queuedRuns.push(message);
  }
}

async function nextRun() {
  if (queuedRuns.length > 0) return queuedRuns.shift();
  if (streamClosed) return null;
  return new Promise((resolve) => {
    resolveNextRun = resolve;
  });
}

function rejectPendingRunOperations(message) {
  for (const pending of pendingToolResults.values()) pending.reject(new Error(message));
  pendingToolResults.clear();
  for (const pending of pendingTurnUpdates.values()) pending.reject(new Error(message));
  pendingTurnUpdates.clear();
}

function resetRunState() {
  activeAgent?.clearAllQueues();
  activeAgent?.abort();
  activeAgent = null;
  rejectPendingRunOperations("Pi run ended before the host operation completed");
}

function parseContextEnvelope(request) {
  const context = request?.context;
  if (!context || typeof context !== "object" || Array.isArray(context)) {
    throw new Error("Run request requires a context envelope object");
  }
  if (context.version !== 1) {
    throw new Error(`Unsupported context envelope version: ${String(context.version)}`);
  }
  if (!Array.isArray(context.messages) || !Array.isArray(context.tools)) {
    throw new Error("Context envelope messages and tools must be arrays");
  }
  if (!context.budget || typeof context.budget !== "object") {
    throw new Error("Context envelope budget must be an object");
  }
  if (!context.execution || typeof context.execution !== "object") {
    throw new Error("Context envelope execution must be an object");
  }
  return context;
}

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && (part.type === "text" || part.type === "thinking"))
    .map((part) => String(part.text ?? part.thinking ?? ""))
    .join("\n");
}

function contentToPiUser(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const blocks = [];
  for (const part of content) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text") {
      blocks.push({ type: "text", text: String(part.text ?? "") });
      continue;
    }
    if (part.type !== "image_url") continue;
    const rawUrl = typeof part.image_url === "string" ? part.image_url : part.image_url?.url;
    const match = /^data:([^;,]+);base64,(.+)$/s.exec(String(rawUrl ?? ""));
    if (match) {
      blocks.push({ type: "image", mimeType: match[1], data: match[2] });
    } else if (rawUrl) {
      blocks.push({ type: "text", text: `[External image omitted by restricted Pi harness: ${rawUrl}]` });
    }
  }
  return blocks.length > 0 ? blocks : "";
}

function zeroUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { ...ZERO_COST, total: 0 },
  };
}

function boolish(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  return ["1", "true", "yes", "on", "ok", "success"].includes(String(value ?? "").trim().toLowerCase());
}

function resultIsError(content) {
  try {
    const parsed = JSON.parse(String(content ?? ""));
    return Object.hasOwn(parsed, "success") && !boolish(parsed.success);
  } catch {
    return false;
  }
}

function convertOpenAIMessages(messages, model) {
  const converted = [];
  const toolNamesById = new Map();
  let timestamp = Date.now() - Math.max(0, messages.length) * 2;
  for (const message of messages) {
    timestamp += 1;
    const role = message?.role;
    if (role === "system") continue;
    if (role === "user") {
      converted.push({ role: "user", content: contentToPiUser(message.content), timestamp });
      continue;
    }
    if (role === "assistant") {
      const blocks = [];
      const text = textFromContent(message.content);
      if (text) blocks.push({ type: "text", text });
      for (const call of message.tool_calls ?? []) {
        const id = String(call.id ?? `historical-${timestamp}-${blocks.length}`);
        const name = String(call.function?.name ?? call.name ?? "");
        let args = call.function?.arguments ?? call.arguments ?? {};
        if (typeof args === "string") {
          try {
            args = JSON.parse(args || "{}");
          } catch {
            args = {};
          }
        }
        toolNamesById.set(id, name);
        blocks.push({ type: "toolCall", id, name, arguments: args ?? {} });
      }
      converted.push({
        role: "assistant",
        content: blocks,
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: zeroUsage(),
        stopReason: blocks.some((block) => block.type === "toolCall") ? "toolUse" : "stop",
        timestamp,
      });
      continue;
    }
    if (role === "tool") {
      const toolCallId = String(message.tool_call_id ?? "");
      const content = String(message.content ?? "");
      converted.push({
        role: "toolResult",
        toolCallId,
        toolName: String(message.tool_name ?? toolNamesById.get(toolCallId) ?? "unknown_tool"),
        content: [{ type: "text", text: content }],
        details: {},
        isError: resultIsError(content),
        timestamp,
      });
    }
  }
  return converted;
}

function normalizeTool(tool) {
  const fn = tool?.function ?? tool ?? {};
  const parameters = fn.parameters && typeof fn.parameters === "object"
    ? fn.parameters
    : { type: "object", properties: {} };
  return {
    name: String(fn.name ?? ""),
    label: String(fn.name ?? "tool"),
    description: String(fn.description ?? ""),
    parameters: Type.Unsafe(parameters),
    executionMode: "sequential",
    async execute(toolCallId, params, signal) {
      if (signal?.aborted) throw new Error("Pi tool call aborted before host execution");
      sendRunEvent("tool_request", {
        tool_call_id: toolCallId,
        name: String(fn.name ?? ""),
        arguments: params ?? {},
      });
      const hostResult = await new Promise((resolve, reject) => {
        pendingToolResults.set(toolCallId, { resolve, reject });
        signal?.addEventListener(
          "abort",
          () => {
            if (pendingToolResults.delete(toolCallId)) reject(new Error("Pi tool call aborted"));
          },
          { once: true },
        );
      });
      const content = String(hostResult.content ?? "");
      if (hostResult.is_error) throw new Error(content || `Host tool failed: ${fn.name}`);
      return {
        content: [{ type: "text", text: content }],
        details: { host: "python", toolCallId },
      };
    },
  };
}

function buildRealModel(request) {
  const spec = request.model ?? {};
  const api = resolveApiName(spec.api);
  return {
    id: String(spec.model ?? "gpt-4o"),
    name: String(spec.model ?? "gpt-4o"),
    api,
    provider: api === "anthropic-messages" ? "anthropic" : "cst-openai-compatible",
    baseUrl: String(spec.base_url ?? "").trim() || "https://api.openai.com/v1",
    reasoning: String(spec.thinking_level ?? "off") !== "off",
    input: ["text", "image"],
    cost: ZERO_COST,
    contextWindow: Number(spec.context_window ?? 128000),
    maxTokens: Number(spec.max_tokens ?? 8192),
    compat: spec.compat && typeof spec.compat === "object" ? spec.compat : {},
  };
}

function scriptedResponseToFaux(response) {
  const blocks = [];
  if (response?.text) blocks.push(fauxText(String(response.text)));
  const calls = response?.tool_calls ?? (response?.tool_call ? [response.tool_call] : []);
  for (const call of calls) {
    blocks.push(
      fauxToolCall(
        String(call.name ?? ""),
        call.arguments ?? {},
        { id: String(call.id ?? `call-${blocks.length + 1}`) },
      ),
    );
  }
  const content = blocks.length === 1 ? blocks[0] : blocks;
  const stopReason = String(response?.stop_reason ?? (calls.length > 0 ? "toolUse" : "stop"));
  return fauxAssistantMessage(content, { stopReason });
}

function assistantProtocolMessage(message) {
  const toolCalls = [];
  const text = [];
  for (const block of message.content ?? []) {
    if (block.type === "text") text.push(String(block.text ?? ""));
    if (block.type === "toolCall") {
      toolCalls.push({
        id: String(block.id ?? ""),
        type: "function",
        function: {
          name: String(block.name ?? ""),
          arguments: JSON.stringify(block.arguments ?? {}),
        },
      });
    }
  }
  const usage = message.usage ?? zeroUsage();
  return {
    role: "assistant",
    content: text.join(""),
    tool_calls: toolCalls,
    usage: {
      prompt_tokens:
        Number(usage.input ?? 0)
        + Number(usage.cacheRead ?? 0)
        + Number(usage.cacheWrite ?? 0),
      completion_tokens: Number(usage.output ?? 0),
      total_tokens: Number(usage.totalTokens ?? 0),
      cached_tokens: Number(usage.cacheRead ?? 0),
      cache_write_tokens: Number(usage.cacheWrite ?? 0),
      reasoning_tokens: Number(usage.reasoning ?? 0),
      cost: usage.cost ?? { ...ZERO_COST, total: 0 },
    },
    stop_reason: String(message.stopReason ?? "stop"),
  };
}

async function run(request, runMeta) {
  const context = parseContextEnvelope(request);
  const realModel = buildRealModel(request);
  let model = realModel;
  let streamFn;
  let getApiKey;
  let faux = null;
  if (Array.isArray(request.scripted_responses)) {
    faux = fauxProvider();
    const models = createModels();
    models.setProvider(faux.provider);
    faux.setResponses(request.scripted_responses.map(scriptedResponseToFaux));
    model = faux.getModel();
    streamFn = (selectedModel, context, options) => models.streamSimple(selectedModel, context, options);
  } else {
    const apiFactory = API_FACTORIES.get(realModel.api);
    const api = apiFactory();
    const cacheRetention = cacheRetentionFor(request.model);
    streamFn = (selectedModel, context, options) => api.streamSimple(
      selectedModel,
      context,
      { ...options, cacheRetention },
    );
    getApiKey = () => String(request.model?.api_key ?? "");
  }

  const systemParts = [String(request.system_prompt ?? "")];
  for (const message of context.messages) {
    if (message?.role === "system") systemParts.push(textFromContent(message.content));
  }
  const transcript = convertOpenAIMessages(context.messages, model);
  let promptMessage = null;
  if (transcript.at(-1)?.role === "user") promptMessage = transcript.pop();

  const maxTurns = Math.max(1, Number(request.limits?.max_turns ?? 16));
  let assistantTurns = 0;
  let limitReached = false;
  const tools = context.tools.map(normalizeTool).filter((tool) => tool.name);
  activeAgent = new Agent({
    streamFn,
    getApiKey,
    sessionId: String(runMeta.sessionId || runMeta.requestId),
    toolExecution: "sequential",
    steeringMode: "one-at-a-time",
    followUpMode: "one-at-a-time",
    initialState: {
      systemPrompt: systemParts.filter(Boolean).join("\n\n"),
      model,
      thinkingLevel: String(request.model?.thinking_level ?? "off"),
      tools,
      messages: transcript,
    },
    shouldStopAfterTurn: ({ message }) => {
      const hasToolCall = (message.content ?? []).some((block) => block.type === "toolCall");
      if (assistantTurns >= maxTurns && hasToolCall) {
        limitReached = true;
        return true;
      }
      return false;
    },
    prepareNextTurnWithContext: async ({ toolResults, context }) => {
      if (!Array.isArray(toolResults) || toolResults.length === 0) return undefined;
      const updateId = `turn-update-${assistantTurns}`;
      sendRunEvent("prepare_next_turn", { update_id: updateId, turn_index: assistantTurns });
      const update = await new Promise((resolve, reject) => {
        pendingTurnUpdates.set(updateId, { resolve, reject });
      });
      return {
        context: {
          ...context,
          tools: (update.tools ?? []).map(normalizeTool).filter((tool) => tool.name),
        },
      };
    },
  });

  const unsubscribe = activeAgent.subscribe((event) => {
    if (event.type === "message_end" && event.message?.role === "assistant") {
      assistantTurns += 1;
      sendRunEvent("assistant_message", {
        turn_index: assistantTurns,
        message: assistantProtocolMessage(event.message),
      });
    }
    if (event.type === "turn_end" && Array.isArray(event.toolResults) && event.toolResults.length > 0) {
      sendRunEvent("tool_batch_end", {
        turn_index: assistantTurns,
        assistant_content: textFromContent(event.message?.content),
        tool_names: event.toolResults.map((result) => String(result.toolName ?? "")),
      });
    }
  });

  try {
    if (promptMessage) {
      await activeAgent.prompt(promptMessage);
    } else if (activeAgent.state.messages.at(-1)?.role === "toolResult") {
      await activeAgent.continue();
    } else {
      await activeAgent.prompt({
        role: "user",
        content: String(context.execution.continuation_prompt ?? "Continue the current task."),
        timestamp: Date.now(),
      });
    }
  } finally {
    unsubscribe();
  }

  const assistants = activeAgent.state.messages.filter((message) => message.role === "assistant");
  const finalAssistant = assistants.at(-1);
  const protocolFinal = finalAssistant ? assistantProtocolMessage(finalAssistant) : null;
  const stopReason = String(protocolFinal?.stop_reason ?? "error");
  const errorText = String(activeAgent.state.errorMessage ?? finalAssistant?.errorMessage ?? "");
  const ok = !limitReached && !["error", "aborted"].includes(stopReason) && !errorText;
  return {
    ok,
    final_text: String(protocolFinal?.content ?? ""),
    stop_reason: stopReason,
    limit_reached: limitReached,
    error: errorText,
    error_envelope: ok || !errorText ? null : providerErrorEnvelope(errorText, runMeta.requestId),
    assistant_turns: assistantTurns,
    provider_calls: faux?.state?.callCount ?? null,
  };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });

rl.on("line", (line) => {
  let raw;
  try {
    raw = JSON.parse(line);
  } catch (error) {
    sendHarnessError(
      "",
      "protocol_error",
      `Invalid JSON from Python host: ${error.message}`,
      { line_preview: String(line).slice(0, 300) },
    );
    return;
  }
  let message;
  try {
    message = parseEnvelope(raw);
  } catch (error) {
    sendHarnessError(
      String(raw?.request_id ?? ""),
      String(error?.code ?? "protocol_error"),
      error instanceof Error ? error.message : String(error),
    );
    return;
  }

  if (message.type === "hello") {
    if (handshakeComplete) {
      sendHarnessError(message.requestId, "protocol_error", "Harness handshake already completed");
      return;
    }
    handshakeComplete = true;
    workerState = "idle";
    sendEnvelope(
      "hello_ack",
      message.requestId,
      {
        runtime: { name: "pi-agent-core", version: "0.84.1" },
        capabilities: CAPABILITIES,
      },
      message.sessionId,
    );
    return;
  }

  if (!handshakeComplete) {
    sendHarnessError(message.requestId, "protocol_error", "Harness handshake is required before other messages");
    return;
  }

  if (message.type === "health") {
    sendEnvelope(
      "health_result",
      message.requestId,
      {
        ok: workerState === "idle" || workerState === "running",
        status: workerState,
        active_runs: activeRun ? 1 : 0,
      },
      message.sessionId,
    );
    return;
  }

  if (message.type === "run") {
    if (workerState !== "idle") {
      sendHarnessError(
        message.requestId,
        "protocol_error",
        `Sidecar is busy and cannot accept a run while state=${workerState}`,
      );
      return;
    }
    workerState = "running";
    activeRun = {
      requestId: message.requestId,
      sessionId: message.sessionId,
    };
    enqueueRun(message);
    return;
  }

  if (!activeRun || message.requestId !== activeRun.requestId) {
    sendHarnessError(
      message.requestId,
      "correlation_mismatch",
      "Message request_id does not match the active run",
      { expected_request_id: activeRun?.requestId ?? null, received_request_id: message.requestId },
    );
    return;
  }

  if (message.type === "tool_result") {
    const toolCallId = String(message.payload.tool_call_id ?? "");
    const pending = pendingToolResults.get(toolCallId);
    if (pending) {
      pendingToolResults.delete(toolCallId);
      pending.resolve(message.payload);
    } else {
      sendHarnessError(message.requestId, "protocol_error", `Unknown tool_call_id: ${toolCallId}`);
    }
    return;
  }
  if (message.type === "turn_update") {
    const updateId = String(message.payload.update_id ?? "");
    const pending = pendingTurnUpdates.get(updateId);
    if (pending) {
      pendingTurnUpdates.delete(updateId);
      pending.resolve(message.payload);
    } else {
      sendHarnessError(message.requestId, "protocol_error", `Unknown update_id: ${updateId}`);
    }
    return;
  }
  if (message.type === "abort") {
    activeAgent?.abort();
    return;
  }
  if (message.type === "steer") {
    if (!activeAgent) {
      sendHarnessError(message.requestId, "protocol_error", "No active Pi Agent accepts steering");
      return;
    }
    activeAgent.steer({
      role: "user",
      content: String(message.payload.message ?? ""),
      timestamp: Date.now(),
    });
    return;
  }
  if (message.type === "follow_up") {
    if (!activeAgent) {
      sendHarnessError(message.requestId, "protocol_error", "No active Pi Agent accepts follow-up");
      return;
    }
    activeAgent.followUp({
      role: "user",
      content: String(message.payload.message ?? ""),
      timestamp: Date.now(),
    });
    return;
  }
  sendHarnessError(message.requestId, "unknown_message_type", `Unknown Harness message type: ${message.type}`);
});

rl.on("close", () => {
  streamClosed = true;
  if (resolveNextRun) {
    const resolve = resolveNextRun;
    resolveNextRun = null;
    resolve(null);
  }
  rejectPendingRunOperations("Python host closed the Pi protocol stream");
  activeAgent?.abort();
});

while (!streamClosed) {
  const runMessage = await nextRun();
  if (!runMessage) break;
  const runCorrelation = {
    requestId: runMessage.requestId,
    sessionId: runMessage.sessionId,
  };
  let resultPayload;
  try {
    resultPayload = await run(runMessage.payload, runCorrelation);
  } catch (error) {
    const providerError = providerErrorEnvelope(error, runCorrelation.requestId);
    const looksLikeProviderFailure = providerError.code !== "unknown";
    const errorEnvelope = looksLikeProviderFailure
      ? providerError
      : harnessError(
        "protocol_error",
        error instanceof Error ? error.message : String(error),
        {},
        runCorrelation.requestId,
      );
    resultPayload = {
      ok: false,
      final_text: "",
      stop_reason: "error",
      limit_reached: false,
      error: errorEnvelope.message,
      error_envelope: errorEnvelope,
    };
  } finally {
    resetRunState();
    activeRun = null;
    workerState = handshakeComplete ? "idle" : "starting";
  }
  sendEnvelope(
    "result",
    runCorrelation.requestId,
    resultPayload,
    runCorrelation.sessionId,
  );
}

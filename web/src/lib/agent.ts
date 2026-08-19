// Agent request pipeline, ported from share/ui.html runAgentRequest
// (1072-1115) and showAgentWaiting (1056-1070).
//
// showAgentWaiting no longer builds DOM here: Waiting.svelte renders the
// .waiting indicator whenever a message has status === "planning", and its
// elapsed timer is anchored by waitingAnchor() below so a chat reopened
// mid-flight keeps counting from the original start instead of restarting at
// zero. runAgentRequest only flips fields on the record object; Waiting and
// MessageList render those fields.
import { get } from "svelte/store";
import { chats, activeAgentRequests, commitMessageTransition } from "./stores/chats";
import { busy } from "./stores/ui";
import { errorMessage, type AgentMessage } from "./domain";

interface AgentResult {
  content?: string;
  tasks?: string[];
  error?: string;
}

// showAgentWaiting(b, startedAt): the anchor used for the elapsed readout.
export function waitingAnchor(record: AgentMessage): number {
  return record.startedAt || Date.now();
}

export async function runAgentRequest(record: AgentMessage, chatId: string | null): Promise<void> {
  if (!record.startedAt) { record.startedAt = Date.now(); commitMessageTransition(chatId); }
  if (activeAgentRequests.has(record.requestId)) {
    // Reopened mid-flight: the guard blocks a duplicate request, but the live
    // waiting state must still show — anchored to the original start, or the
    // elapsed readout restarts from zero on every chat switch. (Waiting is
    // rendered by MessageList purely from record.status === "planning", so
    // nothing to do here but bail.)
    return;
  }
  activeAgentRequests.add(record.requestId);
  // was: busy = true; $("send").disabled = true — Composer binds #send's
  // disabled state to the same ui-store flag.
  busy.set(true);
  const chat = get(chats).find(c => c.id === chatId);
  const apiMessages = (chat?.messages || []).filter(m =>
    m.status !== "planning" && !m.kind && !m.mode
  ).map(m => ({role:m.role, content:m.content}));
  const started = record.startedAt;
  try {
    const resp = await fetch("/mira/agent", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({messages: apiMessages,
                           enable_thinking: localStorage.getItem("argus.thinking") === "on",
                           request_id: record.requestId}),
    });
    if (!resp.ok) {
      const failure = await resp.json() as AgentResult;
      throw new Error(failure.error || `HTTP ${resp.status}`);
    }
    const result = await resp.json() as AgentResult;
    record.content = result.content || "任务已处理。";
    record.tasks = result.tasks || [];
    record.status = "complete";
    record.elapsed = ((Date.now() - started) / 1000).toFixed(1);
  } catch (err: unknown) {
    record.content = "⚠ " + errorMessage(err);
    record.status = "failed";
  } finally {
    activeAgentRequests.delete(record.requestId);
    commitMessageTransition(chatId);
    busy.set(false);
    // Re-render the conversation if the user is still on it: openChat sets a
    // fresh messages array so MessageList re-reads the mutated record (the
    // Waiting indicator disappears because status is no longer "planning").
    document.getElementById("input")?.focus();
  }
}

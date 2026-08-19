// Chat core, ported from share/ui.html:378-524. localStorage key "argus.chats"
// is unchanged (renaming would lose every existing conversation).
// renderChats() from the legacy file is gone — Sidebar.svelte derives its rows
// from the `chats` store, so every place that used to call renderChats() now
// just sets a new array reference on the store.
import { get, writable } from "svelte/store";
import { showView } from "./ui";

export interface Chat { id: string; title: string; messages: any[] }
export const chats = writable<Chat[]>([]);
export const curId = writable<string | null>(null);
export const messages = writable<any[]>([]);
export const activeAgentRequests = new Set<string>();

export async function loadChats(): Promise<void> {
  try {
    const saved = JSON.parse(localStorage.getItem("argus.chats") || "[]");
    chats.set(Array.isArray(saved) ? saved : []);
  } catch { chats.set([]); }
}

export function titleMatchesKind(title: string, kind: string): boolean {
  const words: Record<string, RegExp | undefined> = {
    image: /图片|图像|画|照片|image/i,
    music: /歌|音乐|歌曲|配乐|music|song/i,
    video: /视频|影片|动画|video/i,
  };
  return words[kind]?.test(title || "");
}

export async function recoverUnlinkedTasks(): Promise<void> {
  try {
    const tasks = await fetch("/mira/jobs").then(r => r.json());
    const list = get(chats);
    const linked = new Set(list.flatMap(c => c.messages.flatMap(m => m.tasks || [])));
    let changed = false;
    for (const task of tasks) {
      if (linked.has(task.id)) continue;
      const createdMs = Number(task.created) * 1000;
      const candidate = list.find(c => {
        const last = c.messages[c.messages.length - 1];
        const chatMs = Number(c.id);
        return last?.role === "user" && !last.requestId && titleMatchesKind(c.title, task.kind)
          && Number.isFinite(chatMs) && createdMs >= chatMs - 30_000
          && createdMs - chatMs < 10 * 60_000;
      });
      if (!candidate) continue;
      candidate.messages.push({role:"assistant", content:"已恢复后台生成任务。",
                               tasks:[task.id], status:"complete"});
      linked.add(task.id); changed = true;
    }
    if (changed) {
      saveChats();
      chats.set([...list]); // was: renderChats()
      const cur = get(curId);
      if (cur) openChat(cur);
    }
  } catch { /* task service may still be starting */ }
}

export function saveChats(): void {
  // data: URLs from screenshots can exceed localStorage's small quota and used
  // to make send() throw before the request even left the browser. Keep full
  // images in the live conversation, but persist a text-safe history.
  const compact = get(chats).slice(0, 50).map(c => ({...c, messages: c.messages.map(m => ({
    ...m,
    content: Array.isArray(m.content)
      ? m.content.filter(p => p.type !== "image_url")
      : m.content,
  }))}));
  while (compact.length) {
    try {
      localStorage.setItem("argus.chats", JSON.stringify(compact));
      return;
    } catch { compact.pop(); }
  }
  try { localStorage.removeItem("argus.chats"); } catch { /* storage unavailable */ }
}

export function chatTitle(msgs: any[]): string {
  const first = msgs.find(m => m.role === "user");
  if (!first) return "New chat";
  const t = typeof first.content === "string"
    ? first.content
    : (first.content.find(p => p.type === "text")?.text || "Image");
  return t.slice(0, 40) || "Image";
}

export function persistCurrent(): void {
  const msgs = get(messages);
  if (!msgs.length) return;
  const title = chatTitle(msgs);
  const list = get(chats);
  const current = get(curId);
  const existing = list.find(c => c.id === current);
  if (existing) {
    existing.messages = msgs;
    existing.title = title;
    chats.set([existing, ...list.filter(c => c.id !== current)]);
  } else {
    const id = current || String(Date.now());
    curId.set(id);
    chats.set([{id, title, messages: msgs}, ...list]);
  }
  saveChats();
}

export function newChat(): void {
  // Legacy also reset #moduleTitle (now derived from the view store), the
  // composer images/thumbs, restored the #log hero and focused #input.
  messages.set([]);
  curId.set(null);
  // The composer state (images array, thumbs) is owned by Composer.svelte;
  // it listens for this event and clears itself. The hero reappears because
  // MessageList renders Hero when messages is empty.
  window.dispatchEvent(new CustomEvent("mira:newchat"));
  document.getElementById("input")?.focus();
}

export function showChat(): void {
  // was: body.classList.remove("launching", "iterating") + moduleTitle text
  showView("chat");
}

export function openChat(id: string): void {
  const c = get(chats).find(x => x.id === id);
  if (!c) return;
  // was: body.classList.remove("launching", "iterating") + moduleTitle text
  showView("chat");
  curId.set(id);
  // New array reference so the store always notifies; the message records
  // themselves are shared by reference so in-place mutations (startedAt,
  // status, url, …) land on the objects saveChats() persists.
  messages.set([...c.messages]);
  // The legacy per-message dispatch (renderMedia / resumeMedia /
  // renderTaskCard / runAgentRequest for planning messages) is driven by the
  // components that render each message — see MessageList.svelte, MediaCard,
  // TaskCard and Waiting.
}

// Replaces the render pass of legacy renderChats()/openChat() for flows that
// mutate a message record in place (runMedia, runAgentRequest): kicks the
// messages store with a fresh array of the same record references so
// MessageList re-reads every record field.
export function refreshMessages(): void {
  messages.update(m => [...m]);
}

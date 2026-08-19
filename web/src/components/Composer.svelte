<script module lang="ts">
  // Thinking toggle — sent per request, no server restart needed
  // (share/ui.html:368-376). Persisted in localStorage "argus.thinking"
  // ("on"/"off") exactly like the legacy code; lib/agent.ts reads this store
  // (or localStorage, which is written on every toggle) for enable_thinking.
  import { writable } from "svelte/store";
  export const thinking = writable(localStorage.getItem("argus.thinking") === "on");
</script>

<script lang="ts">
  // Composer — markup from share/ui.html:318-341, behavior from 1131-1172.
  import { onMount, onDestroy, tick } from "svelte";
  import { get } from "svelte/store";
  import { model, botNotices, botBubble } from "../lib/stores/models";
  import { ready, lastHealth, startupText, pollReady, startPolling } from "../lib/stores/status";
  import { busy } from "../lib/stores/ui";
  import { curId, appendMessage } from "../lib/stores/chats";
  // lib/agent.ts (chat-core) — expected export:
  //   runAgentRequest(record, bubble?, chatId)  [legacy arity; DOM bubble arg
  //   is dead in Svelte — passed as undefined, record is the render source]
  import { runAgentRequest } from "../lib/agent";
  import type { AgentMessage, MessageContent } from "../lib/domain";
  import Thumbs, { images, fileToImage } from "./Thumbs.svelte";
  import ModelPicker from "./ModelPicker.svelte";

  let draft = "";
  let inputEl: HTMLTextAreaElement;
  let fileEl: HTMLInputElement;

  // textarea autosize (ui.html:1168-1171)
  function autosize() {
    if (!inputEl) return;
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + "px";
  }

  // think chip toggle (ui.html:371-375) — paintThink() is the class:on binding
  function toggleThink() {
    const next = !get(thinking);
    thinking.set(next);
    localStorage.setItem("argus.thinking", next ? "on" : "off");
  }
  // module-scope store mirrored for $-auto-subscription in the template
  const thinkOn = thinking;

  // legacy $("attach").onclick → file picker (ui.html:1028)
  function onFileChange(e: Event) {
    const t = e.target as HTMLInputElement;
    [...(t.files || [])].forEach(fileToImage);
    t.value = "";
  }

  // send() — ui.html:1131-1163 verbatim (DOM → stores)
  async function send() {
    const text = draft.trim();
    const imgsNow = get(images);
    if (get(busy) || (!text && !imgsNow.length) || !get(model)) return;
    // don't fire into a server that is still downloading or loading — say what it's doing
    if (!get(ready)) {
      await pollReady();
      if (!get(ready)) {
        const health: any = get(lastHealth);
        const stale = health.stale_gb > 1
          ? ` (${health.stale_gb} GB of superseded partials will be cleaned automatically)` : "";
        botBubble(`The model isn't ready yet — ${startupText(health)}.` + stale +
          ` Your message is still in the box; send it once the dot turns green.`, true);
        return;
      }
    }
    busy.set(true);
    const imgs = imgsNow.slice();
    images.set([]);
    draft = "";
    if (inputEl) inputEl.style.height = "auto";
    // bubble("user", text, imgs) — the user bubble is rendered by MessageList
    // from the message record pushed below (content carries the images).
    const content: MessageContent = imgs.length
      ? [...imgs.map(u => ({type: "image_url" as const, image_url: {url: u}})), {type: "text" as const, text: text || "Describe this image."}]
      : text;
    const requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    appendMessage({role: "user", content, requestId});
    const chatId = get(curId);
    const record: AgentMessage = {role: "assistant", content: "", tasks: [], status: "planning", requestId};
    appendMessage(record);
    await runAgentRequest(record, chatId);
  }

  // keydown (ui.html:1165-1167): Enter = newline, Cmd/Ctrl+Enter = send
  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
  }

  // Hero → composer prefill (legacy useSuggestion: value + focus + input event)
  function onPrefill(e: any) {
    const v = typeof e?.detail === "string" ? e.detail : e?.detail?.text;
    if (typeof v === "string") {
      draft = v;
      tick().then(() => { autosize(); inputEl?.focus(); });
    }
  }

  // Legacy refocuses the input after a request finishes
  // (runAgentRequest finally / sendMedia: $("input").focus()).
  let prevBusy = false;
  $: if (prevBusy && !$busy) inputEl?.focus();
  $: prevBusy = $busy;

  // Notices are ephemeral in legacy (openChat re-rendered #log and wiped
  // them); clearing on chat switch reproduces that. Re-opening the same chat
  // keeps them here (legacy would wipe — accepted deviation).
  onMount(() => {
    inputEl?.focus(); // ui.html:1172
    startPolling();   // ui.html:1006-1007 — idempotent; App.svelte may also call it
    window.addEventListener("mira:prefill", onPrefill as EventListener);
    let previous = get(curId);
    const stop = curId.subscribe(id => {
      if (id !== previous) botNotices.set([]);
      previous = id;
    });
    return () => stop();
  });
  onDestroy(() => window.removeEventListener("mira:prefill", onPrefill as EventListener));
</script>

<div id="composer">
  {#each $botNotices as n (n.id)}
    <div class="msg bot"><div class="bubble" class:notice={n.notice}>{n.text}</div></div>
  {/each}
  <div id="card">
    <Thumbs />
    <textarea id="input" rows="1" placeholder="发送消息…" bind:this={inputEl} bind:value={draft} oninput={autosize} onkeydown={onKeydown}></textarea>
    <!-- kept for parity: legacy #lyrics (display:none unless .show; music
         payload reads $("lyrics")?.value) — never shown by current markup -->
    <textarea id="lyrics"></textarea>
    <div id="tools">
      <button class="round" id="attach" title="Attach image" onclick={() => fileEl.click()}>＋</button>
      <input type="file" id="file" accept="image/*" multiple hidden bind:this={fileEl} onchange={onFileChange} />
      <button id="think" title="Let the model think before answering" class:on={$thinkOn} onclick={toggleThink}>
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"
          stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.2a4.3 4.3 0 0 0-4.3 4.3c0 1.2.5 2.2 1.3 3 .5.5.8 1.1.8 1.8v.7h4.4v-.7c0-.7.3-1.3.8-1.8a4.2 4.2 0 0 0 1.3-3A4.3 4.3 0 0 0 10 3.2z"/><line x1="8.2" y1="16.4" x2="11.8" y2="16.4"/></svg>
        Think
      </button>
      <span class="shortcut send-shortcut">Enter 换行 · ⌘↵ 发送</span>
      <ModelPicker />
      <button id="send" title="Send" disabled={$busy} onclick={send}>↑</button>
    </div>
  </div>
</div>

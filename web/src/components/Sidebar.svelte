<script lang="ts">
  // nav#side, ported from ui.html:262-291 + its handlers (349-366, 441-464, 521).
  import { get } from "svelte/store";
  import { chats, curId, newChat, showChat, openChat, saveChats } from "../lib/stores/chats";
  import { sidebarHidden, showView } from "../lib/stores/ui";
  import { lastHealth } from "../lib/stores/status";

  // setSidebar() from ui.html:358-363 — App.svelte mirrors the store onto
  // document.body ("side-hidden") so the legacy CSS keeps working.
  function setSidebar(hidden: boolean) {
    sidebarHidden.set(hidden);
    localStorage.setItem("argus.side", hidden ? "hidden" : "shown");
  }
  // setSidebar(localStorage.getItem("argus.side") === "hidden") at script load
  sidebarHidden.set(localStorage.getItem("argus.side") === "hidden");

  function onNewChat() { showChat(); newChat(); }           // $("newChat").onclick
  function onLaunch() { showView("launch"); }               // launchBtn → renderLaunch + body.launching + title
  function onIterations() { showView("iterations"); }       // iterationBtn → renderIterations + body.iterating + title
  function onOpenLog() { fetch("/argus/openlog", {method: "POST"}); }

  // chat row delete — renderChats()'s del.onclick, verbatim
  function deleteChat(e: MouseEvent, c: {id: string}) {
    e.stopPropagation();
    chats.set(get(chats).filter(x => x.id !== c.id));
    saveChats();
    if (c.id === get(curId)) newChat(); // else: renderChats() is now reactive
  }

  // pollReady()'s apiFoot update (ui.html:991-992), derived from lastHealth
  $: apiFootText = (() => {
    const h = $lastHealth;
    if (!h || !h.api_port) return "";
    const apiHost = h.api_host === "0.0.0.0" ? location.hostname : h.api_host;
    return `http://${apiHost}:${h.api_port}/v1`;
  })();
</script>

<nav id="side" class:hidden={$sidebarHidden}>
  <div id="sideTop">
    <span class="brand-feather" aria-hidden="true"><img src="/mira/icon.png" alt=""></span>
    <span class="brand-name">Mira</span>
    <button class="icon-btn" id="toggleIn" title="Collapse sidebar" on:click={() => setSidebar(true)}>
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">
        <rect x="2.2" y="3.4" width="15.6" height="13.2" rx="2.6"/>
        <line x1="7.9" y1="3.4" x2="7.9" y2="16.6"/>
      </svg>
    </button>
  </div>
  <button class="side-btn" id="newChat" on:click={onNewChat}>
    <span class="ic"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round"><path d="M10 4H5.5A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16h9A1.5 1.5 0 0 0 16 14.5V10"/><path d="M13.4 3.6a1.4 1.4 0 0 1 2 2L10.6 10.4l-2.6.6.6-2.6z"/></svg></span>新对话
  </button>
  <button class="side-btn" id="launchBtn" on:click={onLaunch}>
    <span class="ic"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round"><path d="M8.4 12.5 4 11l2.2-2.6 3.1.4"/><path d="M7.5 11.6c1.6-4 4.6-7 8.9-8.1.4 4.4-2.1 7.9-5.3 10.2z"/><path d="M7.5 15.6 4.4 16l.4-3.1"/></svg></span>连接工具
  </button>
  <button class="side-btn" id="iterationBtn" on:click={onIterations}>
    <span class="ic"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M15.8 7.2A6 6 0 0 0 5 5.1L3.5 7"/><path d="M3.5 3.8V7H7"/><path d="M4.2 12.8A6 6 0 0 0 15 14.9l1.5-1.9"/><path d="M16.5 16.2V13H13"/></svg></span>迭代中心
  </button>
  <div class="label">对话记录</div>
  <div id="chats">
    {#each $chats as c (c.id)}
      <div class="chat-row" class:cur={c.id === $curId} on:click={() => openChat(c.id)}>
        <span class="t">{c.title}</span>
        <button class="del" on:click={e => deleteChat(e, c)}>✕</button>
      </div>
    {/each}
  </div>
  <button class="side-btn" id="openLog" on:click={onOpenLog}>
    <span class="ic"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="3" width="13" height="14" rx="2"/><line x1="6.5" y1="7" x2="13.5" y2="7"/><line x1="6.5" y1="10" x2="13.5" y2="10"/><line x1="6.5" y1="13" x2="11" y2="13"/></svg></span>运行日志
  </button>
  <div class="foot" id="apiFoot">{apiFootText}</div>
</nav>

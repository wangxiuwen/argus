<script lang="ts">
  // Launch view — ported verbatim from share/ui.html:526-590 (LAUNCH cards +
  // renderLaunch). Legacy showed this pane via body.launching set by the
  // #launchBtn click handler (ui.html:585-590); here the chat-core Sidebar
  // sets the view store, App.svelte mirrors it onto the body class, and this
  // component fetches /argus/config and renders the cards on entry.
  import { onMount } from "svelte";
  import { view } from "../lib/stores/ui";
  import { model } from "../lib/stores/models";

  interface LaunchItem { icon: string; name: string; desc: string; cmd: string }

  const LAUNCH = (api: string): LaunchItem[] => [
    {icon: "⌨︎", name: "curl", desc: "Check the endpoint from a shell",
     cmd: `curl ${api}/chat/completions -H 'Content-Type: application/json' \\\n  -d '{"model":"MODEL","messages":[{"role":"user","content":"hello"}]}'`},
    {icon: "🐍", name: "OpenAI Python SDK", desc: "Any script written against the OpenAI client",
     cmd: `OPENAI_BASE_URL=${api} OPENAI_API_KEY=mira python your_script.py`},
    {icon: "⬢", name: "OpenAI Node SDK", desc: "Same for JavaScript and TypeScript",
     cmd: `OPENAI_BASE_URL=${api} OPENAI_API_KEY=mira node your_script.mjs`},
    {icon: "⌘", name: "Codex CLI", desc: "OpenAI's coding agent against your local model",
     cmd: `mira launch codex`},
    {icon: "✎", name: "aider", desc: "Pair programming in your terminal",
     cmd: `mira launch aider`},
    {icon: "◇", name: "OpenCode", desc: "Open-source coding agent against your local model",
     cmd: `mira launch opencode`},
    {icon: "✦", name: "Claude Code", desc: "Anthropic's coding agent in local-friendly bare mode",
     cmd: `mira launch claude`},
    {icon: "⚙", name: "Any OpenAI-compatible app", desc: "Open WebUI, Continue, Zed, Raycast — paste these two values",
     cmd: `Base URL: ${api}\nAPI key:  mira   (any non-empty string works)`},
  ];

  // Cards with MODEL substituted at render time. Legacy kept the previous
  // list visible until the fresh /argus/config fetch resolved, and never
  // re-substituted on a later model switch until the view was re-entered.
  let cards: LaunchItem[] = [];
  // transient "Copied" label per card, reset like the legacy DOM rebuild
  let copied: Record<number, boolean> = {};

  async function renderLaunch() {
    const {config} = await fetch("/argus/config").then(r => r.json());
    const host = config.HOST === "0.0.0.0" ? location.hostname : config.HOST;
    const api = `http://${host}:${config.PORT}/v1`;
    cards = LAUNCH(api).map(item => ({...item, cmd: item.cmd.replaceAll("MODEL", $model || "MODEL")}));
    copied = {};
  }

  async function copyCmd(cmd: string, index: number) {
    await navigator.clipboard.writeText(cmd);
    copied = { ...copied, [index]: true };
    setTimeout(() => { copied = { ...copied, [index]: false }; }, 1500);
  }

  // Legacy set the title inside the #launchBtn click handler (ui.html:589);
  // #moduleTitle lives in TopBar, so reach it the same imperative way.
  function setModuleTitle(text: string) {
    const el = document.getElementById("moduleTitle");
    if (el) el.textContent = text;
  }

  // View entry. subscribe() also replays the current value at mount, so
  // mounting straight into the launch view (e.g. a future deep link) renders.
  onMount(() => {
    const unsubscribe = view.subscribe(v => {
      if (v === "launch") { setModuleTitle("连接工具"); renderLaunch(); }
    });
    return unsubscribe;
  });
</script>

{#if $view === "launch"}
<div id="launch">
  <div class="wrap">
    <h2>Launch</h2>
    <div class="sub">复制命令到终端运行；编码工具会通过 Mira 本地桥接服务使用当前模型。</div>
    <div id="cards">
      {#each cards as item, i}
        <div class="card">
          <div class="logo">{item.icon}</div>
          <div class="info">
            <div class="name">{item.name}</div>
            <div class="desc">{item.desc}</div>
            <div class="cmd">
              <code>{item.cmd}</code>
              <button class="copy" onclick={() => copyCmd(item.cmd, i)}>{copied[i] ? "Copied" : "Copy"}</button>
            </div>
          </div>
        </div>
      {/each}
    </div>
  </div>
</div>
{/if}

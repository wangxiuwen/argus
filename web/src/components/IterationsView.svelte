<script lang="ts">
  // Iterations view — ported verbatim from share/ui.html:592-632
  // (candidateLabel / candidateApproval / renderIterations, plus the view
  // entry at ui.html:631-632). The chat-core Sidebar's 迭代中心 button sets
  // the view store; App.svelte mirrors body.iterating; this component fetches
  // /mira/iterations and renders on entry, keeping the legacy 3s auto-refresh
  // chain (including its yank-back when a candidate is still queued/preparing/
  // running/publishing and the user has navigated away).
  import { onMount } from "svelte";
  import { view } from "../lib/stores/ui";
  import { mediaJson } from "../lib/api";

  type CandidateStatus = "queued" | "preparing" | "running" | "ready" | "insufficient_data" | "complete" | "failed" | "applied" | "publishing" | "published" | "publish_failed";
  interface Candidate { id: string; kind: "code" | "lora"; status: CandidateStatus; error?: string; summary?: string; goal?: string; pr_url?: string }
  interface Preference { name: string; value: string }
  interface IterationOverview { preferences: Preference[]; feedback: { total?: number; learnable?: number; average?: number }; candidates: Candidate[] }
  interface CandidateRow { c: Candidate; label: string; meta: string; approve: { action: string; label: string } | null }

  function candidateLabel(candidate: Candidate) {
    const kind = candidate.kind === "code" ? "源码" : "模型适配";
    const states: Record<CandidateStatus, string> = {queued:"排队中",preparing:"准备中",running:"运行中",ready:"待批准",insufficient_data:"数据不足",complete:"已完成",failed:"失败",applied:"已应用",publishing:"正在发布 PR",published:"PR 已创建",publish_failed:"发布失败"};
    return `${kind}候选 · ${states[candidate.status]}`;
  }

  async function candidateApproval(id: string, action: string) {
    await mediaJson(`/mira/candidates/${id}/${action}`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({confirmed:true}),
    });
    renderIterations();
  }

  // Display state. Legacy rebuilt #iterationContent's DOM on every render
  // cycle, so all transient state (loading text, the goal input, approve
  // button arming) reset with each cycle; the resets below mirror that.
  let loading = true;                  // legacy initial markup read 正在读取…
  let loadError: string | null = null;
  let prefCount = 0;
  let prefsText = "";
  let fbTotal: any = 0;
  let fbLearnable: any = 0;
  let fbAvg: any = "—";
  let rows: CandidateRow[] = [];       // candidate rows, derived inside the try
  let codeGoal = "";
  let armed: Record<string, boolean> = {};    // two-step confirm: first click
  let working: Record<string, boolean> = {};  // approval POST in flight
  let failed: Record<string, string> = {};    // approval POST failed

  // plain mirror of the view store for timer callbacks (no redependency)
  let currentView: string = "chat";

  function setModuleTitle(text: string) {
    const el = document.getElementById("moduleTitle");
    if (el) el.textContent = text;
  }

  function approveAction(c: Candidate): { action: string; label: string } | null {
    let action: string | null = null, label = "";
    if(c.kind==="code"&&["ready","applied","publish_failed"].includes(c.status)){action="publish";label=c.status==="publish_failed"?"重新批准发布 PR":"批准发布 GitHub PR";}
    if(c.kind==="lora"&&c.status==="ready"){action="start";label="批准开始训练";}
    return action ? { action, label } : null;
  }

  async function renderIterations() {
    setModuleTitle("迭代中心");
    loading = true;                    // legacy wiped the box to 正在读取… up front
    loadError = null;
    armed = {}; working = {}; failed = {}; codeGoal = "";
    try {
      const data = await mediaJson<IterationOverview>("/mira/iterations");
      // Shape the display model where the legacy DOM build happened — inside
      // the same try, so malformed payloads land in the same catch.
      prefCount = data.preferences.length;
      prefsText = data.preferences.map(p=>`${p.name}：${p.value}`).join("\n") || "还没有明确偏好；可在产物下方点“记住并改进”。";
      fbTotal = data.feedback.total || 0;
      fbLearnable = data.feedback.learnable || 0;
      fbAvg = data.feedback.average ?? "—";
      rows = data.candidates.map(c => ({
        c,
        label: candidateLabel(c),
        meta: c.error || c.summary || c.goal || "",
        approve: approveAction(c),
      }));
      loading = false;
      if(data.candidates.some(c=>["queued","preparing","running","publishing"].includes(c.status)))
        setTimeout(() => {
          if (currentView === "iterations") renderIterations();
          // Legacy re-added body.iterating from the timer, pulling the user
          // back; the view store entry does the same and re-renders via the
          // subscription below.
          else view.set("iterations");
        }, 3000);
    } catch(e: any) {
      loadError = `迭代状态读取失败：${e.message}`;
      loading = false;
    }
  }

  function approveLabel(c: Candidate, a: { action: string; label: string }): string {
    if (working[c.id]) return "正在执行…";
    if (failed[c.id]) return failed[c.id];
    if (armed[c.id]) return a.action==="publish"?"再次点击确认：创建分支和 PR":"再次点击确认（不会覆盖当前 Fermi）";
    return a.label;
  }

  function onApprove(c: Candidate, a: { action: string; label: string }) {
    if(!armed[c.id]){ armed = { ...armed, [c.id]: true }; return; }
    working = { ...working, [c.id]: true };
    failed = { ...failed }; delete failed[c.id];
    candidateApproval(c.id, a.action).catch(e=>{
      working = { ...working, [c.id]: false };
      failed = { ...failed, [c.id]: `失败：${e.message}` };
    });
  }

  async function makeCode() {
    const goal = codeGoal.trim();
    if(!goal) return;
    await mediaJson("/mira/candidates/code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({goal})});
    renderIterations();
  }

  async function makeLora() {
    await mediaJson("/mira/candidates/lora",{method:"POST",headers:{"Content-Type":"application/json"},body:'{"iters":100}'});
    renderIterations();
  }

  onMount(() => {
    const unsubscribe = view.subscribe(v => {
      currentView = v;
      if (v === "iterations") renderIterations();
    });
    // Legacy boot entry: `if (location.hash === "#iterations") renderIterations();`
    // (ui.html:632). The subscription above replays the current value, so
    // setting the view is enough to trigger the first render.
    if (location.hash === "#iterations") view.set("iterations");
    return unsubscribe;
  });
</script>

{#if $view === "iterations"}
<div id="iterations"><div class="iteration-wrap">
  <h2>迭代中心</h2>
  <div class="iteration-sub">创作质检与记忆自动运行；源码 Candidate 和 LoRA Training Run 必须在这里批准。</div>
  <div id="iterationContent">
    {#if loading}
      正在读取…
    {:else if loadError}
      {loadError}
    {:else}
      <section class="iteration-section">
        <h3>长期记忆 · {prefCount} 条</h3>
        <div class="candidate-meta">{prefsText}</div>
      </section>
      <section class="iteration-section">
        <h3>反馈数据</h3>
        <div class="candidate-meta">{fbTotal} 条反馈 · {fbLearnable} 条可训练 · 平均 {fbAvg}/5</div>
      </section>
      <section class="iteration-section">
        <h3>源码自我迭代</h3>
        <div class="iteration-form">
          <input id="codeGoal" placeholder="希望 Fermi 自己改进什么？" bind:value={codeGoal}>
          <button class="media-download" id="makeCode" onclick={makeCode}>生成隔离 Candidate</button>
        </div>
      </section>
      <section class="iteration-section">
        <h3>模型权重迭代</h3>
        <div class="iteration-form">
          <button class="media-download" id="makeLora" onclick={makeLora}>从明确好评准备 LoRA Candidate</button>
        </div>
        <div class="candidate-meta">至少需要 20 条评分 4–5 且允许学习的样本。</div>
      </section>
      <section class="iteration-section">
        <h3>迭代候选</h3>
        {#each rows as r}
          <div class="candidate-row">
            {r.label}
            <div class="candidate-meta">{r.meta}</div>
            {#if r.c.pr_url}
              <a class="media-download" href={r.c.pr_url} target="_blank" rel="noopener noreferrer">打开 GitHub PR</a>
            {/if}
            {#if r.approve}
              <button class="media-download" disabled={working[r.c.id]} onclick={() => r.approve && onApprove(r.c, r.approve)}>{approveLabel(r.c, r.approve)}</button>
            {/if}
          </div>
        {/each}
      </section>
    {/if}
  </div>
</div></div>
{/if}

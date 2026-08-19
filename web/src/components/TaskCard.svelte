<script lang="ts">
  // Port of renderTaskCard (share/ui.html 779-832). Same DOM/classes so
  // app.css keeps hitting: .task-card > .task-head(+.task-state) /
  // .task-progress(progress+span) / .task-output / .task-actions.
  // Polls /mira/jobs/<id> every 3s while non-terminal (and in the
  // retry-offered terminal branch, exactly like the legacy code) and rebuilds
  // its content on every refresh — mirrored here with {#key task} so each
  // refresh remounts children like the legacy innerHTML wipe.
  import { onMount, onDestroy } from "svelte";
  import { mediaJson } from "../lib/api";
  import MediaCard from "./MediaCard.svelte";

  let { id }: { id: string } = $props();

  const TASK_LABELS: Record<string, string> = { image: "图片", music: "音乐", video: "视频" };
  const TASK_STATES: Record<string, string> = { queued: "排队中", running: "生成中", paused: "已暂停", complete: "已完成", failed: "失败", cancelled: "已取消" };

  let task = $state<any>(null);
  let readError = $state("");
  let retryText = $state("重试失败项");
  let retryDisabled = $state(false);
  let timer: ReturnType<typeof setTimeout> | null = null;
  let dead = false;

  async function taskAction(action: string) {
    await mediaJson(`/mira/jobs/${id}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  }

  async function refresh() {
    if (timer) { clearTimeout(timer); timer = null; } // deviation: dedupe pending poll so button-triggered refreshes can't stack poll chains
    try {
      const t = await mediaJson(`/mira/jobs/${id}`);
      if (dead) return;
      task = t;
      readError = "";
      retryText = "重试失败项"; retryDisabled = false; // legacy rebuilt the card, resetting the retry button
      if (!["complete", "failed", "cancelled"].includes(task.status)) {
        timer = setTimeout(refresh, 3000);
      } else if (task.failed > 0 || task.completed + task.failed < task.total) {
        timer = setTimeout(refresh, 3000);
      }
    } catch (e: any) {
      if (dead) return;
      readError = `任务状态读取失败：${e.message}`;
    }
  }

  async function togglePause() {
    await taskAction(task.status === "paused" ? "resume" : "pause");
    refresh();
  }

  async function cancelRemaining() {
    await taskAction("cancel");
    refresh();
  }

  async function retryFailed() {
    retryDisabled = true; retryText = "正在重新排队…";
    try { await taskAction("retry"); } catch (e: any) { retryDisabled = false; retryText = `重试失败：${e.message}`; return; }
    refresh();
  }

  // Elapsed readout reads task.updated (NOT created) — a recent fix; keep it.
  const stateText = $derived.by(() => {
    if (!task) return "";
    let s = TASK_STATES[task.status] || task.status;
    if (task.status === "running") {
      const elapsed = Math.max(0, Math.round(Date.now() / 1000 - task.updated));
      s += elapsed < 60 ? ` · ${elapsed} 秒` : ` · ${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`;
    }
    return s;
  });

  const completedItems = $derived(
    task ? (task.items || []).filter((item: any) => item.status === "complete" && item.output).slice(-3) : []);

  onMount(refresh);
  onDestroy(() => {
    dead = true;
    if (timer) clearTimeout(timer);
  });
</script>

<div class="task-card">
  {#if readError}
    {readError}
  {:else if task}
    {#key task}
      <div class="task-head">{TASK_LABELS[task.kind] || task.kind}批任务 · {task.total} 项<span class="task-state">{stateText}</span></div>
      <div class="task-progress">
        <progress max={task.total} value={task.completed + task.failed}></progress>
        <span>{task.completed}/{task.total}{task.failed ? ` · ${task.failed} 失败` : ""}</span>
      </div>
      {#each completedItems as item (item.position)}
        <div class="task-output">
          <MediaCard record={{ kind: task.kind, url: item.output, batchId: task.id, position: item.position, qualityScore: item.quality_score, status: "complete" }} />
        </div>
      {/each}
      {#if !["complete", "failed", "cancelled"].includes(task.status)}
        <div class="task-actions">
          <button class="media-download" onclick={togglePause}>{task.status === "paused" ? "继续" : "暂停"}</button>
          <button class="media-download" onclick={cancelRemaining}>取消剩余任务</button>
        </div>
      {:else if task.failed > 0 || task.completed + task.failed < task.total}
        <div class="task-actions">
          <button class="media-download" disabled={retryDisabled} onclick={retryFailed}>{retryText}</button>
        </div>
      {/if}
    {/key}
  {/if}
</div>

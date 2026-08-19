<script lang="ts">
  // Port of the legacy media card (share/ui.html runMedia card 649-660,
  // renderMedia 738-777, and the openChat branches at 510-515). The DOM
  // structure and class names are identical so app.css keeps hitting:
  // .media-card > .media-title / .media-progress(progress+span) /
  // media element / .media-actions > .media-download + .feedback-input.
  // `record` is the message object owned by the chats store — the runtime in
  // lib/media.ts mutates its fields (jobId/url/status/elapsed/…) and calls
  // saveChats(); this component only mirrors card UI state and re-renders.
  import { onMount, onDestroy, untrack } from "svelte";
  import { runMedia, resumeMedia, subscribeMedia, unsubscribeMedia, isMediaRunning, type MediaCallbacks } from "../lib/media";
  import { mediaJson } from "../lib/api";

  let { record }: { record: any } = $props();

  // --- card UI state (legacy: title div + progress bar + label span) ---
  let view = $state(untrack(() => record.status === "complete" ? "complete" : record.status === "running" ? "running" : "failed"));
  let title = $state("");
  let stageLabel = $state("正在启动本地模型…");
  let pCur = $state<number | null>(null); // null → indeterminate bar (legacy: no value attribute)
  let pMax = $state(1);
  let failMsg = $state<string | null>(null); // set on live failure → .notice class (legacy openChat replay has none)

  // --- completed-view state (renderMedia buttons) ---
  let dlText = $state("下载到“下载/Fermi”");
  let dlDisabled = $state(false);
  let dlTitle = $state("");
  let editingRating = $state<number | null>(null); // legacy feedback.dataset.editing + hidden
  let fbNote = $state("");
  let fbSaving = $state(false);
  let fbDone = $state<Record<number, boolean>>({});

  const RATINGS: [number, string][] = [[5, "👍 好评并学习"], [1, "👎 记住并改进"]];

  const callbacks: MediaCallbacks = {
    setTitle(text) { title = text; },
    setStage(label) { stageLabel = label; },
    setProgress(cur, total) { pCur = cur; pMax = total; },
    setDownload(percent) { stageLabel = `下载模型 ${percent}%`; pCur = percent; pMax = 100; },
    fail(msg) { failMsg = msg; view = "failed"; },
    complete() { view = "complete"; }, // record.url/elapsed/… already applied + saved by the runtime
  };

  const focusInput = (el: HTMLInputElement) => { el.focus(); };

  async function downloadMedia() {
    dlDisabled = true; dlText = "正在保存…";
    try {
      const result: any = await mediaJson("/mira/media/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: record.kind, url: record.url }) });
      dlText = "已保存并在 Finder 中显示"; dlTitle = result.path;
    } catch (e: any) {
      dlDisabled = false; dlText = `下载失败：${e.message}`;
    }
  }

  function startFeedback(rating: number) {
    if (editingRating !== null) return; // legacy: dataset.editing guard
    editingRating = rating; fbNote = "";
  }

  async function saveFeedback() {
    if (editingRating === null) return;
    fbSaving = true;
    await mediaJson("/mira/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ batch_id: record.batchId, position: record.position || 1, rating: editingRating, note: fbNote.trim(), learn: true }) });
    fbDone[editingRating] = true; editingRating = null; fbSaving = false;
  }

  function cancelFeedback() {
    editingRating = null;
  }

  // openChat behavior: a persisted "running" record restarts the loop on mount
  // (resumeMedia); a record that never started (fresh send) takes the fresh
  // runMedia path — same branches the legacy sendMedia/openChat used, decided
  // by whether a start was ever persisted.
  onMount(() => {
    if (record.status === "running") {
      subscribeMedia(record, callbacks);
      if (!isMediaRunning(record)) {
        if (record.startedAt || record.jobId) resumeMedia(record, callbacks);
        else runMedia(record.kind, record.prompt || "", record, callbacks, false);
      } else {
        title = "正在恢复生成任务"; // re-mounted while the background loop still owns this record
      }
    }
  });
  // Stop on destroy = stop receiving UI updates for this card. The background
  // loop itself keeps going (like the legacy detached card) so the record still
  // completes and persists; a remount re-subscribes.
  onDestroy(() => { unsubscribeMedia(record); });

  const completedTitle = $derived(
    ({ image: "生成的图片", music: "生成的音乐", video: "生成的视频" } as any)[record.kind]
    + (record.elapsed ? ` · ${record.elapsed} 秒` : "")
    + (record.qualityScore != null ? ` · 质量 ${record.qualityScore}/10` : ""));
</script>

<div class="media-card" class:notice={failMsg != null}>
  {#if view === "running"}
    <div class="media-title">{title}</div>
    <div class="media-progress">
      {#if pCur === null}
        <progress max={pMax}></progress>
      {:else}
        <progress max={pMax} value={pCur}></progress>
      {/if}
      <span>{stageLabel}</span>
    </div>
  {:else if view === "complete"}
    <div class="media-title">{completedTitle}</div>
    {#if record.kind === "image"}
      <img class="generated-image" src={record.url} alt="" />
    {:else if record.kind === "music"}
      <audio controls preload="metadata" src={record.url}></audio>
    {:else if record.kind === "video"}
      <!-- svelte-ignore a11y_media_has_caption -- generated media has no caption source -->
      <video controls preload="metadata" src={record.url}></video>
    {/if}
    <div class="media-actions">
      <button class="media-download" disabled={dlDisabled} title={dlTitle} onclick={downloadMedia}>{dlText}</button>
      {#if record.batchId}
        {#each RATINGS as [rating, label] (rating)}
          {#if fbDone[rating]}
            <button class="media-download" disabled>已记住</button>
          {:else if editingRating !== rating}
            <button class="media-download" onclick={() => startFeedback(rating)}>{label}</button>
          {/if}
        {/each}
        {#if editingRating !== null}
          <input class="feedback-input" placeholder={editingRating === 5 ? "哪里做得好？可留空" : "希望下次改进什么？"} bind:value={fbNote} use:focusInput />
          <button class="media-download" disabled={fbSaving} onclick={saveFeedback}>保存并学习</button>
          <button class="media-download" onclick={cancelFeedback}>取消</button>
        {/if}
      {/if}
    </div>
  {:else}
    生成失败：{failMsg || record.error || "任务未完成"}
  {/if}
</div>

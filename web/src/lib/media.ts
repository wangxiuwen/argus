// Media generation runtime, ported verbatim from share/ui.html runMedia /
// resumeMedia (lines 645-736). The legacy loop mutated a DOM card in place;
// here it mutates the record object (owned by the chats store) exactly as
// before and reports card UI through callbacks. The loop keeps running after
// the card unmounts — same as the legacy loop writing into a detached card —
// so record + saveChats() stay authoritative; a remounted MediaCard
// re-subscribes (subscribeMedia) and re-renders instead of holding dead DOM.
import { mediaJson, mediaPost, delay } from "./api";
import { saveChats } from "./stores/chats";

// UI callbacks for one media record. All UI state of the legacy card maps to
// these; the record mutations (jobId/url/status/elapsed/payload/error) stay
// inside runMedia and are persisted via saveChats() at the same points as the
// legacy code.
export interface MediaCallbacks {
  /** .media-title heading — set once at start: "正在生成图片"/… or "正在恢复生成任务". */
  setTitle(text: string): void;
  /** .media-progress status line — "正在启动本地模型…", "首次使用，正在下载模型…", "正在生成… N 秒", … */
  setStage(label: string): void;
  /** progress bar: value=cur, max=total; cur === null → indeterminate bar (legacy bar.removeAttribute("value")). */
  setProgress(cur: number | null, total: number): void;
  /** model download: label `下载模型 ${percent}%` and bar pinned to percent/100. */
  setDownload(percent: number): void;
  /** generation failed; the card shows `生成失败：${msg}` with the legacy .notice class. */
  fail(msg: string): void;
  /** record.status became "complete" (url/elapsed already applied + saved); render the completed view. */
  complete(record: any): void;
}

// Live subscriber per record — the mounted MediaCard. Emits become no-ops when
// no card is mounted (background run), which is what keeps unmounting safe.
const subscribers = new WeakMap<object, MediaCallbacks>();
// Records whose runMedia loop is currently in flight (guards double starts
// when the composer fires a fresh run and the card mounts in the same tick).
const runningRecords = new WeakSet<object>();

export function subscribeMedia(record: object, cb: MediaCallbacks): void {
  subscribers.set(record, cb);
}
export function unsubscribeMedia(record: object): void {
  subscribers.delete(record);
}
export function isMediaRunning(record: object): boolean {
  return runningRecords.has(record);
}

function emit(record: any, key: keyof MediaCallbacks, ...args: any[]): void {
  const cb = subscribers.get(record) as any;
  if (cb && typeof cb[key] === "function") cb[key](...args);
}

export async function runMedia(
  mode: string,
  prompt: string,
  record: any,
  callbacks?: MediaCallbacks,
  resume: boolean = false,
): Promise<void> {
  if (callbacks) subscribeMedia(record, callbacks);
  runningRecords.add(record);
  const fire = (key: keyof MediaCallbacks, ...args: any[]) => emit(record, key, ...args);
  const started = record.startedAt || Date.now();
  // Persist the start immediately: the chat window reloads on every reopen, and
  // during a long model download no later save used to land — a resume then
  // restarted the elapsed timer from zero.
  if (!record.startedAt) { record.startedAt = started; saveChats(); }
  fire("setTitle", resume ? "正在恢复生成任务" : ({ image: "正在生成图片", music: "正在生成音乐", video: "正在生成视频" } as any)[mode]);
  fire("setStage", "正在启动本地模型…");
  fire("setProgress", null, 1); // legacy: fresh <progress> with no value attribute
  try {
    // A resume can land before the first generate was accepted (model still
    // downloading, or the page reloaded mid-download), so both paths share the
    // ensure-generate block. Without it a resumed loop saw running=false the
    // moment the download process exited and adopted a stale output as its own.
    if (!resume || !record.jobId) {
      let payload = record.payload;
      if (!payload) {
        payload = { prompt };
        // The picker controls these read from were removed from the composer
        // long ago; fall back to the same defaults the job service uses.
        // (Legacy read $("imageSize")?.value etc. — those elements no longer
        // exist, so every read evaluated to these exact fallback values.)
        if (mode === "image") Object.assign(payload, { size: "1024x1024", steps: 4 });
        if (mode === "music") Object.assign(payload, { lyrics: "", duration_seconds: 60 });
        if (mode === "video") Object.assign(payload, { width: 960, height: 544, frames: 121, steps: 6, seed: 6 });
        record.payload = payload;
      }
      try {
        const accepted = await mediaPost(mode, "generate", payload);
        record.jobId = accepted.job_id;
      } catch (e: any) {
        // "busy" = a download or another generation already holds the worker;
        // either way we wait for model_ready && !running and post generate again
        if (!/not prepared|尚未准备|busy|忙/i.test(e.message)) throw e;
        fire("setStage", resume ? "后台模型尚未就绪，正在等待下载…" : "首次使用，正在下载模型…");
        if (!resume) {
          try { await mediaPost(mode, "prepare", { accept_license: true }); }
          catch (pe: any) { if (!/busy|忙/i.test(pe.message)) throw pe; }
        }
        for (;;) {
          const s = await mediaJson(`/mira/${mode}/status`);
          if (s.error) throw Error(s.error);
          if (s.model_ready && !s.running) break;
          if (s.download?.percent != null) fire("setDownload", s.download.percent);
          await delay(2000);
        }
        fire("setProgress", null, 1); // legacy: bar.removeAttribute("value")
        const accepted = await mediaPost(mode, "generate", payload);
        record.jobId = accepted.job_id;
      }
      saveChats();
    }
    for (;;) {
      const s = await mediaJson(`/mira/${mode}/status`);
      if (s.error) throw Error(s.error);
      if (record.jobId && s.job_id && record.jobId !== s.job_id) throw Error("该生成任务已被另一项任务替换");
      if (!record.jobId && s.job_id) { record.jobId = s.job_id; saveChats(); }
      if (s.progress?.total) fire("setProgress", s.progress.step, s.progress.total);
      else if (s.download?.percent != null && !s.model_ready) fire("setProgress", s.download.percent, 100);
      fire("setStage", (s.download?.percent != null && !s.model_ready)
        ? `下载模型 ${s.download.percent}%`
        : `${s.stage === "generating" ? "正在生成" : "正在处理"}… ${Math.round((Date.now() - started) / 1000)} 秒`);
      if (!s.running) {
        let output = s.output;
        if (!output || (mode === "video" && output.startsWith("/") && !output.startsWith("/outputs/"))) {
          const list = await mediaJson(`/mira/${mode}/${mode === "video" ? "outputs" : "list"}`);
          const item = list[0]; output = typeof item === "string" ? `/files/${encodeURIComponent(item)}` : item?.url;
        }
        if (!output) throw Error("任务结束，但没有找到输出文件");
        record.url = output.startsWith("http") ? output : `http://127.0.0.1:${mode === "video" ? 9877 : mode === "music" ? 9879 : 9880}${output}`;
        record.status = "complete"; record.elapsed = Math.round((Date.now() - started) / 1000);
        saveChats(); // legacy also re-rendered the chat list here — store reactivity covers it
        fire("complete", record);
        return;
      }
      await delay(2000);
    }
  } catch (e: any) {
    record.status = "failed"; record.error = e.message; saveChats();
    fire("fail", e.message); // legacy: card.textContent = `生成失败：${e.message}` + .notice
  } finally {
    runningRecords.delete(record);
  }
}

export function resumeMedia(record: any, callbacks?: MediaCallbacks): void {
  // Legacy replaced the whole card with this line before runMedia redrew it.
  callbacks?.setStage("正在重新连接后台生成任务…");
  runMedia(record.kind, record.prompt || "", record, callbacks, true);
}

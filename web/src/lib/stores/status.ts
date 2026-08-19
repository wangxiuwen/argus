// Server readiness / health polling. Ported verbatim from share/ui.html:966-1007.
// The old pollReady is a single-shot health check with a switching-aware
// ready gate and apiFoot URL update — not a retry loop. Keep that behavior.
//
// Legacy DOM side effects mapped to stores (rendered elsewhere):
//   $("dot").classList / $("statusText").textContent -> dotOk / statusText
//   (TopBar renders: <span id="status"><span id="statusText">{$statusText}</span>
//    <span id="dot" class:ok={$dotOk}></span></span> — initial "connecting…")
//   $("apiFoot").textContent -> apiFoot (sidebar renders <div class="foot" id="apiFoot">)
//   setPickerLabel() -> reactive pickerLabel in models.ts
import { writable, get } from "svelte/store";
import { busy } from "./ui";
import { model, switching, loadModels } from "./models";

export const ready = writable(false);
export const lastHealth = writable<any>({});
export const lastTps = writable(0); // tokens/s of the request in flight (set by agent/media streaming)
export const statusText = writable("connecting…");
export const dotOk = writable(false);
export const apiFoot = writable("");

export function startupText(h: any): string {
  if (h.stage === "failed") return h.error || "服务启动失败，请查看 Mira 日志";
  if (!h.alive) return "server stopped";
  if (h.stage === "downloading") {
    const pct = h.percent != null ? ` ${h.percent}%` : "";
    const of = h.total_gb ? ` · ${h.downloaded_gb}/${h.total_gb} GB` : "";
    return `downloading model${pct}${of}`;
  }
  if (h.stage === "loading") return "loading model into memory…";
  return get(switching) ? "restarting…" : "starting…";
}

export async function pollReady(): Promise<void> {
  let h: any;
  try {
    h = await fetch("/argus/health").then(r => r.json());
    lastHealth.set(h);
  } catch {
    dotOk.set(false);
    statusText.set("interface offline");
    return;
  }
  const cur = get(model), sw = get(switching);
  const targetReady = h.ready && (!sw || h.model === cur);
  if (targetReady) {
    if (!sw) model.set(h.model);
    else if (h.model === cur) switching.set(false);
    dotOk.set(true);
    const tps = get(lastTps);
    statusText.set(get(busy)
      ? (tps ? `generating… ${tps.toFixed(1)} tok/s` : "generating…")
      : "ready");
    // setPickerLabel() — reactive via pickerLabel derived
  } else {
    // process alive but not answering = downloading/loading weights, or busy for us
    dotOk.set(!!(get(busy) && h.alive));
    statusText.set(get(busy) ? "generating…" : startupText(h));
  }
  ready.set(!!targetReady);
  const apiHost = h.api_host === "0.0.0.0" ? location.hostname : h.api_host;
  if (h.api_port) apiFoot.set(`http://${apiHost}:${h.api_port}/v1`);
}

// ui.html:1006-1007 — loadModels().then(pollReady).catch(() => pollReady())
// plus a 5s interval. Idempotent: safe to call from both App onMount and
// Composer onMount (Composer calls it, so the app works unwired).
let started = false;
export function startPolling(): void {
  if (started) return;
  started = true;
  loadModels().then(pollReady).catch(() => pollReady());
  setInterval(() => { loadModels().catch(() => {}); pollReady(); }, 5000);
}

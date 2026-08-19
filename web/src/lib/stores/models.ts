// Model catalogue + switching. Ported verbatim from share/ui.html:924-963 —
// including the /argus/use POST body and the post-switch wait behavior
// (switching stays true until pollReady sees health.model === target).
//
// Legacy DOM side effects mapped to stores:
//   setPickerLabel()      -> pickerLabel derived (ModelPicker renders it)
//   dot/statusText writes -> status.ts stores (models.ts sets them here;
//                            models <-> status import cycle is function-scoped
//                            only, safe under ESM/Vite)
//   bubble("bot", …)      -> botNotices store (rendered by Composer, never
//                            persisted to argus.chats — legacy appended these
//                            straight to #log without touching `messages`)
import { writable, derived, get } from "svelte/store";
import { busy } from "./ui";
import { ready, dotOk, statusText } from "./status";

export interface Variant { id: string; label: string; downloaded?: boolean }
export const model = writable<string | null>(null);
export const variants = writable<Variant[]>([]);
export const switching = writable(false);

// Ephemeral bot bubbles — legacy bubble("bot", text) [+ .notice class].
// Wiped when curId changes (legacy: openChat re-rendered #log from scratch).
export interface BotNotice { id: number; text: string; notice?: boolean }
export const botNotices = writable<BotNotice[]>([]);
let noticeSeq = 0;
export function botBubble(text: string, isNotice = false): void {
  botNotices.update(list => [...list, { id: ++noticeSeq, text, notice: isNotice }]);
}

export function shortName(id: string): string { return id.split("/").pop() ?? id; }

// Legacy setPickerLabel (ui.html:906-912): name strips a trailing "(…)" group,
// title keeps the full label. Updates automatically wherever model/variants
// change — equivalent to every legacy setPickerLabel() call site.
export const pickerLabel = derived([model, variants], ([$model, $variants]) => {
  const v = $variants.find(v => v.id === $model);
  const fullLabel = v ? v.label : ($model ? shortName($model) : "Select a model");
  return { name: fullLabel.replace(/\s*\([^)]*\)\s*$/, ""), title: fullLabel };
});

export async function loadModels(): Promise<void> {
  const info = await fetch("/argus/models").then(r => r.json());
  variants.set(info.variants);
  if (!get(model)) model.set(info.current);
  // Legacy also compared catalogs to re-render an already-open menu with
  // scroll preserved (ui.html:930-933); here ModelPicker re-renders the list
  // reactively from $variants whenever the catalogue refreshes while open.
}

export async function switchModel(target: string): Promise<void> {
  if (target === get(model)) return;
  if (get(busy)) {
    botBubble("Stop the current reply before switching models.", true);
    return;
  }
  const previous = get(model);
  switching.set(true);
  ready.set(false);
  model.set(target);
  // setPickerLabel() — reactive via pickerLabel derived
  dotOk.set(false);
  statusText.set("restarting…");
  botBubble(`Switching to ${shortName(target)} — restarting the server` +
    (get(variants).find(v => v.id === target)?.downloaded ? "…" : " and downloading the model (this can take a while)…"));
  try {
    const resp = await fetch("/argus/use", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model: target})});
    if (!resp.ok) throw new Error((await resp.json()).error || `HTTP ${resp.status}`);
  } catch (err: any) {
    switching.set(false);
    model.set(previous);
    // setPickerLabel() — reactive via pickerLabel derived
    botBubble(`Could not switch models: ${err.message}`, true);
  }
}

<script lang="ts">
  // Model picker — markup from share/ui.html:331-337, behavior from 834-963.
  // optionRow/sectionLabel became markup below; renderList became reactive
  // state (local list derives from $variants + filter; hub search keeps the
  // legacy 350ms debounce, generation counter and result cache from 886-904).
  import { onMount, onDestroy, tick } from "svelte";
  import { model, variants, pickerLabel, switchModel } from "../lib/stores/models";

  let open = false;
  let filter = "";
  let findEl: HTMLInputElement;
  let listEl: HTMLDivElement;

  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let searchGeneration = 0;
  const hubSearchCache = new Map<string, any[]>();
  let hubResults: any[] = [];
  let searching = false;

  // renderList(filter): local matches (ui.html:874-877)
  $: f = filter.trim().toLowerCase();
  $: local = $variants.filter(v => v.label.toLowerCase().includes(f) || v.id.toLowerCase().includes(f));
  $: showNothingLocal = !local.length && !!f;
  // appendHubResults (ui.html:863-868): only when results exist, deduped
  // against known local ids, capped at 12.
  $: knownIds = new Set($variants.map(v => v.id));
  $: hubFiltered = hubResults.filter(r => !knownIds.has(r.id)).slice(0, 12);
  $: showHub = hubResults.length > 0;

  // Hub search (ui.html:879-902): debounce 350ms + generation guard + cache.
  // Event-driven like the legacy input handler — a reactive block here would
  // re-trigger itself through ++searchGeneration / searchTimer writes.
  function scheduleSearch(raw: string) {
    const q = raw.trim().toLowerCase();
    if (searchTimer) clearTimeout(searchTimer);
    const generation = ++searchGeneration;
    if (q.length >= 2) {
      const cached = hubSearchCache.get(q);
      if (cached) {
        hubResults = cached;
        searching = false;
        return;
      }
      searching = true;
      hubResults = [];
      searchTimer = setTimeout(async () => {
        let results: any[] = [];
        try {
          // any model mlx-vlm can load works — search Hugging Face for MLX conversions
          results = (await fetch(`/argus/search?q=${encodeURIComponent(q)}`).then(r => r.json())).results;
        } catch { /* offline: local list still works */ }
        if (generation !== searchGeneration || filter.trim().toLowerCase() !== q) return;
        hubSearchCache.set(q, results);
        hubResults = results;
        searching = false;
      }, 350);
    } else {
      hubResults = [];
      searching = false;
    }
  }

  // loadModels keeps an already-open menu in sync with preserved scroll
  // (ui.html:930-933, renderList(find.value, /* preserveScroll */ true)).
  // Keep an open menu in sync when the catalogue changes (loadModels),
  // preserving scroll (ui.html:930-933). Subscribed outside the reactive
  // system: a reactive block + tick() here re-invalidated itself in an
  // endless microtask chain that starved the main thread on open.
  const stopVariants = variants.subscribe(() => {
    if (!open) return;
    const top = listEl ? listEl.scrollTop : 0;
    tick().then(() => { if (listEl) listEl.scrollTop = top; });
  });

  // pickerBtn onclick (ui.html:914-919) — reopening always starts from a
  // fresh list (legacy renderList() with filter ""), even if filter is
  // already "" and the reactive block would not re-run.
  function toggleMenu(e: MouseEvent) {
    e.stopPropagation();
    open = !open;
    if (open) {
      filter = "";
      scheduleSearch("");
      tick().then(() => findEl?.focus());
    }
  }

  // legacy optionRow onclick (ui.html:848)
  function pick(id: string) {
    open = false;
    switchModel(id);
  }

  const onDocClick = () => { open = false; };
  onMount(() => document.addEventListener("click", onDocClick));
  onDestroy(() => {
    document.removeEventListener("click", onDocClick);
    if (searchTimer) clearTimeout(searchTimer);
    stopVariants();
  });
</script>

<div id="picker">
  <button id="pickerBtn" title={$pickerLabel.title} onclick={toggleMenu}><span class="name">{$pickerLabel.name}</span><span class="caret">▾</span></button>
  <div id="menu" class:open onclick={(e: MouseEvent) => e.stopPropagation()}>
    <input id="find" placeholder="Find model…" bind:this={findEl} bind:value={filter} oninput={() => scheduleSearch(filter)} />
    <div id="list" bind:this={listEl}>
      {#each local as v (v.id)}
        <div class="opt" class:cur={v.id === $model}
             title={v.downloaded ? v.id : v.id + " (not on disk yet — switching downloads it)"}
             onclick={() => pick(v.id)}>
          <span class="lbl">{v.label}</span>
          <span class="mark">{v.id === $model ? "✓" : (v.downloaded ? "本地" : "↓")}</span>
        </div>
      {/each}
      {#if showNothingLocal}<div class="opt-label">nothing local matches</div>{/if}
      {#if searching}<div class="opt-label">searching Hugging Face…</div>{/if}
      {#if showHub}
        <div class="opt-label">on Hugging Face</div>
        {#each hubFiltered as v (v.id)}
          <div class="opt" class:cur={v.id === $model}
               title={v.downloaded ? v.id : v.id + " (not on disk yet — switching downloads it)"}
               onclick={() => pick(v.id)}>
            <span class="lbl">{v.label}</span>
            <span class="mark">{v.id === $model ? "✓" : (v.downloaded ? "本地" : "↓")}</span>
          </div>
        {/each}
      {/if}
    </div>
  </div>
</div>

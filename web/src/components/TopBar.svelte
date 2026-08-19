<script lang="ts">
  // #bar, ported from ui.html:293-302. statusText / dot replicate what
  // pollReady() wrote into #statusText/#dot (ui.html:972-992), derived here
  // from the status stores; moduleTitle derives from the view (legacy set
  // "本地助手" / "连接工具" / "迭代中心" alongside every body-class switch).
  import { view, busy, sidebarHidden } from "../lib/stores/ui";
  import { ready, lastHealth, lastTps, startupText } from "../lib/stores/status";

  function setSidebar(hidden: boolean) {
    sidebarHidden.set(hidden);
    localStorage.setItem("argus.side", hidden ? "hidden" : "shown");
  }

  $: moduleTitle = $view === "launch" ? "连接工具" : $view === "iterations" ? "迭代中心" : "本地助手";

  // pollReady text logic: offline catch → "interface offline" (contract: the
  // status store sets lastHealth to null when /argus/health throws); ready →
  // ready/generating + tok/s; not ready → generating… / startupText(h).
  $: statusText = (() => {
    if ($ready) {
      return $busy
        ? ($lastTps ? `generating… ${$lastTps.toFixed(1)} tok/s` : "generating…")
        : "ready";
    }
    if ($lastHealth === null) return "interface offline";
    if ($busy) return "generating…";
    if (!$lastHealth || Object.keys($lastHealth).length === 0) return "connecting…";
    return startupText($lastHealth);
  })();

  // dot: ok on ready; otherwise ok only while busy with the process alive;
  // never ok once the bridge is unreachable.
  $: dotOk = $ready || ($busy && !!$lastHealth?.alive);
</script>

<div id="bar">
  <button class="icon-btn" id="toggleOut" title="Show sidebar" on:click={() => setSidebar(false)}>
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round">
      <rect x="2.2" y="3.4" width="15.6" height="13.2" rx="2.6"/>
      <line x1="7.9" y1="3.4" x2="7.9" y2="16.6"/>
    </svg>
  </button>
  <span id="moduleTitle">{moduleTitle}</span>
  <span id="status"><span id="statusText">{statusText}</span><span id="dot" class:ok={dotOk}></span></span>
</div>

<script lang="ts">
  // The waiting indicator, ported from showAgentWaiting (ui.html:1056-1070)
  // plus openChat's planning trigger (ui.html:517). Rendered by MessageList
  // whenever a message has status === "planning" and a requestId; runAgentRequest
  // flips that field when the reply lands, which unmounts this component —
  // that is the old stopWaiting() teardown.
  import { onMount, onDestroy } from "svelte";
  import { runAgentRequest, waitingAnchor } from "../lib/agent";

  export let record: any;
  export let chatId: string | null = null;

  let secs = 0;
  const started = waitingAnchor(record); // startedAt-anchored, not mount-anchored
  const tick = setInterval(() => {
    secs = Math.round((Date.now() - started) / 1000);
  }, 500);
  onDestroy(() => clearInterval(tick));

  // openChat: if (m.status === "planning" && m.requestId) runAgentRequest(m, b, id)
  // — firing on mount makes every (re)appearance of the message run the same
  // call; runAgentRequest's activeAgentRequests guard blocks duplicate fetches.
  onMount(() => { runAgentRequest(record, chatId); });
</script>

<span class="waiting"><span class="dots"><i></i><i></i><i></i></span><span class="stage">Agent 正在理解请求并选择能力</span><span class="secs">{secs} 秒</span></span>

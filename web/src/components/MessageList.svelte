<script lang="ts">
  // The #log contents, ported from bubble() (ui.html:1041-1054) and openChat's
  // render loop (ui.html:495-519). #log itself lives in App.svelte; this
  // component renders its children and keeps the legacy .empty class in sync.
  import { afterUpdate } from "svelte";
  import { messages, curId } from "../lib/stores/chats";
  import Bubble from "./Bubble.svelte";
  import Hero from "./Hero.svelte";
  import MediaCard from "./MediaCard.svelte";
  import TaskCard from "./TaskCard.svelte";
  import Waiting from "./Waiting.svelte";
  import { isPlanningAgentMessage, messageImages, messageText } from "../lib/domain";

  // bubble() cleared #log's .empty class (restoring it is newChat()'s job via
  // the empty messages store) and scrolled to the bottom on every append.
  afterUpdate(() => {
    const log = document.getElementById("log");
    if (!log) return;
    const empty = $messages.length === 0;
    log.classList.toggle("empty", empty);
    if (!empty) log.scrollTop = log.scrollHeight;
  });
</script>

{#if $messages.length === 0}
  <Hero />
{:else}
  {#each $messages as m (m)}<Bubble role={m.role === "user" ? "user" : "bot"} text={messageText(m.content)} imgs={messageImages(m.content)}>{#if m.kind}<MediaCard record={m} />{/if}{#each m.tasks || [] as taskId}<TaskCard id={taskId} />{/each}{#if isPlanningAgentMessage(m)}<Waiting record={m} chatId={$curId} />{/if}</Bubble>{/each}
{/if}

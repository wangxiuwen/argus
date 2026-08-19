<script lang="ts">
  // bubble(), ported from ui.html:1041-1054. Same DOM: .msg.{role} > .bubble
  // with the images first, then the text node, then whatever the caller
  // appended to the returned bubble element (media cards, task cards, the
  // waiting indicator) — that tail is the default <slot />.
  export let role: string;
  export let text: string;
  export let imgs: string[] = [];
</script>

<div class="msg {role}"><div class="bubble">{#each imgs as src}<img src={src} alt="">{/each}{text}<slot /></div></div>

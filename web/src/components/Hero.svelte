<script lang="ts">
  // #hero empty state, ported from ui.html:303-305/350-354.
  // useSuggestion() prefilled #input and focused it; the composer is owned by
  // Composer.svelte, so the prefill travels as a window event ('mira:prefill',
  // detail = the button text) which the Composer listens for.
  function useSuggestion(text: string) {
    window.dispatchEvent(new CustomEvent("mira:prefill", {detail: text}));
    document.getElementById("input")?.focus();
  }
</script>

<div id="hero"><span class="hero-mark" aria-hidden="true"><img src="/mira/icon.png" alt=""></span><h1>今天想创作什么？</h1><span class="t">聊天、图片、音乐和视频，都在同一个对话里完成</span><div class="suggestions"><button class="suggestion" on:click={() => useSuggestion("帮我把想法整理成一段提示词")}>帮我把想法整理成一段提示词</button><button class="suggestion" on:click={() => useSuggestion("生成一张雨夜上海街头的电影感画面")}>生成一张雨夜上海街头的电影感画面</button><button class="suggestion" on:click={() => useSuggestion("创作一首温暖的木吉他流行歌")}>创作一首温暖的木吉他流行歌</button><button class="suggestion" on:click={() => useSuggestion("生成晨雾森林里小鹿穿过阳光的视频")}>生成晨雾森林里小鹿穿过阳光的视频</button></div></div>

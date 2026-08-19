<script lang="ts">
  // Mirrors the legacy body layout exactly so app.css selectors keep working:
  // nav#side + div#main(#bar, #log/#launch/#iterations, #composer).
  // View switching stays body-class driven (launching / iterating / side-hidden).
  import { onMount } from "svelte";
  import { view, sidebarHidden } from "./lib/stores/ui";
  import Sidebar from "./components/Sidebar.svelte";
  import TopBar from "./components/TopBar.svelte";
  import MessageList from "./components/MessageList.svelte";
  import LaunchView from "./components/LaunchView.svelte";
  import IterationsView from "./components/IterationsView.svelte";
  import Composer from "./components/Composer.svelte";
  import { loadChats, recoverUnlinkedTasks } from "./lib/stores/chats";
  import { startPolling } from "./lib/stores/status";

  $: if (typeof document !== "undefined") {
    document.body.classList.toggle("launching", $view === "launch");
    document.body.classList.toggle("iterating", $view === "iterations");
    document.body.classList.toggle("side-hidden", $sidebarHidden);
  }

  onMount(() => {
    loadChats().then(recoverUnlinkedTasks);
    startPolling();
  });
</script>

<Sidebar />
<div id="main">
  <TopBar />
  <div id="log" class="empty">
    <MessageList />
  </div>
  <LaunchView />
  <IterationsView />
  <Composer />
</div>

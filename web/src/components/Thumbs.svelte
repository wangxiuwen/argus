<script module lang="ts">
  // Attachments — ported verbatim from share/ui.html:1009-1038.
  // `images` is the legacy module-level array; Composer reads/clears it for
  // send(). addImage/removeImage keep the legacy indexOf/splice semantics.
  import { writable } from "svelte/store";
  export const images = writable<string[]>([]);

  export function addImage(dataURL: string): void {
    images.update(list => { list.push(dataURL); return list; });
  }

  export function fileToImage(f: File | null): void {
    if (!f || !f.type.startsWith("image/")) return;
    const r = new FileReader();
    r.onload = () => addImage(r.result as string);
    r.readAsDataURL(f);
  }

  // legacy: images.splice(images.indexOf(dataURL), 1) + thumb remove
  export function removeImage(dataURL: string): void {
    images.update(list => {
      const i = list.indexOf(dataURL);
      if (i >= 0) list.splice(i, 1);
      return list;
    });
  }
</script>

<script lang="ts">
  // Global paste/drag listeners (legacy document.addEventListener, never
  // removed in the single-page original; removed here on destroy).
  import { onMount, onDestroy } from "svelte";

  const onPaste = (e: ClipboardEvent) => [...(e.clipboardData?.items || [])].forEach(it => {
    if (it.type.startsWith("image/")) fileToImage(it.getAsFile());
  });
  const onDragover = (e: DragEvent) => { e.preventDefault(); document.body.classList.add("drag"); };
  const onDragleave = (e: DragEvent) => { if (!e.relatedTarget) document.body.classList.remove("drag"); };
  const onDrop = (e: DragEvent) => {
    e.preventDefault(); document.body.classList.remove("drag");
    [...(e.dataTransfer?.files || [])].forEach(fileToImage);
  };
  onMount(() => {
    document.addEventListener("paste", onPaste);
    document.addEventListener("dragover", onDragover);
    document.addEventListener("dragleave", onDragleave);
    document.addEventListener("drop", onDrop);
  });
  onDestroy(() => {
    document.removeEventListener("paste", onPaste);
    document.removeEventListener("dragover", onDragover);
    document.removeEventListener("dragleave", onDragleave);
    document.removeEventListener("drop", onDrop);
  });
</script>

<!-- #thumbs:empty {display:none} relies on no child nodes — keep this line
     free of whitespace text nodes (comments are ignored by :empty). -->
<div id="thumbs">{#each $images as dataURL, i}<div class="thumb"><img src={dataURL} alt=""><button onclick={() => removeImage(dataURL)}>✕</button></div>{/each}</div>

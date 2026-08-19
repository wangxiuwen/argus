// View-level UI state. The legacy CSS switches views via body classes
// (launching / iterating / side-hidden); App.svelte mirrors the store into
// document.body so those selectors keep working untouched.
import { writable } from "svelte/store";

export type View = "chat" | "launch" | "iterations";
export const view = writable<View>("chat");
export const busy = writable(false);
export const sidebarHidden = writable(false);

export function showView(v: View) {
  view.set(v);
}

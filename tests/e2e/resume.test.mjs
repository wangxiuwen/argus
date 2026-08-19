import { chromium } from 'playwright-core';
const seed = [{id:"t1", title:"video test", messages:[
  {role:"user", content:"make a video"},
  {role:"assistant", content:"", kind:"video", status:"running", prompt:"make a video"},
]}];
const b = await chromium.launch({ channel: 'chrome' });
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
const p = await ctx.newPage();
p.on('pageerror', e => console.log('PAGEERROR:', e.message));
p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE:', m.text().slice(0,200)); });
await p.addInitScript(seed => {
  if (!localStorage.getItem("argus.chats")) localStorage.setItem("argus.chats", JSON.stringify(seed));
}, seed);
await p.goto('http://127.0.0.1:8091/', { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(1000);
const opened = await p.evaluate(() => { openChat("t1"); return document.querySelectorAll("#log .media-card").length; });
await p.waitForTimeout(4000);
const snap = () => ({
  cardText: document.querySelector("#log .media-card")?.textContent || "",
  cards: document.querySelectorAll("#log .media-card").length,
  labels: [...document.querySelectorAll("#log .media-card .media-progress span")].map(e => e.textContent),
  rec: (() => { const r = JSON.parse(localStorage.getItem("argus.chats"))[0]?.messages?.find(m => m.kind === "video"); return { startedAt: r?.startedAt ?? null, payload: !!r?.payload, jobId: r?.jobId ?? null, status: r?.status }; })(),
});
console.log("after resume:", JSON.stringify(await p.evaluate(snap)));
await p.screenshot({ path: 'tests/e2e/out_resume1.png' });
const before = await p.evaluate(() => JSON.parse(localStorage.getItem("argus.chats"))[0].messages.find(m => m.kind === "video").startedAt);
await p.reload({ waitUntil: 'domcontentloaded' });
await p.waitForTimeout(1000);
await p.evaluate(() => openChat("t1"));
await p.waitForTimeout(4000);
console.log("after reload+resume:", JSON.stringify(await p.evaluate(snap)));
const after = await p.evaluate(() => JSON.parse(localStorage.getItem("argus.chats"))[0].messages.find(m => m.kind === "video").startedAt);
console.log("startedAt preserved across reload:", before === after && !!before);
await p.screenshot({ path: 'tests/e2e/out_resume2.png' });
await b.close();

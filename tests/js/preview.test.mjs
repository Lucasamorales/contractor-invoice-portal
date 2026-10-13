// preview.js: the invoice preview frame grows to the height of the invoice
// inside it, so nobody scrolls inside a picture of a piece of paper.
import assert from "node:assert/strict";
import { test } from "node:test";

import { fire, page, run } from "./harness.mjs";

const FRAME = `<div class="sheet-frame"><iframe class="sheet-scale" srcdoc="<div class='sheet'>invoice</div>"></iframe></div>`;

// jsdom does no layout, so the measurements the script reads are supplied the
// way a browser would report them: the sheet's height, and a frame drawn at
// `scale` of its natural width.
function measure(dom, frame, { sheetHeight, docHeight = 0, scale = 0.5 }) {
  const inner = frame.contentDocument;
  const sheet = inner.querySelector(".sheet") || inner.body;
  Object.defineProperty(sheet, "scrollHeight", { value: sheetHeight, configurable: true });
  Object.defineProperty(inner.documentElement, "scrollHeight", { value: docHeight, configurable: true });
  Object.defineProperty(frame, "offsetWidth", { value: 794, configurable: true });
  frame.getBoundingClientRect = () => ({ width: 794 * scale });
}

async function framed(body = FRAME) {
  const dom = await page(body);
  const frame = dom.window.document.querySelector("iframe");
  return { dom, frame, outer: dom.window.document.querySelector(".sheet-frame") };
}

test("the frame takes the sheet's height and the wrapper the scaled height", async () => {
  const { dom, frame, outer } = await framed();
  measure(dom, frame, { sheetHeight: 1500.2, scale: 0.5 });
  run(dom, "preview.js");
  assert.equal(frame.style.height, "1501px");
  assert.equal(outer.style.height, "751px");
});

test("the taller of the sheet and the document wins", async () => {
  const { dom, frame } = await framed();
  measure(dom, frame, { sheetHeight: 900, docHeight: 1123 });
  run(dom, "preview.js");
  assert.equal(frame.style.height, "1123px");
});

test("a frame with nothing to measure yet keeps its CSS height", async () => {
  const { dom, frame } = await framed();
  measure(dom, frame, { sheetHeight: 0 });
  run(dom, "preview.js");
  assert.equal(frame.style.height, "");
});

test("it is measured again when the frame finishes loading", async () => {
  const { dom, frame } = await framed();
  measure(dom, frame, { sheetHeight: 1123 });
  run(dom, "preview.js");
  measure(dom, frame, { sheetHeight: 2246 });
  fire(dom, frame, "load");
  assert.equal(frame.style.height, "2246px");
});

test("it is measured again when the window is resized", async () => {
  const { dom, frame, outer } = await framed();
  measure(dom, frame, { sheetHeight: 1123, scale: 0.5 });
  run(dom, "preview.js");
  measure(dom, frame, { sheetHeight: 1123, scale: 1 });
  fire(dom, dom.window, "resize");
  assert.equal(outer.style.height, "1123px");
});

test("a frame outside a wrapper still gets its own height", async () => {
  const { dom, frame } = await framed(`<iframe class="sheet-scale" srcdoc="<p>x</p>"></iframe>`);
  measure(dom, frame, { sheetHeight: 400 });
  run(dom, "preview.js");
  assert.equal(frame.style.height, "400px");
});

test("fonts settling inside the frame trigger another measurement", async () => {
  const { dom, frame } = await framed();
  let settle;
  frame.contentDocument.fonts = { ready: new Promise((resolve) => (settle = resolve)) };
  measure(dom, frame, { sheetHeight: 1000 });
  run(dom, "preview.js");
  measure(dom, frame, { sheetHeight: 1040 });
  settle();
  await frame.contentDocument.fonts.ready;
  await Promise.resolve();
  assert.equal(frame.style.height, "1040px");
});

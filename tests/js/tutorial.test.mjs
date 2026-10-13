// tutorial.js: the "?" button turns the page into a guided tour of the
// elements marked data-tut, in the order they appear on the page.
import assert from "node:assert/strict";
import { test } from "node:test";

import { click, fire, page, run } from "./harness.mjs";

// Declared out of page order on purpose: the tour follows the page.
const STEPS = [
  { key: "total", title: "Your total", body: "What you will be paid." },
  { key: "fee", title: "Your fee", body: "Agreed monthly." },
  { key: "absent", title: "Not on this page", body: "Skipped." },
];

function markup(steps = STEPS) {
  return `
    <div class="bar"><button id="help-toggle" aria-expanded="false">?</button></div>
    <main>
      <p data-tut="fee">Fee</p>
      <p data-tut="total">Total</p>
    </main>
    <script type="application/json" id="tutorial-steps">${typeof steps === "string" ? steps : JSON.stringify(steps)}</script>`;
}

async function open(body = markup()) {
  const dom = await page(body);
  dom.window.scrollTo = () => {};
  run(dom, "tutorial.js");
  const doc = dom.window.document;
  return {
    dom,
    doc,
    toggle: doc.getElementById("help-toggle"),
    card: doc.querySelector(".tut-card"),
    banner: doc.querySelector(".tut-banner"),
    backdrop: doc.querySelector(".tut-backdrop"),
    title: () => doc.querySelector(".tut-title").textContent,
    lit: () => doc.querySelector("[data-spot]"),
    button: (label) => [...doc.querySelectorAll(".tut-card button")].find((b) => b.textContent === label),
  };
}

function key(dom, name) {
  fire(dom, dom.window.document, "keydown", { key: name });
}

test("nothing is shown until the tour is started", async () => {
  const { card, banner, backdrop, doc } = await open();
  assert.equal(card.hidden, true);
  assert.equal(banner.hidden, true);
  assert.equal(backdrop.hidden, true);
  assert.equal(doc.querySelectorAll(".tut-pin").length, 0);
});

test("starting the tour pins each step present and opens the first in page order", async () => {
  const { dom, doc, toggle, card, banner, title, lit } = await open();
  click(dom, toggle);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(doc.body.classList.contains("tut-on"), true);
  assert.deepEqual(
    [...doc.querySelectorAll(".tut-pin")].map((pin) => pin.textContent),
    ["1", "2"],
  );
  assert.equal(banner.hidden, false);
  assert.equal(doc.querySelector(".tut-banner-count").textContent, "2 on this page.");
  assert.equal(card.hidden, false);
  assert.equal(title(), "Your fee", "the fee comes first on the page");
  assert.equal(lit().getAttribute("data-tut"), "fee");
  assert.equal(doc.querySelector(".tut-count").textContent, "1 of 2");
});

test("Back is disabled on the first step and Next moves on", async () => {
  const { dom, toggle, button, title, lit } = await open();
  click(dom, toggle);
  assert.equal(button("Back").disabled, true);
  click(dom, button("Next"));
  assert.equal(title(), "Your total");
  assert.equal(lit().getAttribute("data-tut"), "total");
  assert.equal(button("Back").disabled, false);
});

test("the last step offers Done, which ends the tour", async () => {
  const { dom, doc, toggle, button, card, banner } = await open();
  click(dom, toggle);
  click(dom, button("Next"));
  assert.equal(button("Next"), undefined);
  click(dom, button("Done"));
  assert.equal(card.hidden, true);
  assert.equal(banner.hidden, true);
  assert.equal(doc.querySelectorAll(".tut-pin").length, 0);
  assert.equal(doc.body.classList.contains("tut-on"), false);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
});

test("arrow keys step through and stop at either end", async () => {
  const { dom, toggle, title } = await open();
  click(dom, toggle);
  key(dom, "ArrowLeft");
  assert.equal(title(), "Your fee");
  key(dom, "ArrowRight");
  key(dom, "ArrowRight");
  assert.equal(title(), "Your total");
});

test("Escape ends the tour and returns focus to where it was", async () => {
  const { dom, doc, toggle, card } = await open();
  toggle.focus();
  click(dom, toggle);
  key(dom, "Escape");
  assert.equal(card.hidden, true);
  assert.equal(doc.activeElement, toggle);
});

test("keys do nothing while the tour is off", async () => {
  const { dom, card } = await open();
  key(dom, "ArrowRight");
  assert.equal(card.hidden, true);
});

test("closing the card keeps the pins, and a pin reopens its step", async () => {
  const { dom, doc, toggle, card, title } = await open();
  click(dom, toggle);
  click(dom, doc.querySelector(".tut-close"));
  assert.equal(card.hidden, true);
  assert.equal(doc.querySelector("[data-spot]"), null);
  assert.equal(doc.querySelectorAll(".tut-pin").length, 2);

  key(dom, "ArrowRight");
  assert.equal(card.hidden, true, "arrows are ignored while no card is open");

  click(dom, doc.querySelectorAll(".tut-pin")[1]);
  assert.equal(card.hidden, false);
  assert.equal(title(), "Your total");
});

test("clicking the backdrop closes the card", async () => {
  const { dom, toggle, card, backdrop } = await open();
  click(dom, toggle);
  click(dom, backdrop);
  assert.equal(card.hidden, true);
});

test("pressing the toggle again, or Done on the banner, ends the tour", async () => {
  const { dom, doc, toggle, banner } = await open();
  click(dom, toggle);
  click(dom, toggle);
  assert.equal(banner.hidden, true);
  click(dom, toggle);
  click(dom, banner.querySelector("button"));
  assert.equal(doc.querySelectorAll(".tut-pin").length, 0);
});

test("content swapped in while the tour is on gets its pin", async () => {
  const { dom, doc, toggle } = await open();
  click(dom, toggle);
  doc.querySelector('[data-tut="total"] .tut-pin').remove();
  fire(dom, doc.body, "htmx:afterSwap");
  assert.equal(doc.querySelectorAll(".tut-pin").length, 2);
});

test("a swap while the tour is off adds no pins", async () => {
  const { dom, doc } = await open();
  fire(dom, doc.body, "htmx:afterSwap");
  assert.equal(doc.querySelectorAll(".tut-pin").length, 0);
});

test("a screen with no steps for its elements disables the button and says why", async () => {
  const { toggle } = await open(markup([{ key: "elsewhere", title: "x", body: "y" }]));
  assert.equal(toggle.disabled, true);
  assert.equal(toggle.title, "No tutorial on this screen yet");
});

test("unreadable step data is treated as no tutorial", async () => {
  const { toggle } = await open(markup("{oops"));
  assert.equal(toggle.disabled, true);
});

test("a page without the help button is left alone", async () => {
  const dom = await page(`<p data-tut="fee">Fee</p>`);
  run(dom, "tutorial.js");
  assert.equal(dom.window.document.querySelector(".tut-card"), null);
});

// jsdom does no layout, so the geometry the placement reads is supplied.
function geometry(dom, card, lit, { rect, cardHeight, cardWidth = 320, viewport = [1000, 800] }) {
  Object.defineProperty(dom.window, "innerWidth", { value: viewport[0], configurable: true });
  Object.defineProperty(dom.window, "innerHeight", { value: viewport[1], configurable: true });
  Object.defineProperty(card, "offsetHeight", { value: cardHeight, configurable: true });
  Object.defineProperty(card, "offsetWidth", { value: cardWidth, configurable: true });
  lit.getBoundingClientRect = () => rect;
}

async function placed(options) {
  const view = await open();
  click(view.dom, view.toggle);
  geometry(view.dom, view.card, view.lit(), options);
  fire(view.dom, view.dom.window, "resize");
  return view.card.style;
}

test("the card sits below the lit element when there is room", async () => {
  const style = await placed({ rect: { top: 100, bottom: 140, left: 60 }, cardHeight: 200 });
  assert.equal(style.top, "162px");
  assert.equal(style.left, "60px");
});

test("it goes above when there is no room below", async () => {
  const style = await placed({ rect: { top: 600, bottom: 760, left: 60 }, cardHeight: 200 });
  assert.equal(style.top, "378px");
});

test("it is centred when it fits neither above nor below", async () => {
  const style = await placed({ rect: { top: 100, bottom: 700, left: 60 }, cardHeight: 400 });
  assert.equal(style.top, "200px");
});

test("it is pulled back inside the right-hand edge", async () => {
  const style = await placed({ rect: { top: 100, bottom: 140, left: 900 }, cardHeight: 200 });
  assert.equal(style.left, "656px");
});

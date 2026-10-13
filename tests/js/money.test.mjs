// money.js: every amount on the Finance screens opens hidden and is shown on
// a press, without fetching anything.
import assert from "node:assert/strict";
import { test } from "node:test";

import { click, load, page, run } from "./harness.mjs";

const AMOUNT = `
  <span data-money>
    <button data-money-show>£•••</button>
    <button data-money-hide hidden>£1,200.00</button>
  </span>`;

const PAGE = `<button data-money-all aria-pressed="false">Show all</button>${AMOUNT}${AMOUNT}`;

function parts(dom) {
  const doc = dom.window.document;
  return {
    all: doc.querySelector("[data-money-all]"),
    shows: [...doc.querySelectorAll("[data-money-show]")],
    hides: [...doc.querySelectorAll("[data-money-hide]")],
  };
}

test("pressing the dots shows that figure and only that one", async () => {
  const dom = await load("money.js", PAGE);
  const { shows, hides } = parts(dom);
  click(dom, shows[0]);
  assert.deepEqual(
    hides.map((b) => b.hidden),
    [false, true],
  );
  assert.equal(shows[0].hidden, true);
});

test("pressing a shown figure hides it again", async () => {
  const dom = await load("money.js", PAGE);
  const { shows, hides } = parts(dom);
  click(dom, shows[0]);
  click(dom, hides[0]);
  assert.equal(hides[0].hidden, true);
  assert.equal(shows[0].hidden, false);
});

test("focus moves to the button that replaced the one pressed", async () => {
  const dom = await load("money.js", PAGE);
  const { shows, hides } = parts(dom);
  click(dom, shows[1]);
  assert.equal(dom.window.document.activeElement, hides[1]);
});

test("Show all reveals every figure and becomes Hide all", async () => {
  const dom = await load("money.js", PAGE);
  const { all, hides } = parts(dom);
  click(dom, all);
  assert.deepEqual(
    hides.map((b) => b.hidden),
    [false, false],
  );
  assert.equal(all.textContent, "Hide all");
  assert.equal(all.getAttribute("aria-pressed"), "true");
});

test("Hide all hides every figure again", async () => {
  const dom = await load("money.js", PAGE);
  const { all, hides } = parts(dom);
  click(dom, all);
  click(dom, all);
  assert.deepEqual(
    hides.map((b) => b.hidden),
    [true, true],
  );
  assert.equal(all.textContent, "Show all");
});

test("the Show all button follows figures revealed one at a time", async () => {
  const dom = await load("money.js", PAGE);
  const { all, shows } = parts(dom);
  click(dom, shows[0]);
  assert.equal(all.textContent, "Show all", "one is still hidden");
  click(dom, shows[1]);
  assert.equal(all.textContent, "Hide all");
});

test("a click elsewhere on the page changes nothing", async () => {
  const dom = await load("money.js", `${PAGE}<p id="other">text</p>`);
  const { hides } = parts(dom);
  click(dom, dom.window.document.getElementById("other"));
  assert.deepEqual(
    hides.map((b) => b.hidden),
    [true, true],
  );
});

test("an amount missing one of its two buttons is left alone", async () => {
  const dom = await load("money.js", `<span data-money><button data-money-show>£•••</button></span>`);
  const show = dom.window.document.querySelector("[data-money-show]");
  click(dom, show);
  assert.equal(show.hidden, false);
});

// hx-boost re-runs body scripts on every navigation while the listener on
// document survives; bound twice, one press would show and hide in one click.
test("running the file twice still toggles once per press", async () => {
  const dom = await page(PAGE);
  run(dom, "money.js");
  run(dom, "money.js");
  const { shows, hides } = parts(dom);
  click(dom, shows[0]);
  assert.equal(hides[0].hidden, false);
});

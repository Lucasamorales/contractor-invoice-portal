// kudos.js: the prize shown on a row follows the place selected, because the
// money follows the selection and not the standings.
import assert from "node:assert/strict";
import { test } from "node:test";

import { fire, load, loadingPage, ready, run } from "./harness.mjs";

const PRIZES = `<script type="application/json" id="kudos-prizes">{"1":"£100.00","2":"£60.00","":"—"}</script>`;

const ROW = `
  <table><tr>
    <td><form class="row-form"><select name="place">
      <option value="1" selected>1st</option>
      <option value="2">2nd</option>
      <option value="3">3rd</option>
    </select></form></td>
    <td data-prize>£100.00</td>
  </tr></table>`;

function parts(dom) {
  const doc = dom.window.document;
  return { select: doc.querySelector("select"), cell: doc.querySelector("[data-prize]") };
}

function choose(dom, select, value) {
  select.value = value;
  fire(dom, select, "change");
}

test("choosing another place shows that place's prize and marks the row changed", async () => {
  const dom = await load("kudos.js", PRIZES + ROW);
  const { select, cell } = parts(dom);
  choose(dom, select, "2");
  assert.equal(cell.textContent, "£60.00");
  assert.equal(cell.classList.contains("changed"), true);
});

test("going back to the computed place removes the changed mark", async () => {
  const dom = await load("kudos.js", PRIZES + ROW);
  const { select, cell } = parts(dom);
  choose(dom, select, "2");
  choose(dom, select, "1");
  assert.equal(cell.textContent, "£100.00");
  assert.equal(cell.classList.contains("changed"), false);
});

test("a place with no configured prize leaves the figure as the server drew it", async () => {
  const dom = await load("kudos.js", PRIZES + ROW);
  const { select, cell } = parts(dom);
  choose(dom, select, "3");
  assert.equal(cell.textContent, "£100.00");
});

test("unreadable prize data leaves the server-rendered figure alone", async () => {
  const dom = await load("kudos.js", `<script type="application/json" id="kudos-prizes">{oops</script>${ROW}`);
  const { select, cell } = parts(dom);
  choose(dom, select, "2");
  assert.equal(cell.textContent, "£100.00");
});

test("a page with no prize data is left untouched", async () => {
  const dom = await load("kudos.js", ROW);
  const { select, cell } = parts(dom);
  choose(dom, select, "2");
  assert.equal(cell.textContent, "£100.00");
});

test("a select whose row has no prize cell is skipped", async () => {
  const dom = await load(
    "kudos.js",
    `${PRIZES}<table><tr><td><form class="row-form"><select name="place"><option value="2">2nd</option></select></form></td></tr></table>`,
  );
  const select = dom.window.document.querySelector("select");
  assert.doesNotThrow(() => choose(dom, select, "2"));
});

test("loaded before the document is ready, it waits for it", async () => {
  const dom = loadingPage(PRIZES + ROW);
  assert.equal(dom.window.document.readyState, "loading");
  run(dom, "kudos.js");
  await ready(dom);
  const { select, cell } = parts(dom);
  choose(dom, select, "2");
  assert.equal(cell.textContent, "£60.00");
});

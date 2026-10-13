// receipts.js: uploading a receipt the moment it is chosen, holding the
// submit buttons until the server has answered, the running total, and the
// "another expense" button.
import assert from "node:assert/strict";
import { test } from "node:test";

import { click, fire, page, run, type } from "./harness.mjs";

const ROW = `
  <div class="item">
    <input class="desc" name="description">
    <input class="amount" name="amount">
    <input type="file" data-receipt>
    <input type="hidden" name="receipt_uri" value="">
    <span class="receipt-status"></span>
  </div>`;

function wizard({ rows = 1, max = 3, csrf = `{"X-CSRF-Token": "tok-123"}` } = {}) {
  return `
    <div hx-headers='${csrf}'>
      <form class="theme-form"><button type="submit" id="theme">Night</button></form>
      <form id="wizard">
        <div id="items">${ROW.repeat(rows)}</div>
        <button type="button" id="add-row" data-max="${max}">Add another</button>
        <p id="running-total" data-base="250000">£2,500.00</p>
        <button type="submit" id="next"><b>Next</b></button>
      </form>
    </div>`;
}

// A fetch whose reply the test delivers when it chooses, so the state while
// the upload is still in flight can be looked at.
function pendingFetch(dom) {
  const calls = [];
  dom.window.fetch = (url, init) =>
    new Promise((resolve, reject) => {
      calls.push({ url, init, resolve, reject });
    });
  return calls;
}

function reply(status, data) {
  return { ok: status < 400, json: () => Promise.resolve(data) };
}

async function settle() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

async function open(options) {
  const dom = await page(wizard(options));
  const calls = pendingFetch(dom);
  run(dom, "receipts.js");
  const doc = dom.window.document;
  return {
    dom,
    doc,
    calls,
    row: doc.querySelector(".item"),
    file: doc.querySelector("[data-receipt]"),
    hidden: doc.querySelector("[name=receipt_uri]"),
    status: doc.querySelector(".receipt-status"),
    next: doc.getElementById("next"),
  };
}

function choose(dom, input, name = "taxi.jpg") {
  const file = name ? new dom.window.File(["x"], name, { type: "image/jpeg" }) : null;
  Object.defineProperty(input, "files", { value: file ? [file] : [], configurable: true });
  fire(dom, input, "change");
}

test("choosing a file posts it to /receipts with the page's CSRF token", async () => {
  const { dom, calls, file } = await open();
  choose(dom, file);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/receipts");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual({ ...calls[0].init.headers }, { "X-CSRF-Token": "tok-123" });
  assert.equal(calls[0].init.body.get("file").name, "taxi.jpg");
});

test("the submit button is held, and says why, until the upload answers", async () => {
  const { dom, calls, file, next, status } = await open();
  choose(dom, file);
  assert.equal(next.disabled, true);
  assert.equal(next.textContent, "Waiting for the receipt…");
  assert.equal(status.textContent, "Checking…");

  calls[0].resolve(reply(200, { uri: "gs://b/r1", name: "taxi.jpg" }));
  await settle();
  assert.equal(next.disabled, false);
  assert.equal(next.innerHTML, "<b>Next</b>", "the original label comes back intact");
});

test("the theme toggle in the header is never disabled by an upload", async () => {
  const { dom, doc, file } = await open();
  choose(dom, file);
  assert.equal(doc.getElementById("theme").disabled, false);
  assert.equal(doc.getElementById("theme").textContent, "Night");
});

test("a stored receipt fills the hidden field and names the file", async () => {
  const { dom, calls, file, hidden, status } = await open();
  choose(dom, file);
  calls[0].resolve(reply(200, { uri: "gs://b/r1", name: "taxi.jpg" }));
  await settle();
  assert.equal(hidden.value, "gs://b/r1");
  assert.equal(status.textContent, "Attached: taxi.jpg");
  assert.equal(status.classList.contains("missing"), false);
});

test("a converted photo is reported as converted", async () => {
  const { dom, calls, file, status } = await open();
  choose(dom, file, "photo.heic");
  calls[0].resolve(reply(200, { uri: "gs://b/r2", name: "photo.jpg", converted: true }));
  await settle();
  assert.equal(status.textContent, "Attached: photo.jpg (converted to JPEG)");
});

test("a refusal shows the server's wording and clears the hidden field", async () => {
  const { dom, calls, file, hidden, status, next } = await open();
  hidden.value = "gs://b/old";
  choose(dom, file, "huge.pdf");
  calls[0].resolve(reply(413, { error: "That file is larger than 10 MB." }));
  await settle();
  assert.equal(status.textContent, "That file is larger than 10 MB.");
  assert.equal(status.classList.contains("missing"), true);
  assert.equal(hidden.value, "");
  assert.equal(next.disabled, false);
});

test("a refusal with no reason still says the file was not accepted", async () => {
  const { dom, calls, file, status } = await open();
  choose(dom, file);
  calls[0].resolve(reply(400, {}));
  await settle();
  assert.equal(status.textContent, "That file was not accepted.");
});

test("a dropped connection is reported and the button is released", async () => {
  const { dom, calls, file, status, next } = await open();
  choose(dom, file);
  calls[0].reject(new TypeError("network"));
  await settle();
  assert.equal(status.textContent, "That upload did not go through. Try again.");
  assert.equal(status.classList.contains("missing"), true);
  assert.equal(next.disabled, false);
});

test("the button stays held while any of several uploads is outstanding", async () => {
  const { dom, doc, calls, next } = await open({ rows: 2 });
  const [first, second] = doc.querySelectorAll("[data-receipt]");
  choose(dom, first);
  choose(dom, second);
  calls[0].resolve(reply(200, { uri: "gs://b/1", name: "a.jpg" }));
  await settle();
  assert.equal(next.disabled, true, "the second has not answered");
  calls[1].resolve(reply(200, { uri: "gs://b/2", name: "b.jpg" }));
  await settle();
  assert.equal(next.disabled, false);
});

test("clearing the file clears the stored location without uploading", async () => {
  const { dom, calls, file, hidden, status } = await open();
  hidden.value = "gs://b/old";
  status.textContent = "Attached: old.jpg";
  choose(dom, file, null);
  assert.equal(calls.length, 0);
  assert.equal(hidden.value, "");
  assert.equal(status.textContent, "");
});

test("a page with no readable CSRF token sends no token header", async () => {
  const { dom, calls, file } = await open({ csrf: "not json" });
  choose(dom, file);
  assert.deepEqual({ ...calls[0].init.headers }, {});
});

test("a change on any other field uploads nothing", async () => {
  const { dom, doc, calls } = await open();
  fire(dom, doc.querySelector(".desc"), "change");
  assert.equal(calls.length, 0);
});

test("the running total adds the expenses typed to the base figure", async () => {
  const { dom, doc } = await open({ rows: 2 });
  const [a, b] = doc.querySelectorAll(".amount");
  type(dom, a, "12.50");
  type(dom, b, "£1,000");
  assert.equal(doc.getElementById("running-total").textContent, "£3,512.50");
});

test("blank, negative and unreadable amounts are left out of the total", async () => {
  const { dom, doc } = await open({ rows: 3 });
  const [a, b, c] = doc.querySelectorAll(".amount");
  type(dom, a, "-5");
  type(dom, b, "abc");
  type(dom, c, "0.99");
  assert.equal(doc.getElementById("running-total").textContent, "£2,500.99");
});

test("Add another appends an empty copy of the last row and focuses it", async () => {
  const { dom, doc, status } = await open();
  doc.querySelector(".desc").value = "Train";
  doc.querySelector(".amount").value = "40";
  status.textContent = "Attached: t.jpg";
  status.classList.add("missing");

  click(dom, doc.getElementById("add-row"));
  const rows = doc.querySelectorAll(".item");
  assert.equal(rows.length, 2);
  assert.equal(rows[1].querySelector(".desc").value, "");
  assert.equal(rows[1].querySelector(".amount").value, "");
  assert.equal(rows[1].querySelector(".receipt-status").textContent, "");
  assert.equal(rows[1].querySelector(".receipt-status").classList.contains("missing"), false);
  assert.equal(doc.activeElement, rows[1].querySelector(".desc"));
  assert.equal(rows[0].querySelector(".desc").value, "Train", "the original row is untouched");
});

test("the add button stops at the server's limit and says so", async () => {
  const { dom, doc } = await open({ rows: 2, max: 3 });
  const button = doc.getElementById("add-row");
  click(dom, button);
  assert.equal(doc.querySelectorAll(".item").length, 3);
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, "That is the most we can attach");
});

test("a page already at the limit does not grow", async () => {
  const { dom, doc } = await open({ rows: 3, max: 3 });
  click(dom, doc.getElementById("add-row"));
  assert.equal(doc.querySelectorAll(".item").length, 3);
});

// hx-boost re-runs body scripts on every navigation; bound twice, one chosen
// file would upload once per visit.
test("running the file twice still uploads a chosen file once", async () => {
  const { dom, calls, file } = await open();
  run(dom, "receipts.js");
  choose(dom, file);
  assert.equal(calls.length, 1);
});

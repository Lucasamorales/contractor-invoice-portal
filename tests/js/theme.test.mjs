// theme.js: the day/night toggle paints at once, records the choice in a
// cookie, and falls back to the server only when the cookie will not take.
import assert from "node:assert/strict";
import { test } from "node:test";

import { page, run } from "./harness.mjs";

const FORM = `
  <form class="theme-form" action="/theme" method="post">
    <input type="hidden" name="next" value="/">
    <button type="submit" class="theme-toggle" aria-pressed="false">Toggle</button>
  </form>`;

async function open({ dark = false, url, body = FORM } = {}) {
  const dom = await page(body, { url });
  dom.window.matchMedia = () => ({ matches: dark });
  const calls = [];
  dom.window.fetch = (target, init) => {
    calls.push({ target, init });
    return calls.reply ?? Promise.resolve({ ok: true, type: "basic" });
  };
  run(dom, "theme.js");
  const doc = dom.window.document;
  return { dom, doc, calls, root: doc.documentElement, button: doc.querySelector(".theme-toggle") };
}

function press(dom, doc) {
  const event = new dom.window.Event("submit", { bubbles: true, cancelable: true });
  doc.querySelector(".theme-form").dispatchEvent(event);
  return event;
}

test("the first press on a light machine switches to night", async () => {
  const { dom, doc, root, button } = await open();
  const event = press(dom, doc);
  assert.equal(root.getAttribute("data-theme"), "night");
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.getAttribute("aria-label"), "Switch to day mode");
  assert.equal(button.getAttribute("title"), "Switch to day mode");
  assert.equal(event.defaultPrevented, true, "the form is not posted");
});

test("the first press on a dark machine switches to day", async () => {
  const { dom, doc, root, button } = await open({ dark: true });
  press(dom, doc);
  assert.equal(root.getAttribute("data-theme"), "day");
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(button.getAttribute("aria-label"), "Switch to night mode");
});

test("an explicit choice already on the page wins over the machine's preference", async () => {
  const { dom, doc, root } = await open({ dark: true });
  root.setAttribute("data-theme", "day");
  press(dom, doc);
  assert.equal(root.getAttribute("data-theme"), "night");
});

test("the choice is written to a year-long cookie and no request is made", async () => {
  const { dom, doc, calls } = await open();
  press(dom, doc);
  assert.match(doc.cookie, /theme=night/);
  assert.equal(calls.length, 0);
});

test("pressing twice lands back on day", async () => {
  const { dom, doc, root } = await open();
  press(dom, doc);
  press(dom, doc);
  assert.equal(root.getAttribute("data-theme"), "day");
  assert.match(doc.cookie, /theme=day/);
});

// A browser refuses a script write over an httponly cookie of the same name.
function refuseCookies(doc) {
  Object.defineProperty(doc, "cookie", { get: () => "", set: () => {}, configurable: true });
}

test("when the cookie will not take, the server is asked to record it", async () => {
  const { dom, doc, calls, root } = await open();
  refuseCookies(doc);
  press(dom, doc);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].target, "https://portal.test/theme");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.keepalive, true);
  assert.equal(calls[0].init.body.get("next"), "/");
  await Promise.resolve();
  assert.equal(root.getAttribute("data-theme"), "night");
});

test("a redirect answer from the server counts as recorded", async () => {
  const { dom, doc, calls, root } = await open();
  refuseCookies(doc);
  calls.reply = Promise.resolve({ ok: false, type: "opaqueredirect" });
  press(dom, doc);
  await calls.reply;
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(root.getAttribute("data-theme"), "night");
});

test("if nothing recorded the choice, the page goes back to how it was", async () => {
  const { dom, doc, calls, root, button } = await open();
  refuseCookies(doc);
  calls.reply = Promise.resolve({ ok: false, type: "basic" });
  press(dom, doc);
  await calls.reply;
  for (let i = 0; i < 4; i++) await Promise.resolve();
  assert.equal(root.getAttribute("data-theme"), "day");
  assert.equal(button.getAttribute("aria-pressed"), "false");
});

test("a failed request also puts the page back", async () => {
  const { dom, doc, calls, root } = await open({ dark: true });
  refuseCookies(doc);
  calls.reply = Promise.reject(new TypeError("offline"));
  press(dom, doc);
  for (let i = 0; i < 4; i++) await Promise.resolve();
  assert.equal(root.getAttribute("data-theme"), "night");
});

test("submitting any other form is left to the browser", async () => {
  const { dom, doc, root } = await open({ body: `${FORM}<form id="other"></form>` });
  const event = new dom.window.Event("submit", { bubbles: true, cancelable: true });
  doc.getElementById("other").dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
  assert.equal(root.hasAttribute("data-theme"), false);
});

test("a toggle form with no button still switches the page", async () => {
  const { dom, doc, root } = await open({ body: `<form class="theme-form" action="/theme"></form>` });
  press(dom, doc);
  assert.equal(root.getAttribute("data-theme"), "night");
});

// After four navigations an unguarded listener toggled four times and landed
// back where it started, which looks like a button that does nothing.
test("running the file twice still toggles once per press", async () => {
  const { dom, doc, root } = await open();
  run(dom, "theme.js");
  press(dom, doc);
  assert.equal(root.getAttribute("data-theme"), "night");
});

test("over plain http the cookie is written without the Secure flag", async () => {
  const { dom, doc } = await open({ url: "http://localhost:8000/" });
  let written = "";
  Object.defineProperty(doc, "cookie", {
    get: () => written,
    set: (value) => {
      written = value;
    },
    configurable: true,
  });
  press(dom, doc);
  assert.match(written, /^theme=night;Path=\/;Max-Age=31536000;SameSite=Lax$/);
});

// Loads one of the browser scripts from app/static into a jsdom window.
//
// The scripts are plain files with no exports (the CSP is script-src 'self' and
// there is no build step), so they can only be exercised the way a browser
// does: put markup on a page, run the file, raise events, read the DOM back.
import { readFileSync } from "node:fs";
import { afterEach } from "node:test";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

import { JSDOM } from "jsdom";

const STATIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../app/static");

// A jsdom window keeps timers alive, and one with an iframe keeps the process
// from exiting at all, so every page a test opened is closed when it ends.
const open = [];
afterEach(() => {
  while (open.length) open.pop().window.close();
});

/**
 * A page holding `body` that has not reached DOMContentLoaded yet, which is
 * the state a script in the <head> would find. Synchronous on purpose: jsdom
 * finishes loading as soon as the caller yields.
 */
export function loadingPage(body = "", { url = "https://portal.test/" } = {}) {
  const dom = new JSDOM(`<!doctype html><html><head></head><body>${body}</body></html>`, {
    url,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  open.push(dom);
  return dom;
}

/** A page holding `body`, parsed and loaded, with none of our scripts run yet. */
export async function page(body = "", options) {
  const dom = loadingPage(body, options);
  if (dom.window.document.readyState === "loading") await ready(dom);
  return dom;
}

export function ready(dom) {
  return new Promise((resolve) => {
    dom.window.document.addEventListener("DOMContentLoaded", resolve, { once: true });
  });
}

/**
 * Runs a file from app/static in `dom`, as a <script src> at the end of the
 * body would. The real path is given as the script's name so coverage is
 * attributed to the file in the repo rather than to an anonymous script.
 */
export function run(dom, script) {
  const file = path.join(STATIC, script);
  vm.runInContext(readFileSync(file, "utf8"), dom.getInternalVMContext(), {
    filename: pathToFileURL(file).href,
  });
  return dom;
}

/** The common case: a page with this markup and this one script. */
export async function load(script, body, options) {
  return run(await page(body, options), script);
}

export function fire(dom, element, type, init = {}) {
  const Event = init.key !== undefined ? dom.window.KeyboardEvent : dom.window.Event;
  element.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, ...init }));
}

export function click(dom, element) {
  element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
}

/** Types into a field the way a keystroke does: set the value, then raise input. */
export function type(dom, input, value) {
  input.value = value;
  input.setSelectionRange?.(value.length, value.length);
  fire(dom, input, "input");
}

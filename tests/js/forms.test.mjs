// forms.js: shaping bank and money fields as they are typed, and the
// "type it twice" check on the account number (R-BANK-03).
import assert from "node:assert/strict";
import { test } from "node:test";

import { fire, load, type } from "./harness.mjs";

async function field(format, extra = "") {
  const dom = await load("forms.js", `<form><input name="f" data-format="${format}" ${extra}></form>`);
  return { dom, input: dom.window.document.querySelector("input") };
}

test("a sort code is grouped in pairs and stops at six digits", async () => {
  const { dom, input } = await field("sort-code");
  type(dom, input, "230801");
  assert.equal(input.value, "23-08-01");
  type(dom, input, "23-08-0199");
  assert.equal(input.value, "23-08-01");
});

test("an account number refuses letters and stops at eight digits", async () => {
  const { dom, input } = await field("account");
  type(dom, input, "12a34b5678999");
  assert.equal(input.value, "12345678");
});

test("an IBAN is upper-cased and grouped in fours", async () => {
  const { dom, input } = await field("iban");
  type(dom, input, "gb82 west1234-5698 765432");
  assert.equal(input.value, "GB82 WEST 1234 5698 7654 32");
});

test("a BIC is upper-cased and capped at eleven characters", async () => {
  const { dom, input } = await field("bic");
  type(dom, input, "deut-de ff500extra");
  assert.equal(input.value, "DEUTDEFF500");
});

test("money keeps one dot and two decimals", async () => {
  const { dom, input } = await field("money");
  type(dom, input, "£1,234.567");
  assert.equal(input.value, "1234.56");
});

test("a second dot in a money field does not move the first", async () => {
  const { dom, input } = await field("money");
  type(dom, input, "12.3.4");
  assert.equal(input.value, "12.34");
});

test("a phone number keeps digits and the punctuation a number uses", async () => {
  const { dom, input } = await field("phone");
  type(dom, input, "+44 (0)7700 900000 ext");
  assert.equal(input.value, "+44 (0)7700 900000 ");
});

test("a value that is already in shape is left alone, caret included", async () => {
  const { dom, input } = await field("sort-code");
  input.value = "23-08-01";
  input.setSelectionRange(2, 2);
  fire(dom, input, "input");
  assert.equal(input.value, "23-08-01");
  assert.equal(input.selectionStart, 2);
});

test("the caret stays after the character just typed when a separator is added", async () => {
  const { dom, input } = await field("sort-code");
  input.value = "230";
  input.setSelectionRange(3, 3);
  fire(dom, input, "input");
  assert.equal(input.value, "23-0");
  assert.equal(input.selectionStart, 4);
});

test("typing only characters the format drops leaves the caret at the start", async () => {
  const { dom, input } = await field("account");
  type(dom, input, "--");
  assert.equal(input.value, "");
  assert.equal(input.selectionStart, 0);
});

test("a pasted value is shaped on change, for browsers that skip input", async () => {
  const { dom, input } = await field("iban");
  input.value = "lu280019400644750000";
  fire(dom, input, "change");
  assert.equal(input.value, "LU28 0019 4006 4475 0000");
});

test("a field with an unknown format is not touched", async () => {
  const { dom, input } = await field("postcode");
  type(dom, input, "ab-12");
  assert.equal(input.value, "ab-12");
});

async function pair() {
  const dom = await load(
    "forms.js",
    `<form>
       <input name="account_number">
       <input name="confirm" data-match="account_number">
     </form>`,
  );
  const doc = dom.window.document;
  return {
    dom,
    first: doc.querySelector("[name=account_number]"),
    second: doc.querySelector("[name=confirm]"),
    error: () => doc.querySelector(".field-error"),
  };
}

test("no complaint while the second number could still become the first", async () => {
  const { dom, first, second, error } = await pair();
  first.value = "12345678";
  type(dom, second, "1234");
  assert.equal(error().hidden, true);
  assert.equal(second.validity.customError, false);
});

test("a mismatch is said while typing and blocks the submit", async () => {
  const { dom, first, second, error } = await pair();
  first.value = "12345678";
  type(dom, second, "1243");
  assert.equal(error().hidden, false);
  assert.equal(error().textContent, "The two account numbers do not match.");
  assert.equal(second.validationMessage, "The two account numbers do not match.");
});

test("leaving the field with only a prefix typed is still a mismatch", async () => {
  const { dom, first, second, error } = await pair();
  first.value = "12345678";
  type(dom, second, "1234");
  fire(dom, second, "blur");
  assert.equal(error().hidden, false);
  assert.equal(second.validity.customError, true);
});

test("correcting the first field clears the error on the second", async () => {
  const { dom, first, second, error } = await pair();
  first.value = "12345678";
  type(dom, second, "1243");
  type(dom, first, "12435678");
  assert.equal(error().hidden, true);
  assert.equal(second.validity.customError, false);
});

test("an existing error element next to the field is reused", async () => {
  const dom = await load(
    "forms.js",
    `<form>
       <input name="account_number" value="12345678">
       <input name="confirm" data-match="account_number"><span class="field-error" hidden></span>
     </form>`,
  );
  const doc = dom.window.document;
  type(dom, doc.querySelector("[name=confirm]"), "9");
  assert.equal(doc.querySelectorAll(".field-error").length, 1);
  assert.equal(doc.querySelector(".field-error").hidden, false);
});

test("a match field pointing at nothing is ignored", async () => {
  const dom = await load("forms.js", `<form><input name="confirm" data-match="missing"></form>`);
  const input = dom.window.document.querySelector("input");
  type(dom, input, "123");
  assert.equal(dom.window.document.querySelector(".field-error"), null);
});

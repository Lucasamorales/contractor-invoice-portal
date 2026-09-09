// Shaping what somebody types into the shape the field actually holds.
//
// The server is still the authority — app/domain/banking.py normalises and
// validates everything that arrives, and nothing here is trusted. This exists
// so a sort code looks like a sort code while it is being typed, and so a
// letter in an account number is refused at the keystroke rather than after a
// round trip.
//
// Declared on the input as data-format, so a new field is an attribute rather
// than another branch in here.
(function () {
  "use strict";

  // Reformat as groups, keeping the caret sensible by only ever appending
  // separators to the left of it.
  function grouped(digits, size, separator) {
    var out = [];
    for (var i = 0; i < digits.length; i += size) {
      out.push(digits.slice(i, i + size));
    }
    return out.join(separator);
  }

  var FORMATS = {
    // Six digits, shown 23-08-01. The server strips the dashes again.
    "sort-code": function (value) {
      return grouped(value.replace(/\D/g, "").slice(0, 6), 2, "-");
    },
    // Eight digits, nothing else. A UK account number has no letters.
    account: function (value) {
      return value.replace(/\D/g, "").slice(0, 8);
    },
    // Upper case, grouped in fours the way a bank prints one.
    iban: function (value) {
      return grouped(
        value.replace(/[^A-Za-z0-9]/g, "").toUpperCase().slice(0, 34),
        4,
        " "
      );
    },
    // 8 or 11 characters, upper case, letters and digits.
    bic: function (value) {
      return value.replace(/[^A-Za-z0-9]/g, "").toUpperCase().slice(0, 11);
    },
    // Digits, one dot, at most two decimals. Typing a second dot does nothing
    // rather than silently moving the one already there.
    money: function (value) {
      var cleaned = value.replace(/[^0-9.]/g, "");
      var parts = cleaned.split(".");
      if (parts.length > 2) cleaned = parts[0] + "." + parts.slice(1).join("");
      var dot = cleaned.indexOf(".");
      if (dot !== -1) cleaned = cleaned.slice(0, dot + 3);
      return cleaned;
    },
    // Digits, spaces and the punctuation a phone number really uses.
    phone: function (value) {
      return value.replace(/[^0-9+()\-\s]/g, "").slice(0, 24);
    },
  };

  function apply(input) {
    var format = FORMATS[input.getAttribute("data-format")];
    if (!format) return;

    input.addEventListener("input", function () {
      var before = input.value;
      // How many characters sit behind the caret that the format keeps, so the
      // caret can be put back in the same logical place afterwards.
      var caret = input.selectionStart;
      var kept = before.slice(0, caret).replace(/[^0-9A-Za-z.]/g, "").length;

      var after = format(before);
      if (after === before) return;
      input.value = after;

      var seen = 0;
      var position = after.length;
      for (var i = 0; i < after.length; i++) {
        if (/[0-9A-Za-z.]/.test(after[i])) seen++;
        if (seen === kept) {
          position = i + 1;
          break;
        }
      }
      if (kept === 0) position = 0;
      try {
        input.setSelectionRange(position, position);
      } catch (e) {
        // A field that does not support selection. The value is still right.
      }
    });

    // Paste and autofill do not always raise input in older browsers.
    input.addEventListener("change", function () {
      input.value = format(input.value);
    });
  }

  // ----------------------------------------------------------- matching --
  //
  // The second account number exists to catch a transposition (R-BANK-03),
  // and the field says so — so it should say when they differ, while there is
  // still a cursor in it, rather than after a round trip.
  //
  // Declared as data-match="<name of the other field>", like data-format.

  function message(input) {
    var next = input.nextElementSibling;
    if (next && next.classList.contains("field-error")) return next;
    var span = document.createElement("span");
    span.className = "field-error";
    span.hidden = true;
    input.insertAdjacentElement("afterend", span);
    return span;
  }

  function compare(input, other) {
    var typed = input.value;
    var target = other.value;
    var error = message(input);

    // Nothing to say while either is empty — that is what required is for —
    // and nothing to say while what is typed could still become the other
    // one. Complaining at the first character is how a form becomes noise.
    var wrong = Boolean(typed) && Boolean(target) && target.indexOf(typed) !== 0;

    if (wrong) {
      error.textContent = "The two numbers are different.";
      error.hidden = false;
      // The browser refuses the submit and moves focus here, which is what
      // it already does for a malformed email address.
      input.setCustomValidity("The two numbers are different.");
    } else {
      error.hidden = true;
      input.setCustomValidity("");
    }
  }

  function match(input) {
    var form = input.form;
    var other = form && form.elements[input.getAttribute("data-match")];
    if (!other) return;

    var check = function () {
      compare(input, other);
    };
    input.addEventListener("input", check);
    input.addEventListener("blur", function () {
      // On the way out, a value that is merely a prefix is still wrong.
      if (input.value && other.value && input.value !== other.value) {
        message(input).textContent = "The two numbers are different.";
        message(input).hidden = false;
        input.setCustomValidity("The two numbers are different.");
      }
    });
    // Correcting the first field has to clear an error on the second.
    other.addEventListener("input", check);
  }

  function init() {
    document.querySelectorAll("input[data-format]").forEach(apply);
    document.querySelectorAll("input[data-match]").forEach(match);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

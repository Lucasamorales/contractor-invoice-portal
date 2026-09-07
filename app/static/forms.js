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

  function init() {
    document.querySelectorAll("input[data-format]").forEach(apply);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

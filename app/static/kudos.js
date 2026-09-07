// The prize a row will actually pay, kept in step with the place selected.
//
// The dropdown is there for ties: the place the counts suggest is not always
// the place awarded, and the money follows the selection, not the standings.
// Showing the computed prize next to a changed dropdown said one thing and
// paid another.
//
// Plain JS in one file, like receipts.js and tutorial.js — the CSP is
// script-src 'self' and there is no build step (CLAUDE.md §3). The amounts
// come from the server, because they are configuration and the template is
// the only place that knows how to format money.
(function () {
  "use strict";

  function init() {
    var source = document.getElementById("kudos-prizes");
    if (!source) return;

    var prizes;
    try {
      prizes = JSON.parse(source.textContent) || {};
    } catch (e) {
      return; // Leave the server-rendered figure alone rather than blank it.
    }

    document.querySelectorAll(".row-form select[name=place]").forEach(function (select) {
      var row = select.closest("tr");
      var cell = row && row.querySelector("[data-prize]");
      if (!cell) return;

      // Remember what the standings said, so the row can say when the money
      // is no longer the one the counts implied.
      var computed = select.value;

      select.addEventListener("change", function () {
        var amount = prizes[select.value];
        if (amount === undefined) return;
        cell.textContent = amount;
        cell.classList.toggle("changed", select.value !== computed);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

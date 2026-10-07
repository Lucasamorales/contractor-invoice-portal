// Show and hide amounts on the Finance screens (admin/_money.html).
//
// Every amount opens as £•••. Pressing it shows the figure; pressing the
// figure hides it again. Nothing is fetched: the figure is already in the page
// and this only swaps which of the two buttons is showing. A fee arrives from
// its audited reveal already in this shape, so hiding and showing it again
// reuses what was fetched rather than recording a second reveal.
//
// Nothing is remembered. Every page opens hidden, including after pressing
// Show all on the previous one.
//
// External file, not inline: the CSP is script-src 'self'.
(function () {
  "use strict";

  function set(holder, shown) {
    var show = holder.querySelector("[data-money-show]");
    var hide = holder.querySelector("[data-money-hide]");
    if (!show || !hide) return;
    show.hidden = shown;
    hide.hidden = !shown;
  }

  function syncAll(button) {
    var holders = document.querySelectorAll("[data-money]");
    var anyHidden = Array.prototype.some.call(holders, function (holder) {
      var hide = holder.querySelector("[data-money-hide]");
      return hide && hide.hidden;
    });
    button.textContent = anyHidden ? "Show all" : "Hide all";
    button.setAttribute("aria-pressed", anyHidden ? "false" : "true");
  }

  function allButtons() {
    return document.querySelectorAll("[data-money-all]");
  }

  // Bound once, however many times this file runs: it sits in the body, and
  // hx-boost re-executes body scripts on every navigation while a listener on
  // document outlives the swap. Unguarded, a second visit bound it twice and
  // one press showed and hid again in the same click.
  if (window.__portalMoneyBound) return;
  window.__portalMoneyBound = true;

  document.addEventListener("click", function (event) {
    var target = event.target.closest && event.target.closest(
      "[data-money-show], [data-money-hide], [data-money-all]"
    );
    if (!target) return;

    if (target.hasAttribute("data-money-all")) {
      var reveal = target.getAttribute("aria-pressed") !== "true";
      document.querySelectorAll("[data-money]").forEach(function (holder) {
        set(holder, reveal);
      });
      allButtons().forEach(syncAll);
      return;
    }

    var holder = target.closest("[data-money]");
    if (!holder) return;
    var showing = target.hasAttribute("data-money-show");
    set(holder, showing);

    // Keep the keyboard where it was: the button pressed has just hidden.
    var next = holder.querySelector(
      showing ? "[data-money-hide]" : "[data-money-show]"
    );
    if (next) next.focus();

    allButtons().forEach(syncAll);
  });
})();

// The on-screen tutorial: numbered pins with a spotlight.
//
// Plain JS in one file, like receipts.js — the CSP is script-src 'self' and
// there is no build step (CLAUDE.md §3).
//
// Not a forced linear tour, because of the cadence: people use this once a
// month and come back knowing most of it but wanting one specific answer. Pins
// let them go straight to it.
//
// The copy lives server-side and arrives as JSON. A pin finds the element it
// explains from its own nearest positioned ancestor, so declaring a step means
// adding one data-tut attribute in a template and nothing else.
(function () {
  "use strict";

  var GAP = 22; // between the lit element and the card
  var EDGE = 24; // smallest gap to the viewport edge
  var BAND = 116; // where a lit element should sit from the top

  var steps = [];
  var on = false;
  var index = -1;
  var lastFocus = null;

  var toggle, banner, backdrop, card, lit;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function target(step) {
    return document.querySelector('[data-tut="' + step.key + '"]');
  }

  function present() {
    // Ordered by where they sit on the page, not by how they were declared.
    // Numbering that runs 1 at the foot and 2 at the top is worse than no
    // numbering, and which steps exist depends on the state the screen is in.
    return steps.filter(target).sort(function (a, b) {
      var position = target(a).compareDocumentPosition(target(b));
      if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
      if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
      return 0;
    });
  }

  // ---------------------------------------------------------------- pins --

  function addPins() {
    present().forEach(function (step, i) {
      var host = target(step);
      if (host.querySelector(".tut-pin")) return;
      var pin = el("button", "tut-pin", String(i + 1));
      pin.type = "button";
      pin.setAttribute("aria-label", "Explain " + step.title);
      pin.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        show(i);
      });
      host.appendChild(pin);
    });
  }

  function removePins() {
    document.querySelectorAll(".tut-pin").forEach(function (pin) {
      pin.remove();
    });
  }

  // ----------------------------------------------------------- spotlight --

  function clearLit() {
    if (lit) {
      lit.removeAttribute("data-spot");
      lit = null;
    }
  }

  function place() {
    if (!lit || !card) return;
    // Measure both, never estimate. At the foot of a page the scroll cannot
    // reach the band, so the target does not land where it was asked to, and a
    // guessed card height puts the card straight over the thing it explains.
    var rect = lit.getBoundingClientRect();
    var height = card.offsetHeight;
    var below = window.innerHeight - rect.bottom - GAP - EDGE;
    var above = rect.top - GAP - EDGE;

    // Whole or not at all. A card clamped to the space beside its element
    // scrolls, and a step you have to scroll through is a step half of which
    // nobody reads. If neither side has room the card leaves the element and
    // sits in the middle of the window, where it always has room.
    var top;
    if (height <= below) {
      top = rect.bottom + GAP;
    } else if (height <= above) {
      top = rect.top - GAP - height;
    } else {
      top = Math.max(EDGE, (window.innerHeight - height) / 2);
    }

    var left = rect.left;
    var width = card.offsetWidth;
    if (left + width > window.innerWidth - EDGE) {
      left = window.innerWidth - EDGE - width;
    }
    card.style.top = Math.max(EDGE, top) + "px";
    card.style.left = Math.max(EDGE, left) + "px";
  }

  function show(i) {
    var list = present();
    if (!list.length) return;
    index = Math.max(0, Math.min(i, list.length - 1));
    var step = list[index];
    var host = target(step);
    if (!host) return;

    clearLit();
    lit = host;
    lit.setAttribute("data-spot", "on");

    // 'auto', not 'smooth': a scroll still in flight makes the measurement
    // below wrong, and the lit element ends up off-screen.
    var wanted = window.scrollY + host.getBoundingClientRect().top - BAND;
    window.scrollTo({ top: Math.max(0, wanted), behavior: "auto" });

    backdrop.hidden = false;
    card.hidden = false;
    card.innerHTML = "";

    var head = el("div", "tut-head");
    head.appendChild(el("span", "tut-num", String(index + 1)));
    head.appendChild(el("span", "tut-count", index + 1 + " of " + list.length));
    var close = el("button", "tut-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.addEventListener("click", closeCard);
    head.appendChild(close);
    card.appendChild(head);

    card.appendChild(el("h2", "tut-title", step.title));
    card.appendChild(el("p", "tut-body", step.body));

    var actions = el("div", "tut-actions");
    var back = el("button", "button ghost small", "Back");
    back.type = "button";
    back.disabled = index === 0;
    back.addEventListener("click", function () {
      show(index - 1);
    });
    var next = el("button", "button small", index === list.length - 1 ? "Done" : "Next");
    next.type = "button";
    next.addEventListener("click", function () {
      if (index === list.length - 1) stop();
      else show(index + 1);
    });
    actions.appendChild(back);
    actions.appendChild(next);
    card.appendChild(actions);

    place();
    next.focus();
  }

  function closeCard() {
    // Leaves tutorial mode on, so the pins are still there to jump between.
    clearLit();
    backdrop.hidden = true;
    card.hidden = true;
    index = -1;
  }

  // ---------------------------------------------------------------- mode --

  function start() {
    if (!steps.length) return;
    on = true;
    lastFocus = document.activeElement;
    toggle.setAttribute("aria-expanded", "true");
    document.body.classList.add("tut-on");
    addPins();
    banner.hidden = false;
    banner.querySelector(".tut-banner-count").textContent =
      present().length + " on this page.";
    show(0);
  }

  function stop() {
    on = false;
    toggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("tut-on");
    closeCard();
    removePins();
    banner.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // ---------------------------------------------------------------- wire --

  function build() {
    backdrop = el("div", "tut-backdrop");
    backdrop.hidden = true;
    backdrop.addEventListener("click", closeCard);
    document.body.appendChild(backdrop);

    card = el("div", "tut-card");
    card.hidden = true;
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "false");
    document.body.appendChild(card);

    banner = el("div", "tut-banner");
    banner.hidden = true;
    var dot = el("span", "tut-banner-dot");
    banner.appendChild(dot);
    banner.appendChild(el("span", "tut-banner-label", "Tutorial"));
    banner.appendChild(el("span", "tut-banner-count", ""));
    var done = el("button", "button ghost small", "Done");
    done.type = "button";
    done.addEventListener("click", stop);
    banner.appendChild(done);

    var bar = document.querySelector(".bar");
    if (bar && bar.parentNode) bar.parentNode.insertBefore(banner, bar.nextSibling);
  }

  function init() {
    var source = document.getElementById("tutorial-steps");
    toggle = document.getElementById("help-toggle");
    if (!source || !toggle) return;
    try {
      steps = JSON.parse(source.textContent) || [];
    } catch (e) {
      steps = [];
    }

    build();

    if (!steps.length) {
      toggle.disabled = true;
      toggle.title = "No tutorial on this screen yet";
      return;
    }

    toggle.addEventListener("click", function () {
      if (on) stop();
      else start();
    });

    // The toggle and the card belong to this page and go when the body is
    // swapped, so they are bound every time. These two are not: they are on
    // document and window, which survive, and hx-boost re-runs this file on
    // every navigation. Without the guard each navigation added another copy.
    if (!window.__portalTutorialBound) {
      window.__portalTutorialBound = true;
      document.addEventListener("keydown", function (event) {
        if (!on) return;
        if (event.key === "Escape") stop();
        if (card.hidden) return;
        if (event.key === "ArrowRight") show(index + 1);
        if (event.key === "ArrowLeft") show(index - 1);
      });
      window.addEventListener("resize", function () {
        place();
      });
    }
    // Pins live inside swapped fragments (the fee reveal, for one), so they
    // have to be put back after HTMX replaces a chunk of the page.
    document.body.addEventListener("htmx:afterSwap", function () {
      if (on) addPins();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

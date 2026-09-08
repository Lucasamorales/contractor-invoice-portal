// Grow the preview frame to whatever the invoice actually is.
//
// The frame is A4 at 96dpi, which is the normal case. An invoice with ten
// expenses is taller than one page, and a frame that cannot grow leaves you
// scrolling inside a picture of a piece of paper — the one place a scrollbar
// makes no sense at all.
//
// srcdoc is same-origin, so the document inside can simply be measured.
// External file, not inline: the CSP is script-src 'self'.
(function () {
  "use strict";

  function fit(frame) {
    var doc = frame.contentDocument;
    if (!doc || !doc.body) return;

    var sheet = doc.querySelector(".sheet") || doc.body;
    var height = Math.ceil(
      Math.max(sheet.scrollHeight, doc.documentElement.scrollHeight)
    );
    if (!height) return;

    // The scale is set in CSS and is what maps A4 onto the frame's width.
    var scale = frame.getBoundingClientRect().width / frame.offsetWidth;
    frame.style.height = height + "px";

    var outer = frame.closest(".sheet-frame");
    if (outer && scale > 0) outer.style.height = Math.ceil(height * scale) + "px";
  }

  function watch(frame) {
    // srcdoc has usually parsed by the time this runs; load covers the rest,
    // and fonts settling can change the height again.
    fit(frame);
    frame.addEventListener("load", function () {
      fit(frame);
    });
    if (frame.contentDocument && frame.contentDocument.fonts) {
      frame.contentDocument.fonts.ready.then(function () {
        fit(frame);
      });
    }
    window.addEventListener("resize", function () {
      fit(frame);
    });
  }

  function init() {
    document.querySelectorAll("iframe.sheet-scale").forEach(watch);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

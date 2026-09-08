// Make the theme toggle instant.
//
// Without this the toggle costs two network round trips in series: a POST to
// set the cookie, a 303, and then a whole page fetched again to produce a
// document that differs only in colour. Locally that is 30ms; through IAP to
// Cloud Run and back to a database in the EU it is a second or two, and it
// feels like the app is thinking about it.
//
// So the page changes here, immediately, and the cookie is written in the
// background. Nothing about the server-side design changes: the cookie and
// the rendered data-theme are still the source of truth, which is what keeps
// every later navigation from flashing the wrong theme. This only removes the
// wait on the press itself.
//
// Pure enhancement. With scripting off the form posts as it always did, which
// is the standard the rest of this app already meets. An external file, not
// an inline script, because inline is exactly what the CSP forbids.
(function () {
  "use strict";

  function label(next) {
    return next === "night" ? "Switch to day mode" : "Switch to night mode";
  }

  // The attribute is absent until somebody chooses, so prefers-color-scheme
  // can follow the machine. Read what is actually painted rather than
  // assuming day, or the first press on a dark machine appears to do nothing.
  function current(root) {
    var chosen = root.getAttribute("data-theme");
    if (chosen === "day" || chosen === "night") return chosen;
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "night"
      : "day";
  }

  function paint(root, button, value) {
    root.setAttribute("data-theme", value);
    if (!button) return;
    button.setAttribute("aria-pressed", value === "night" ? "true" : "false");
    button.setAttribute("aria-label", label(value));
    button.setAttribute("title", label(value));
  }

  // Delegated, because hx-boost replaces the body on every navigation and a
  // listener bound to the button would go with it.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.classList || !form.classList.contains("theme-form")) return;

    var root = document.documentElement;
    var button = form.querySelector(".theme-toggle");
    var was = current(root);
    var next = was === "night" ? "day" : "night";

    event.preventDefault();
    paint(root, button, next);

    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      // The 303 goes to a page we are already on and would only be thrown
      // away, so do not follow it.
      redirect: "manual",
    })
      .then(function (response) {
        // An opaque redirect is the 303 arriving as expected; both mean the
        // cookie was written.
        if (!response.ok && response.type !== "opaqueredirect") {
          throw new Error("theme not saved");
        }
      })
      .catch(function () {
        // The cookie was not written, so the next page would come back the
        // way it was. Say so now rather than surprising somebody later.
        paint(root, button, was);
      });
  });
})();

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

  // Bound once, however many times this file runs.
  //
  // These scripts sit at the end of <body>, and hx-boost swaps the body — so
  // htmx re-executes every one of them on every navigation. A delegated
  // listener on document is not replaced with the body, so without this each
  // navigation added another: after four, one press toggled the theme four
  // times and landed back where it started, which looks exactly like a button
  // that does nothing.
  if (window.__portalThemeBound) return;
  window.__portalThemeBound = true;

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

    // Written here rather than asked of the server. The page changes on the
    // press and a request would have to finish afterwards — so pressing the
    // toggle and then leaving the page cancelled it, and the next page came
    // back the way it was. Nothing to cancel if there is no request.
    var year = 60 * 60 * 24 * 365;
    var secure = location.protocol === "https:" ? ";Secure" : "";
    document.cookie =
      "theme=" + next + ";Path=/;Max-Age=" + year + ";SameSite=Lax" + secure;

    if (document.cookie.indexOf("theme=" + next) !== -1) return;

    // The write did not take. A browser refuses a script write over an
    // httponly cookie of the same name, and anybody who used the toggle
    // before this file changed still has one — the server can replace it, and
    // this is also the path when cookies are refused outright. keepalive so
    // that leaving the page does not cancel it, which is the whole point.
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      redirect: "manual",
      keepalive: true,
    })
      .then(function (response) {
        if (!response.ok && response.type !== "opaqueredirect") {
          throw new Error("theme not saved");
        }
      })
      .catch(function () {
        // Nothing recorded it, so the next page would come back the way it
        // was. Say so now rather than surprising somebody later.
        paint(root, button, was);
      });
  });
})();

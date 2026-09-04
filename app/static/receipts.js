// Receipt upload for the Additional step (R-WIZ-04).
//
// A separate file rather than inline script because the CSP is script-src
// 'self' and inline would be blocked — which is the point of the CSP.
//
// The file is uploaded the moment it is chosen, not at submit. Two reasons:
// the person finds out immediately that a photo is unreadable or too large,
// long before an invoice depends on it; and the wizard carries the receipt
// through preview as a URI in a hidden field rather than trying to re-post
// the bytes it cannot hold.
(function () {
  "use strict";

  function setStatus(row, text, isError) {
    var el = row.querySelector(".receipt-status");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("missing", Boolean(isError));
  }

  function upload(row, input) {
    var file = input.files && input.files[0];
    var hidden = row.querySelector('input[name="receipt_uri"]');
    if (!file) {
      if (hidden) hidden.value = "";
      setStatus(row, "");
      return;
    }

    setStatus(row, "Checking…", false);
    if (hidden) hidden.value = "";

    var body = new FormData();
    body.append("file", file);

    // The same token the form carries. base.html puts it on <body> for HTMX,
    // and the route accepts it as a header (R-SEC-11).
    var token = "";
    var holder = document.querySelector("[hx-headers]");
    if (holder) {
      try {
        token = JSON.parse(holder.getAttribute("hx-headers"))["X-CSRF-Token"];
      } catch (e) {
        token = "";
      }
    }

    fetch("/receipts", {
      method: "POST",
      body: body,
      headers: token ? { "X-CSRF-Token": token } : {},
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          // The server's wording, not ours: it knows why it refused.
          setStatus(row, result.data.error || "That file was not accepted.", true);
          input.value = "";
          return;
        }
        if (hidden) hidden.value = result.data.uri;
        var note = result.data.converted ? " (converted to JPEG)" : "";
        setStatus(row, "Attached: " + result.data.name + note, false);
      })
      .catch(function () {
        setStatus(row, "That upload did not go through. Try again.", true);
        input.value = "";
      });
  }

  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('input[type="file"][data-receipt]')) return;
    var row = input.closest(".item");
    if (row) upload(row, input);
  });
})();

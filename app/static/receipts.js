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

  // The running total, for feedback only. The server recomputes every figure
  // on POST from integer pence; this never decides what anybody is paid.
  function retotal() {
    var el = document.getElementById("running-total");
    if (!el) return;
    var pence = parseInt(el.getAttribute("data-base") || "0", 10);
    document.querySelectorAll(".item .amount").forEach(function (input) {
      var value = parseFloat((input.value || "").replace(/[^0-9.-]/g, ""));
      if (!isNaN(value) && value > 0) pence += Math.round(value * 100);
    });
    el.textContent =
      "£" + (pence / 100).toLocaleString("en-GB", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
  }

  document.addEventListener("input", function (event) {
    if (event.target.matches && event.target.matches(".item .amount")) retotal();
  });

  // Another expense row. The template draws three; the server has always
  // taken up to MAX_ITEMS, which the button carries so the two cannot
  // disagree. Cloned from the last row rather than built here, so the row's
  // markup lives in one place — the Jinja partial.
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("#add-row");
    if (!button) return;
    var list = document.getElementById("items");
    var rows = list.querySelectorAll(".item");
    var max = parseInt(button.getAttribute("data-max") || "10", 10);
    if (rows.length >= max) return;

    var copy = rows[rows.length - 1].cloneNode(true);
    copy.querySelectorAll("input").forEach(function (input) {
      input.value = "";
    });
    var status = copy.querySelector(".receipt-status");
    if (status) {
      status.textContent = "";
      status.classList.remove("missing");
    }
    list.appendChild(copy);
    var first = copy.querySelector(".desc");
    if (first) first.focus();

    if (list.querySelectorAll(".item").length >= max) {
      button.disabled = true;
      button.textContent = "That is the most we can attach";
    }
  });
})();

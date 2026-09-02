Assets served from this origin.

The Content-Security-Policy in `app/main.py` allows scripts and styles from
`'self'` only, so HTMX must be vendored here rather than loaded from a CDN: a
page that shows fees and account numbers should not put a third party in its
request path.

`htmx.min.js` is not committed yet. Add it before building the wizard
(R-WIZ-01..09).

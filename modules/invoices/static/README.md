Assets served from this origin.

The Content-Security-Policy in `app/main.py` allows scripts and styles from
`'self'` only, so HTMX is vendored here rather than loaded from a CDN: a page
that shows fees and account numbers should not put a third party in its request
path.

| File | Version | SHA-256 |
|---|---|---|
| `htmx.min.js` | 2.0.4 | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |

Fetched from `https://cdnjs.cloudflare.com/ajax/libs/htmx/2.0.4/htmx.min.js`.
Re-record the hash here whenever it is upgraded.

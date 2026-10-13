# app/web

The HTTP layer: FastAPI routers that render Jinja templates. Routes hold no
business rules; they read the verified identity, call a service and render.

| File | What it is |
|---|---|
| `admin.py` | Admin routes (R-ADM-01 .. R-ADM-09) |
| `csrf.py` | CSRF protection for every state-changing form (R-SEC-11) |
| `member.py` | The member wizard (R-WIZ-01 .. R-WIZ-09) |
| `ratelimit.py` | Per-person rate limiting for the two endpoints that matter (R-SEC-08) |
| `static.py` | Serving the stylesheet, the scripts and the fonts |
| `tasks.py` | Scheduler endpoints (R-JOB-01 .. R-JOB-06) |
| `templates.py` | The Jinja environment |
| `theme.py` | Day or night, decided on the server |
| `tutorial.py` | The on-screen tutorial's copy (design handoff, "The tutorial") |

- **Identity** comes only from the Cloud IAP assertion (`app/auth.py`). No route
  takes a person from a query parameter, form field or cookie.
- **Finance routes** additionally require the caller to be in the finance group.
- **Scheduler routes** (`/tasks/*`) verify an OIDC token from one named service
  account, and are switched off until that is configured.
- Every state-changing form carries a CSRF token.

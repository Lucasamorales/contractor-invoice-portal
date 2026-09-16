# Production. Committed, so a change to who can get in or what is deployed is
# reviewed like code. It holds no secret: those are in Secret Manager and only
# named here.
#
# Applied only in the production workspace: infra/versions.tf refuses a plan
# whose environment does not match the selected workspace.

project_id  = "your-gcp-project"
environment = "production"
region      = "europe-west2"

# The digest that was tested on staging, never a tag. Set at release time to
# the image staging is running. 0.8.18, run on staging 16 September 2026.
image = "europe-west2-docker.pkg.dev/your-gcp-project/portal/invoice-portal@sha256:6a5b5ff394df6866eded555753420722d7b1c98b3fbf8318f80ac98d0be4287b"

# CLAUDE.md 2.1: the only people who may see a fee. Decided 16 September 2026:
# the accounts mailbox, Charlie and Remy. Julian does finance work signed in
# as accounts@; his own address is an ordinary contractor account.
finance_emails = [
  "accounts@example.com",
  "charlie@example.com",
  "remy@example.com",
]

# Every invoice email and the monthly summary (R-SUB-07, R-JOB-03).
finance_inbox = "accounts@example.com"

# R-KUD-20: receive kudos, may not cash them or win a prize. Decided
# 16 September 2026: Remy, Kit, Charlie, Riley and Toni Grant. Riley and
# Toni do not invoice here and have no account; the kudos name file already
# ignores their kudos, so only the three who sign in need listing.
kudos_ineligible_emails = [
  "remy@example.com",
  "remy.keller@example.com",
  "charlie@example.com",
]

# Who may sign in at all: everyone on the Workspace domain (decided
# 16 September 2026). Somebody who does not invoice gets an empty profile and
# cannot send anything without a fee; /admin is gated separately by
# finance_emails.
iap_members = [
  "domain:example.com",
]

# The window follows the real dates in production (R-PER-06). The app refuses
# to start with this on.
window_always_open = false

# GO-LIVE SWITCH. While false, the window-open email (the 22nd), the reminder
# (the 27th) and the summary to accounts (the 29th) are paused. Set it to true
# and apply once the fees are in and the dry run has passed; the next 22nd then
# emails every active contractor.
reminders_live = false

# The kudos sheet (R-KUD-01), read daily. Shared read-only with the production
# service account, portal-invoices-production@your-gcp-project.iam.gserviceaccount.com.
kudos_sheet_id = "YOUR_KUDOS_SHEET_ID"

# Sent from the accounts mailbox, so replies land where finance already works.
# Needs example.com verified in Resend (SPF, DKIM).
mail_from = "Invoices <accounts@example.com>"

# Never set in production: the app refuses to start with it (R-ENV-03).
mail_redirect_to = ""

# The same Resend key staging uses, decided 16 September 2026: one account,
# one key. The secret keeps its staging name; production's service account is
# granted read access to it alongside staging's. Created by hand, never by
# Terraform, so the key is never in state (R-SEC-02).
resend_secret_id = "portal-resend-api-key-staging"

# Cloud Run's deterministic address for the service: name, project number,
# region. Known before the service exists, which the other address is not.
app_url = "https://portal-invoices-production-000000000000.europe-west2.run.app"

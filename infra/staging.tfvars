# Staging. Committed since 16 September 2026, so a change to who can get in
# or what is deployed is reviewed like code. It holds no secret: those are
# in Secret Manager and only named here.

project_id  = "your-gcp-project"
environment = "staging"
region      = "europe-west2"

# Pinned by digest, not by tag. A tag can be moved; a digest cannot, so the
# thing that gets deployed is exactly the thing that was tested.
# 0.8.20, built and pushed 22 September 2026.
image = "europe-west2-docker.pkg.dev/your-gcp-project/portal/invoice-portal@sha256:REPLACE_WITH_THE_DIGEST_OF_THE_IMAGE_YOU_PUSHED"

# CLAUDE.md 2.1: the only people who may ever see a fee.
# Leo is here for staging only, so he can reach /admin and set fees while
# testing. Production keeps the real three and nobody else.
finance_emails = [
  "leo@example.com", # staging only, for testing
  # julian@ (Robin) is left out on staging, 14 September 2026, so he can
  # test the contractor side as a plain user. He is finance in production.
  "charlie@example.com",  # Charlie
  "remy@example.com",  # Remy
  "accounts@example.com", # accounts, added 14 September 2026 for testing
]

# Where invoices and the monthly summary go. On staging every email is
# redirected (mail_redirect_to below), so this only decides what the redirect
# says the email was for.
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

# Who may get through the login at all.
# Staging is developers only (R-ENV-04) - do not put the whole team here.
iap_members = [
  "user:leo@example.com",
  # Added 7 September 2026 so Remy can test the overhaul. He is already in
  # finance_emails, so he lands on the admin area as well as the wizard — the
  # fees he can see there are the invented ones from seed_demo (R-ENV-02).
  "user:remy@example.com",
  # Added 14 September 2026 so accounts can test the admin side.
  "user:accounts@example.com",
  # Added 14 September 2026 to test as a plain user, not an admin.
  "user:julian@example.com",
]

# So the wizard can be tested on any day, not only the 22nd to the 28th.
# Production refuses to start with this set (R-PER-06).
window_always_open = true

# The kudos sheet (R-KUD-01). Deliberately EMPTY in staging.
#
# The read works - proved on 5 September, HTTP 200 from the values API with the
# service account's own token. But leaving it set means the five-minutely sync
# pulls the live log into staging, where it mixes with the invented fixture and
# the figures stop meaning anything (R-ENV-02).
#
# Staging runs on app/scripts/seed_demo_kudos.py instead, which drives the same
# sync code with invented rows. Production sets this for real:
#   YOUR_KUDOS_SHEET_ID
kudos_sheet_id = ""

# Mail (R-SUB-06). Until the sending domain has SPF, DKIM and DMARC, Resend
# will only deliver from its own onboarding sender, and only to the address
# that owns the account.
mail_from = "onboarding@resend.dev"

# Every email goes here instead of its recipients, with the intended ones
# written into the body. This is also what allows staging to send at all:
# R-ENV-03 says staging must not reach a real contractor, and a redirect makes
# that impossible rather than merely unlikely.
mail_redirect_to = "leo@example.com"

resend_secret_id = "portal-resend-api-key-staging"

app_url = "https://invoices-staging-xxxx.a.run.app"

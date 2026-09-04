# Access to the secrets the service reads (R-SEC-02).
#
# The secrets and their values already exist — they were created by hand and
# are recorded in docs/setup.md §1.4.1. Terraform grants access to them rather
# than managing their contents: a connection string and an encryption key
# should not sit in a state file.

data "google_secret_manager_secret" "database_url" {
  project   = var.project_id
  secret_id = "portal-database-url-${var.environment}"
}

data "google_secret_manager_secret" "bank_encryption_key" {
  project   = var.project_id
  secret_id = "portal-bank-encryption-key-${var.environment}"
}

# Signs the CSRF token on every form (R-SEC-11). Separate from the bank key: a
# token signer and a payload cipher should not share a secret.
data "google_secret_manager_secret" "csrf_secret" {
  project   = var.project_id
  secret_id = "portal-csrf-secret-${var.environment}"
}

# The Resend API key (R-SUB-06). Optional: staging can run without mail, and
# the service must still start when it is absent, so this is looked up only
# when a name is given.
data "google_secret_manager_secret" "resend_api_key" {
  count     = var.resend_secret_id == "" ? 0 : 1
  project   = var.project_id
  secret_id = var.resend_secret_id
}

resource "google_secret_manager_secret_iam_member" "resend_api_key" {
  count     = var.resend_secret_id == "" ? 0 : 1
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.resend_api_key[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

# Granted per secret, never project-wide. The staging service must not be able
# to read the production database's credentials (R-ENV-01, R-ENV-04).
resource "google_secret_manager_secret_iam_member" "database_url" {
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "bank_encryption_key" {
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.bank_encryption_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "csrf_secret" {
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.csrf_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

output "service_account_email" {
  description = "Set as PORTAL_SERVICE_ACCOUNT_EMAIL so signed URLs can be signed."
  value       = google_service_account.app.email
}

output "bucket_name" {
  description = "Set as PORTAL_GCS_BUCKET."
  value       = google_storage_bucket.files.name
}

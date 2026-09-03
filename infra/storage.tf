# The private bucket (R-STO-01 .. R-STO-03).
#
# It holds invoice PDFs, expense receipts and database backups. Every one of
# those carries either a fee or a full account number, so nothing here is
# public and nothing here is reachable without a signed URL.

resource "google_storage_bucket" "files" {
  name     = "portal-invoices-${var.environment}"
  project  = var.project_id
  location = var.region

  # R-STO-01: uniform access. Per-object ACLs are how a file ends up public by
  # accident, and this bucket cannot afford that.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # R-STO-01: versioning on. An overwritten invoice is recoverable.
  versioning {
    enabled = true
  }

  # R-JOB-05 prunes backups itself, by name and date. This is the safety net
  # for anything the job misses — a run that failed part way, say.
  lifecycle_rule {
    condition {
      age            = var.backup_retention_days + 7
      matches_prefix = ["backups/"]
    }
    action {
      type = "Delete"
    }
  }

  # Old versions of a replaced object do not need keeping indefinitely.
  lifecycle_rule {
    condition {
      num_newer_versions = 3
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    environment = var.environment
    contains    = "personal-data"
  }
}

# The service the application runs as. Cloud Run attaches this identity;
# there are no downloaded key files anywhere in this system (CLAUDE.md §2.7).
resource "google_service_account" "app" {
  account_id   = "portal-invoices-${var.environment}"
  display_name = "invoice portal (${var.environment})"
  project      = var.project_id
}

# Read and write its own objects, and nothing else. Not admin: the application
# has no business changing the bucket's configuration.
resource "google_storage_bucket_iam_member" "app_objects" {
  bucket = google_storage_bucket.files.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

# Signing a V4 URL without a key file means asking the IAM API to sign as
# itself (R-STO-03, R-SEC-01).
resource "google_service_account_iam_member" "app_can_sign" {
  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app.email}"
}

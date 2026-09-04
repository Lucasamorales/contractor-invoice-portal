# Cloud Scheduler (R-JOB-01 .. R-JOB-06).
#
# Each job calls its endpoint with an OIDC token. The app verifies the token's
# audience and that this service account signed it.
#
# These endpoints do NOT sit outside IAP: IAP is enabled on the service, so it
# is in front of every path including /tasks/*. The scheduler therefore needs
# to be allowed through IAP as well (see scheduler_iap below), and the OIDC
# token is checked twice — once by IAP, once by the app.

resource "google_service_account" "scheduler" {
  account_id   = "portal-scheduler-${var.environment}"
  display_name = "Invoice scheduler (${var.environment})"
  project      = var.project_id
}

# It may call the service, and nothing else. It is not the app's identity: a
# compromised scheduler must not be able to read the bank columns.
resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# IAP is in front of the whole service, so there is no "outside IAP" for these
# endpoints to sit in — the comment at the top of this file used to claim there
# was. Without this the scheduler's OIDC token is refused by IAP before the
# container ever sees it, and the job fails as UNAUTHENTICATED with nothing in
# the application log at all, which is a genuinely horrible way to be broken.
#
# run.invoker alone is not enough, exactly as it was not enough for a human:
# that one lets the request reach Cloud Run, this one is what IAP itself
# checks. The token is still verified a second time by the app against
# PORTAL_SCHEDULER_AUDIENCE and this service account, so IAP is an added gate
# rather than the only one.
resource "google_iap_web_cloud_run_service_iam_member" "scheduler_iap" {
  project                = var.project_id
  location               = google_cloud_run_v2_service.app.location
  cloud_run_service_name = google_cloud_run_v2_service.app.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "serviceAccount:${google_service_account.scheduler.email}"
}

locals {
  service_url = google_cloud_run_v2_service.app.uri

  # All times Europe/London: the window is a question about what day it is
  # where the people are (R-PER-01).
  jobs = {
    outbox = {
      # R-JOB-04. Every five minutes; a queued invoice should not wait long.
      schedule    = "*/5 * * * *"
      path        = "/tasks/outbox"
      description = "Send queued mail with backoff"
    }
    sync-kudos = {
      # R-JOB-01. Daily through the window.
      schedule    = "0 7 20-28 * *"
      path        = "/tasks/sync-kudos"
      description = "Read the kudos sheet"
    }
    remind = {
      # R-JOB-02. The endpoint decides which of the two emails the day calls
      # for, so one job covers the 24th and the 27th.
      schedule    = "0 9 24,27 * *"
      path        = "/tasks/remind"
      description = "Window open on the 24th, nudge the stragglers on the 27th"
    }
    summary = {
      # R-JOB-03. The 29th, after the window has closed.
      schedule    = "0 9 29 * *"
      path        = "/tasks/summary"
      description = "Reconciliation to the finance group"
    }
    backup = {
      # R-JOB-05. Nightly, off-peak.
      schedule    = "0 2 * * *"
      path        = "/tasks/backup"
      description = "pg_dump to GCS, thirty days retained"
    }
    drive-mirror = {
      # R-JOB-06. Through the window and just after it.
      schedule    = "30 * 24-29 * *"
      path        = "/tasks/drive-mirror"
      description = "Copy submitted PDFs to the Drive folder"
    }
  }
}

resource "google_cloud_scheduler_job" "jobs" {
  for_each = local.jobs

  name        = "portal-${each.key}-${var.environment}"
  project     = var.project_id
  region      = var.region
  description = each.value.description
  schedule    = each.value.schedule
  time_zone   = "Europe/London"

  # Every job is idempotent, so a retry is safe (R-JOB-*).
  retry_config {
    retry_count          = 3
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
  }

  attempt_deadline = "300s"

  http_target {
    http_method = "POST"
    uri         = "${local.service_url}${each.value.path}"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      # The IAP resource path, not the service URL. IAP is in front of these
      # endpoints, and it validates this token itself before Cloud Run sees
      # it — against the same audience it uses for a browser. With the service
      # URL here IAP does not recognise the token as programmatic access at
      # all: it answers with its own sign-in page, so Scheduler records a
      # cheerful HTTP 200 and the container is never invoked.
      #
      # Must match PORTAL_SCHEDULER_AUDIENCE, which is set from the same local.
      audience = local.iap_audience
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.scheduler_invoker]
}

output "scheduler_service_account" {
  description = "Set as PORTAL_SCHEDULER_SERVICE_ACCOUNT."
  value       = google_service_account.scheduler.email
}

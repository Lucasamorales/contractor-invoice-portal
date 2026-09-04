# Cloud Scheduler (R-JOB-01 .. R-JOB-06).
#
# Each job calls its endpoint with an OIDC token. The app verifies the token's
# audience and that this service account signed it.
#
# The jobs run against their own Cloud Run service (tasks.tf), not the portal:
# IAP is in front of every path the portal serves and has no way to let a
# service account through to an endpoint rather than a browser. See the note at
# the top of tasks.tf for what was tried first.

resource "google_service_account" "scheduler" {
  account_id   = "portal-scheduler-${var.environment}"
  display_name = "Invoice scheduler (${var.environment})"
  project      = var.project_id
}

# The scheduler's only grant is on the tasks service (tasks.tf). It has no
# access to the portal at all: a compromised scheduler must not be able to
# reach the wizard or the admin screens.

locals {
  service_url = google_cloud_run_v2_service.tasks.uri

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
      # The tasks service's own URL, which is also what that service checks
      # against in PORTAL_SCHEDULER_AUDIENCE. Both come from the same local, so
      # they cannot drift apart.
      audience = local.tasks_audience
    }
  }

  depends_on = [google_cloud_run_v2_service_iam_member.tasks_invoker]
}

output "scheduler_service_account" {
  description = "Set as PORTAL_SCHEDULER_SERVICE_ACCOUNT."
  value       = google_service_account.scheduler.email
}

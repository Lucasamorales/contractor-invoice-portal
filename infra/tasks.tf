# The service the scheduled jobs run on (R-JOB-01 .. R-JOB-06).
#
# ---------------------------------------------------------------------------
# Why there are two services.
#
# `scheduler.tf` used to say the task endpoints "sit outside IAP". They did
# not: iap_enabled is set on the portal, and IAP is in front of every path it
# serves. So every scheduled job failed UNAUTHENTICATED, the container was
# never invoked, and there was nothing in the application log to notice —
# which is how it went unseen until somebody watched a job land.
#
# Granting the scheduler roles/iap.httpsResourceAccessor and minting its OIDC
# token for the IAP audience got as far as a cheerful HTTP 200 that the
# container still never saw: IAP answering with its own sign-in page. IAP is
# built to put a human through a browser flow, and a service account calling an
# endpoint is not that.
#
# So the jobs get their own service, from the same image, with no IAP. What
# keeps it private is Cloud Run IAM — only the scheduler's identity may invoke
# it — and the app verifies the OIDC token a second time on every route
# (audience and signer). PORTAL_TASKS_ONLY makes it answer 404 to everything that
# is not a task, so even a loosened IAM would not expose the wizard.
#
# It scales to zero and shares the portal's database and bucket, so it costs
# nothing beyond the seconds it runs (R-ENV-06).
# ---------------------------------------------------------------------------

locals {
  tasks_name = "portal-tasks-${var.environment}"

  # Deterministic, so it can be both the OIDC audience the scheduler mints and
  # the audience the app checks, without one being fed back from the other.
  tasks_audience = join("", [
    "https://", local.tasks_name, "-", data.google_project.this.number,
    ".", var.region, ".run.app",
  ])
}

resource "google_cloud_run_v2_service" "tasks" {
  name     = local.tasks_name
  project  = var.project_id
  location = var.region

  # Billing reads these. Staging and production share one project, so the
  # per-environment budgets in infra/billing tell their costs apart by label.
  labels = {
    app         = "invoice-portal"
    environment = var.environment
  }

  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.environment == "production"

  template {
    # The same identity as the portal: these jobs read the same database, the
    # same bucket and the same secrets. A second identity would be a second
    # set of grants to keep in step for no gain.
    service_account = google_service_account.app.email

    scaling {
      min_instance_count = 0
      max_instance_count = 1 # One job at a time. Every one of them is idempotent.
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      # The whole point of this service.
      env {
        name  = "PORTAL_TASKS_ONLY"
        value = "true"
      }

      env {
        name  = "PORTAL_ENVIRONMENT"
        value = var.environment
      }

      env {
        name  = "PORTAL_GCS_BUCKET"
        value = google_storage_bucket.files.name
      }

      env {
        name  = "PORTAL_SERVICE_ACCOUNT_EMAIL"
        value = google_service_account.app.email
      }

      env {
        name  = "PORTAL_FINANCE_EMAILS"
        value = join(",", var.finance_emails)
      }

      env {
        name  = "PORTAL_KUDOS_SHEET_ID"
        value = var.kudos_sheet_id
      }

      env {
        name  = "PORTAL_MAIL_FROM"
        value = var.mail_from
      }

      env {
        name  = "PORTAL_MAIL_REDIRECT_TO"
        value = var.mail_redirect_to
      }

      env {
        name  = "PORTAL_SCHEDULER_SERVICE_ACCOUNT"
        value = google_service_account.scheduler.email
      }

      # This service's own URL. The scheduler mints tokens for it and the app
      # checks them against it, so the two cannot drift.
      env {
        name  = "PORTAL_SCHEDULER_AUDIENCE"
        value = local.tasks_audience
      }

      # No IAP here, so no IAP audience. app/auth.py only needs it for the
      # browser routes, and this service does not serve them.

      env {
        name = "PORTAL_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "PORTAL_BANK_ENCRYPTION_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.bank_encryption_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "PORTAL_CSRF_SECRET"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.csrf_secret.secret_id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = var.resend_secret_id == "" ? [] : [1]
        content {
          name = "PORTAL_RESEND_API_KEY"
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.resend_api_key[0].secret_id
              version = "latest"
            }
          }
        }
      }

      resources {
        limits = {
          # The backup streams a pg_dump and the outbox holds a PDF and its
          # receipts in memory while it sends. Same ceiling as the portal.
          memory = "512Mi"
          cpu    = "1"
        }
        # CPU only while a request is being handled, so an idle instance
        # costs nothing. The provider defaults to that only when no resources
        # block is set; setting memory above silently switched both services to
        # CPU always allocated, and the scheduler waking the tasks service every
        # five minutes kept one instance billed around the clock (about
        # GBP 1.50 a day). Safe because nothing here works after its response:
        # every job, sending mail included, finishes inside the request.
        cpu_idle = true
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 5
        timeout_seconds       = 3
        failure_threshold     = 6
      }
    }

    # A backup or a Drive mirror is slower than a page load.
    timeout = "540s"
  }

  depends_on = [
    google_secret_manager_secret_iam_member.database_url,
    google_secret_manager_secret_iam_member.bank_encryption_key,
    google_secret_manager_secret_iam_member.csrf_secret,
  ]
}

# The only thing that may invoke it. No allUsers, and no IAP: an
# unauthenticated request is refused by Cloud Run before the container starts.
resource "google_cloud_run_v2_service_iam_member" "tasks_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.tasks.location
  name     = google_cloud_run_v2_service.tasks.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

output "tasks_url" {
  description = "Where the scheduled jobs run. Not reachable by a person."
  value       = google_cloud_run_v2_service.tasks.uri
}

# The service, and the IAP in front of it (R-ACC-01, R-ENV-05).
#
# ---------------------------------------------------------------------------
# A cost decision is baked in here, and it is the big one for R-ENV-06.
#
# IAP in front of Cloud Run can be done two ways:
#
#   1. IAP directly on the Cloud Run service. No load balancer. Free.
#   2. A global external HTTPS load balancer with a serverless NEG, with IAP
#      on the backend service. This is the older, more widely documented
#      arrangement, and the forwarding rule alone is roughly £15-18 a month
#      whether or not anybody uses it.
#
# This file uses (1). Sixteen people invoicing once a month do not need a load
# balancer, and (2) would miss the £0 target on its own, before a single
# invoice is stored.
#
# The trade is that (1) gives you *.run.app rather than a custom domain. If
# invoices.example.com matters more than the money, that is a
# conversation to have deliberately, not a default to drift into.
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "app" {
  name     = "portal-invoices-${var.environment}"
  project  = var.project_id
  location = var.region

  # Only IAP may reach it. Not the internet, and not other services.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  # IAP terminates authentication; the app writes no login code (R-ACC-01).
  iap_enabled = true

  deletion_protection = var.environment == "production"

  template {
    service_account = google_service_account.app.email

    # Scale to zero. Sixteen invoices a month is not a warm-instance workload.
    scaling {
      min_instance_count = 0
      max_instance_count = var.environment == "production" ? 4 : 2
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      env {
        name  = "PORTAL_ENVIRONMENT"
        value = var.environment
      }

      env {
        name  = "PORTAL_GCS_BUCKET"
        value = google_storage_bucket.files.name
      }

      # Needed to sign V4 URLs through the IAM API (R-STO-03).
      env {
        name  = "PORTAL_SERVICE_ACCOUNT_EMAIL"
        value = google_service_account.app.email
      }

      env {
        name  = "PORTAL_WORKSPACE_DOMAIN"
        value = "example.com"
      }

      # R-ACC-04: the app checks finance membership itself as well as IAP.
      env {
        name  = "PORTAL_FINANCE_EMAILS"
        value = join(",", var.finance_emails)
      }

      env {
        name  = "PORTAL_SCHEDULER_SERVICE_ACCOUNT"
        value = google_service_account.scheduler.email
      }

      # Secrets are referenced, never inlined (R-SEC-02). Their values are not
      # in this file and not in the state.
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

      resources {
        limits = {
          # WeasyPrint is the memory-hungry part; 512Mi renders an invoice
          # comfortably and 256Mi does not.
          memory = "512Mi"
          cpu    = "1"
        }
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

    # A cold start has to wake Neon as well as the container.
    timeout = "60s"
  }

  depends_on = [
    google_secret_manager_secret_iam_member.database_url,
    google_secret_manager_secret_iam_member.bank_encryption_key,
  ]
}

# Who may get through IAP (R-ACC-01, R-ENV-04).
#
# Everyone on the Workspace domain: all sixteen invoice, including Robin, who
# uses the member wizard for his own invoice like everybody else (R-ACC-08).
# /admin is gated separately, by the app, against PORTAL_FINANCE_EMAILS.
resource "google_cloud_run_v2_service_iam_member" "iap_access" {
  for_each = toset(var.iap_members)

  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = each.value
}

output "service_url" {
  description = <<-EOT
    Set this as PORTAL_IAP_AUDIENCE and PORTAL_SCHEDULER_AUDIENCE.

    The IAP audience is actually of the form
    /projects/<number>/global/backendServices/<id> when IAP runs behind a load
    balancer. With IAP directly on Cloud Run it is the service URL. Check the
    assertion's `aud` claim once on first login rather than trusting this
    comment — app/auth.py verifies the audience, so a wrong value fails closed.
  EOT
  value       = google_cloud_run_v2_service.app.uri
}

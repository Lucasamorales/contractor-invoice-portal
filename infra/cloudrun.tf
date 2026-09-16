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

locals {
  service_name = "portal-invoices-${var.environment}"

  # Cloud Run gives a service two URLs. This one is deterministic — name,
  # project number, region — so it can be computed before the service exists.
  # The other, with a random hash, cannot, and referencing it from the
  # service's own environment is a dependency cycle.
  service_audience = join("", [
    "https://", local.service_name, "-", data.google_project.this.number,
    ".", var.region, ".run.app",
  ])

  # What IAP actually signs into the assertion's aud claim.
  iap_audience = var.iap_audience != "" ? var.iap_audience : join("", [
    "/projects/", data.google_project.this.number,
    "/locations/", var.region,
    "/services/", local.service_name,
  ])
}

resource "google_cloud_run_v2_service" "app" {
  name     = local.service_name
  project  = var.project_id
  location = var.region

  # Requests arrive through IAP's own frontend, so the ingress setting must
  # allow them. Restricting to internal-load-balancer would be right if IAP sat
  # behind one; with IAP directly on the service there is no load balancer, and
  # that setting would make the service unreachable rather than secure.
  #
  # What actually keeps people out is iap_enabled plus the IAM below: an
  # unauthenticated request never reaches the container.
  ingress = "INGRESS_TRAFFIC_ALL"

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

      # R-KUD-01. Not a secret — it is in the URL of a sheet the whole company
      # can open. What keeps us to this one file is that the sheet is shared
      # with the service account and nothing else is; there is no OAuth scope
      # that means "one spreadsheet".
      env {
        name  = "PORTAL_KUDOS_SHEET_ID"
        value = var.kudos_sheet_id
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

      # The audience IAP puts in the assertion is a resource path, not the
      # service URL — confirmed by reading it off a real rejected assertion:
      #   /projects/{number}/locations/{region}/services/{name}
      #
      # Every part of that is known before the service exists, so it is
      # computed rather than fed back in after the first apply. var.iap_audience
      # overrides it if Google ever changes the shape.
      env {
        name  = "PORTAL_IAP_AUDIENCE"
        value = local.iap_audience
      }

      # The portal does not serve /tasks/*; the jobs run on their own service
      # (tasks.tf). Left set so that if a task route were ever added here by
      # mistake it would refuse rather than run unauthenticated.
      env {
        name  = "PORTAL_SCHEDULER_AUDIENCE"
        value = local.tasks_audience
      }

      env {
        name  = "PORTAL_MAIL_FROM"
        value = var.mail_from
      }

      # Every email links back to the portal. Its own URL is not known to the
      # service at runtime, and hardcoding one would send staging's mail to
      # production, so Terraform passes the address it just created.
      env {
        name  = "PORTAL_APP_URL"
        value = var.app_url
      }

      # Every email goes to one address instead of its recipients. This is
      # what lets staging send at all: R-ENV-03 says staging must not reach a
      # real contractor, and a redirect makes that impossible rather than
      # merely unlikely. Production refuses to start with it set.
      env {
        name  = "PORTAL_MAIL_REDIRECT_TO"
        value = var.mail_redirect_to
      }

      # R-PER-06 shuts submission before the 22nd, which makes the thing
      # untestable for three weeks in four. Staging can force it open;
      # assert_production_ready refuses to let production start with it, so
      # this cannot cross over even if someone sets the variable.
      env {
        name  = "PORTAL_WINDOW_ALWAYS_OPEN"
        value = var.window_always_open ? "true" : "false"
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

      # Without this every form is refused, so the service is unusable rather
      # than insecure if it is missing (R-SEC-11).
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
          # WeasyPrint is the memory-hungry part; 512Mi renders an invoice
          # comfortably and 256Mi does not.
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

    # A cold start has to wake Neon as well as the container.
    timeout = "60s"
  }

  # The service must not start before it can read what it needs.
  depends_on = [
    google_secret_manager_secret_iam_member.database_url,
    google_secret_manager_secret_iam_member.bank_encryption_key,
    google_secret_manager_secret_iam_member.csrf_secret,
  ]
}

# IAP forwards requests as its own service agent, so that agent needs to be
# allowed to invoke the service. Without this every request is refused after
# the person has already signed in, which is a confusing way to be broken.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_cloud_run_v2_service_iam_member" "iap_service_agent" {
  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member = join("", [
    "serviceAccount:service-",
    data.google_project.this.number,
    "@gcp-sa-iap.iam.gserviceaccount.com",
  ])
}

# Who may get through IAP (R-ACC-01, R-ENV-04).
#
# Everyone on the Workspace domain: all sixteen invoice, including Robin, who
# uses the member wizard for his own invoice like everybody else (R-ACC-08).
# /admin is gated separately, by the app, against PORTAL_FINANCE_EMAILS.
#
# Two roles, and both are needed. They are not redundant:
#
#   run.invoker             lets the request reach the container at all.
#   iap.httpsResourceAccessor  is what IAP itself checks for the human.
#
# With only the first, IAP refuses before the app ever sees the request and the
# person gets "You don't have access" with no clue why. That is how this failed
# the first time.
resource "google_cloud_run_v2_service_iam_member" "iap_access" {
  for_each = toset(var.iap_members)

  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = each.value
}

# Granted on the IAP resource, not on the Cloud Run service: the run service
# does not accept iap.httpsResourceAccessor at all, and trying it there gets a
# flat "Role is not supported for this resource".
resource "google_iap_web_cloud_run_service_iam_member" "iap_web_access" {
  for_each = toset(var.iap_members)

  project                = var.project_id
  location               = google_cloud_run_v2_service.app.location
  cloud_run_service_name = google_cloud_run_v2_service.app.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
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

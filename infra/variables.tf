variable "project_id" {
  description = "The GCP project. Today: your-gcp-project."
  type        = string
}

variable "environment" {
  description = "staging or production. Everything is named and scoped by it (R-ENV-01)."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Only staging and production exist (R-ENV-01)."
  }
}

variable "region" {
  description = <<-EOT
    The region for the bucket and the service.

    EU by default. The PDFs carry every person's full account number and legal
    name, so keeping them in the EU is the point, not the default.

    Note this is NOT in the GCS always-free tier, which covers US regions only
    (R-ENV-06). At the volumes here it is pennies a month — roughly 16 invoices
    of about 20KB plus thirty compressed backups of about 3KB — but it is not
    zero, and R-ENV-06 says anything that costs must be raised rather than
    silently provisioned.
  EOT
  type        = string
  default     = "europe-west2" # London
}

variable "backup_retention_days" {
  description = "R-JOB-05 retains thirty days."
  type        = number
  default     = 30
}

variable "image" {
  description = <<-EOT
    The container image to run, e.g.
    europe-west2-docker.pkg.dev/<project>/portal/invoice-portal:<tag>

    Pinned to a tag or digest, never :latest — a service that redeploys itself
    because someone pushed is not one you can reason about.
  EOT
  type        = string
}

variable "finance_emails" {
  description = <<-EOT
    Robin, Charlie and Remy. The only people who may see fees (CLAUDE.md §2.1).

    Checked by the application as well as by IAP (R-ACC-04), which is why it
    is passed in as configuration rather than only granted as an IAM role.
  EOT
  type        = list(string)
  default     = []
}

variable "iap_members" {
  description = <<-EOT
    Who may get through IAP, in IAM member form, e.g.
    ["domain:example.com"] or a list of "user:someone@..." entries.

    Everyone who invoices needs to be here, including Robin: he uses the member
    wizard for his own invoice like everybody else (R-ACC-08). /admin is gated
    separately by the app.

    For staging this should be the developers, and it must not include the
    production finance group (R-ENV-04).
  EOT
  type        = list(string)
  default     = []
}

variable "window_always_open" {
  description = <<-EOT
    Staging only. Forces the submission window open so the wizard can be tested
    on any day of the month rather than only the 24th to the 28th.

    The application refuses to start in production with this set, so a stray
    true here fails loudly rather than quietly letting somebody invoice for the
    wrong month.
  EOT
  type        = bool
  default     = false

  validation {
    condition     = !(var.window_always_open && var.environment == "production")
    error_message = "window_always_open is staging only (R-PER-06)."
  }
}

variable "iap_audience" {
  description = <<-EOT
    Override for the audience IAP puts in the assertion. Normally empty:
    it is computed as /projects/{number}/locations/{region}/services/{name},
    which is what a real assertion was observed to carry.

    Set this only if Google changes the shape. A wrong value refuses every
    login rather than letting anyone in, and app/auth.py logs the audience it
    actually received so the right value is one log line away.
  EOT
  type        = string
  default     = ""
}

variable "kudos_sheet_id" {
  description = <<-EOT
    The kudos spreadsheet's file id, from its URL between /d/ and /edit.

    Share that sheet read-only with the service account in service_account_email
    before setting this, and leave sheets.googleapis.com enabled: the sync
    needs the file id, the sharing and the API, and a 403 mentions none of them.
  EOT
  type        = string
  default     = ""
}

variable "resend_secret_id" {
  description = <<-EOT
    Secret Manager secret holding the Resend API key. Empty means no mail.

    Create it and add the key yourself; Terraform grants access to it and never
    reads it, so the key does not end up in the state file (R-SEC-02).
  EOT
  type        = string
  default     = ""
}

variable "mail_from" {
  description = <<-EOT
    The From address. Until the sending domain has SPF, DKIM and DMARC this
    must be Resend's own onboarding sender, which can only deliver to the
    account owner's address.
  EOT
  type        = string
  default     = ""
}

variable "mail_redirect_to" {
  description = <<-EOT
    Send every email here instead of to its real recipients, with the intended
    ones written into the body. For proving delivery before DNS exists.

    Production refuses to start with this set (app/config.py).
  EOT
  type        = string
  default     = ""
}

# Where the portal answers, so an email can link to the thing it is about.
# Cloud Run generates the host, so this cannot be derived here without a
# cycle: set it from the service_url output after the first apply. Production
# refuses to start without it (assert_production_ready).
variable "app_url" {
  description = "Public URL of the portal, e.g. https://portal-invoices-staging-xxxx.a.run.app"
  type        = string
  default     = ""
}

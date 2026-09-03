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

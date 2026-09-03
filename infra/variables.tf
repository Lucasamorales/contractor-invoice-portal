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

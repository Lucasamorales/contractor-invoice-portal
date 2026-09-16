# What both environments share: the image registry and the budget alerts.
# R-ENV-05: infrastructure in Terraform, these included.

terraform {
  required_version = ">= 1.9"

  # State in a versioned bucket rather than on one laptop, where losing the
  # laptop would have lost Terraform's record of everything it made. The
  # bucket itself is the one thing not made here: a backend cannot create the
  # bucket it lives in (see infra/README.md).
  backend "gcs" {
    bucket = "your-gcp-project-tfstate"
    prefix = "shared"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  # The Budget API bills its calls to a project. Without these it falls back to
  # whichever project gcloud happens to have selected, and refuses.
  billing_project       = var.project_id
  user_project_override = true
}

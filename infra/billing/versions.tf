# R-ENV-05: infrastructure in Terraform, budgets included.

terraform {
  required_version = ">= 1.9"

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

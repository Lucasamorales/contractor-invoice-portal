# R-ENV-05: all infrastructure in Terraform. No console clicking.

terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      # 7.x, not 6.x: iap_enabled on google_cloud_run_v2_service does not
      # exist before v7, and without it IAP would need a load balancer.
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}


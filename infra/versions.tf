# R-ENV-05: all infrastructure in Terraform. No console clicking.

terraform {
  required_version = ">= 1.9"

  # One state per environment, as Terraform workspaces named after them:
  # env/staging.tfstate and env/production.tfstate in a versioned bucket. It
  # used to be one local file holding staging, which a production apply would
  # have rewritten into production, and which existed only on one laptop.
  #
  #   terraform workspace select staging     then  -var-file=staging.tfvars
  #   terraform workspace select production  then  -var-file=production.tfvars
  #
  # The default workspace is deliberately empty. The app service refuses a
  # plan whose environment does not match the workspace (cloudrun.tf).
  backend "gcs" {
    bucket = "your-gcp-project-tfstate"
    prefix = "env"
  }

  required_providers {
    google = {
      source = "hashicorp/google"
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


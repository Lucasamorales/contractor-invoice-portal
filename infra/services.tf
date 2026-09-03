# The APIs this project needs (R-ENV-05).
#
# Enabled here rather than by hand so a fresh project can be brought up from
# this directory alone. They are enabled once for the project, not per
# environment, so applying staging and then production is a no-op the second
# time.

locals {
  required_services = [
    "run.googleapis.com",             # the service itself
    "iap.googleapis.com",             # R-ACC-01, the login
    "cloudscheduler.googleapis.com",  # R-JOB-01..06
    "artifactregistry.googleapis.com" # where the image lives
    ,
    "secretmanager.googleapis.com", # R-SEC-02
    "storage.googleapis.com",       # R-STO-01
    "iamcredentials.googleapis.com" # signing V4 URLs without a key file
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project = var.project_id
  service = each.value

  # Turning an API off because Terraform is destroying one environment would
  # take the other one down with it. Leave them on.
  disable_on_destroy         = false
  disable_dependent_services = false
}

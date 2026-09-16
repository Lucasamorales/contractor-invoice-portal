# Cloud Run pulls its image as the service's own identity (R-ENV-05).
#
# The repository itself is shared by both environments, so it is made in
# infra/shared. Each environment grants only its own service account read
# access to it.

locals {
  image_repository = "portal"
}

resource "google_artifact_registry_repository_iam_member" "app_reader" {
  project    = var.project_id
  location   = var.region
  repository = local.image_repository
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.app.email}"
}

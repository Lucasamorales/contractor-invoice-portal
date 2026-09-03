# Where the container image lives (R-ENV-05).
#
# Chicken and egg: Cloud Run cannot start without an image, and the image
# cannot be pushed without somewhere to push it. So this repository is created
# first, on its own:
#
#   terraform apply -target=google_artifact_registry_repository.images
#
# then the image is built and pushed, and then the rest is applied.
#
# One repository serves both environments. The image is identical; what differs
# between staging and production is configuration, and a tag promoted from one
# to the other is the same bytes that were tested (R-ENV-01).

resource "google_artifact_registry_repository" "images" {
  provider = google

  repository_id = "portal"
  project       = var.project_id
  location      = var.region
  format        = "DOCKER"
  description   = "invoice portal images"

  # Artifact Registry's free allowance is 0.5GB of *stored*, compressed layers.
  # The image is about 138MB compressed, so a handful of versions fit. This
  # keeps the oldest from accumulating past that.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }
}

# Cloud Run pulls as the service's own identity.
resource "google_artifact_registry_repository_iam_member" "app_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.app.email}"
}

output "image_repository" {
  description = "Tag images as <this>/invoice-portal:<version> before pushing."
  value = join("", [
    google_artifact_registry_repository.images.location,
    "-docker.pkg.dev/",
    var.project_id,
    "/",
    google_artifact_registry_repository.images.repository_id,
  ])
}

# Where the container image lives (R-ENV-05).
#
# Shared, so it lives here and not in the environment stack. One repository
# serves both environments: the image is identical, what differs between
# staging and production is configuration, and an image promoted from one to
# the other is the same bytes that were tested (R-ENV-01). In the environment
# stack it was created by whichever environment applied first and would have
# collided when the second tried to create it again.
#
# Chicken and egg: Cloud Run cannot start without an image, and an image cannot
# be pushed without somewhere to push it, so this stack is applied before either
# environment.

resource "google_artifact_registry_repository" "images" {
  repository_id = "portal"
  project       = var.project_id
  location      = var.region
  format        = "DOCKER"
  description   = "invoice portal images"

  # Artifact Registry's free allowance is 0.5GB of *stored*, compressed layers.
  # The image is about 138MB compressed and every deploy pushes another, so at
  # a few deploys a day the repository reached 447MB in three weeks, with the
  # first deletion still a week away. Seven days rather than thirty, and the
  # five most recent are kept whatever their age: KEEP wins over DELETE, so a
  # quiet fortnight cannot leave nothing to roll back to.
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
      older_than = "604800s" # 7 days
    }
  }
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

# Neither id is a secret: they name the account and project, and grant nothing.

variable "billing_account" {
  description = "The Your Company billing account the portal's project bills to."
  type        = string
  default     = "01DF9D-0E9C58-21A9D8"
}

variable "project_id" {
  description = "The project staging and production both run in."
  type        = string
  default     = "your-gcp-project"
}

variable "region" {
  description = "Where the image registry lives, beside the services that pull from it."
  type        = string
  default     = "europe-west2"
}

variable "staging_monthly_gbp" {
  description = "Staging's monthly budget, in whole pounds."
  type        = number
  default     = 3
}

variable "production_monthly_gbp" {
  description = "Production's monthly budget, in whole pounds."
  type        = number
  default     = 5
}

variable "project_monthly_gbp" {
  description = "Everything in the project, labelled or not, in whole pounds."
  type        = number
  default     = 10
}

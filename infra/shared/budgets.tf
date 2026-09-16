# Budget alerts for the invoice portal (R-ENV-06: the cost target is £0).
#
# Kept apart from the environment stack in infra/: a budget belongs to the
# billing account, not to staging or production, and all three are wanted
# before production exists.
#
# Why three. Staging and production share one Google Cloud project, so each
# environment's budget picks out its own costs by the `environment` label on
# its Cloud Run services and bucket. Some costs carry no label at all:
# Scheduler jobs, the shared image registry, secrets, logging. The project
# budget counts everything, so a cost nobody thought to label still raises an
# alert.
#
# Nothing here stops spending. A budget only emails: the billing account's
# admins and users, at half the amount, at 90%, at 100%, and as soon as the
# month is forecast to go over, which is the one that fires within a day of a
# mistake like CPU always allocated.
#
# The amounts are several times the expected cost (under £1 a month for the
# whole project once production exists), so an alert means something changed.

data "google_project" "portal" {
  project_id = var.project_id
}

resource "google_project_service" "budgets" {
  project            = var.project_id
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

locals {
  budgets = {
    staging = {
      name   = "Invoice portal - staging"
      amount = var.staging_monthly_gbp
      labels = { environment = "staging" }
    }
    production = {
      name   = "Invoice portal - production"
      amount = var.production_monthly_gbp
      labels = { environment = "production" }
    }
    project = {
      name   = "Invoice portal - whole project"
      amount = var.project_monthly_gbp
      labels = {}
    }
  }
}

resource "google_billing_budget" "portal" {
  for_each = local.budgets

  billing_account = var.billing_account
  display_name    = each.value.name

  budget_filter {
    projects = ["projects/${data.google_project.portal.number}"]
    labels   = each.value.labels
    # Net of the free tier: an alert is about money actually owed.
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
    calendar_period        = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = "GBP"
      units         = tostring(each.value.amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  # Fires on the trend, before the money is spent.
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  depends_on = [google_project_service.budgets]
}

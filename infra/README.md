# infra

R-ENV-05: all infrastructure in Terraform, no console clicking.

Two stacks, and state for both in the versioned bucket
`your-gcp-project-tfstate`, so nothing depends on one laptop.

| Stack | Folder | State | Holds |
|---|---|---|---|
| **Environment** | `infra/` | `env/staging.tfstate`, `env/production.tfstate` (one workspace each) | Everything named per environment: both Cloud Run services, the bucket, service accounts, scheduler jobs, IAP access, secret access |
| **Shared** | `infra/shared/` | `shared/default.tfstate` | What both environments use: the image registry, and the budget alerts |

## Applying an environment

Always select the workspace, then pass the matching tfvars. The app service
refuses to plan when the two disagree, because applying one environment's
settings over the other's state rebuilds it: a production plan over the
staging state came out as 17 destroys.

```bash
cd infra && terraform init
```

```bash
terraform workspace select staging
```

```bash
terraform plan -var-file=staging.tfvars -out=staging.tfplan
```

Read the plan. Then:

```bash
terraform apply staging.tfplan
```

Production is the same with `production` and `production.tfvars`. The
`default` workspace is deliberately empty.

In Windows PowerShell 5.1 quote the argument, or it gets split on the `=`:
`"-var-file=staging.tfvars"`. Use `;` rather than `&&`.

### Releasing to production

1. Merge `staging` into `main` by pull request.
2. Set `image` in `production.tfvars` to the digest staging is running. The same
   bytes that were tested, never a new build.
3. Plan and apply in the `production` workspace.
4. Run any migration after the new code is live (`docs/setup.md`).

## What the environment stack creates

| Resource | Why |
|---|---|
| `portal-invoices-{env}` Cloud Run service | The portal. Scale to zero, CPU only while handling a request, IAP in front |
| `portal-tasks-{env}` Cloud Run service | Scheduled jobs only, and nothing else answers there |
| Five scheduler jobs | Outbox every 15 minutes, kudos sync daily, reminders on the 22nd and 27th, summary on the 29th, nightly backup. Each with its own OIDC token |
| `portal-invoices-{env}` bucket | PDFs, receipts, backups. Private, versioned, EU |
| Two service accounts | The app's identity, and a separate scheduler identity that cannot read the bank columns |
| IAP access | `iap_members` from the tfvars. Production: everyone on the Workspace domain |
| Secret access, per secret | So staging cannot read production's credentials |

Secrets are created by hand and only *named* here, so no value is ever in state.

## Costs

R-ENV-06 targets £0 a month. Two settings matter most:

- **`cpu_idle = true` on both services.** Setting a memory limit silently
  turned the provider's default off, and CPU was billed around the clock:
  about £1.50 a day on staging, until 16 September 2026.
- **No load balancer.** IAP sits directly on Cloud Run. A load balancer would
  cost around £15–18 a month on its own.

Expected spend is under £1 a month for both environments, mostly scheduler
jobs beyond the free three. The budgets in `infra/shared` email the billing
admins at 50%, 90%, 100% and on a forecast overspend.

## Bootstrapping from nothing

A backend cannot create the bucket it stores state in, so that one bucket is
made by hand, once:

```bash
gcloud storage buckets create gs://your-gcp-project-tfstate --location=europe-west2 --uniform-bucket-level-access --public-access-prevention
```

```bash
gcloud storage buckets update gs://your-gcp-project-tfstate --versioning
```

Then apply `infra/shared` (the registry must exist before an image can be
pushed), push an image, and apply each environment.

IAP needs an OAuth consent screen for the project. It is a one-off in the
console and is already configured.

# infra

R-ENV-05: all infrastructure in Terraform, no console clicking.

**Nothing here has been applied.** Applying it creates real resources, and one
of them costs money — see "The one cost decision" below. That is a call for
Leo, not for a build step.

## What this creates today

| Resource | Why |
|---|---|
| `portal-invoices-{env}` bucket | R-STO-01: private, uniform access, versioning on, public access prevention enforced |
| Lifecycle rules | A safety net behind R-JOB-05's own pruning, and a cap on old object versions |
| `portal-invoices-{env}` service account | Cloud Run's attached identity. No key files anywhere (CLAUDE.md §2.7) |
| Object admin on that bucket only | The application reads and writes its own files and nothing else |
| Token creator on itself | Signing V4 URLs without a private key on disk (R-STO-03) |
| Secret accessor, per secret | R-SEC-02, granted individually so staging cannot read production's credentials |

## What is not here yet

- **Cloud Run and Cloud IAP.** These need decisions only you can make: the
  OAuth consent screen, the IAP brand, and the finance IAM group. Until they
  exist nobody can log in at all, and `PORTAL_IAP_AUDIENCE` cannot be filled in
  because the value does not exist until the backend service does.
- **Cloud Scheduler jobs.** The six endpoints exist and verify their tokens;
  the jobs that call them need the service URL first.
- **The Drive folder** (R-STO-05). Which Shared Drive has not been decided.

## The one cost decision

R-ENV-06 targets £0/month, and this is the first thing that will not be zero.

GCS's always-free allowance covers **US regions only**. The bucket defaults to
`europe-west2` because the PDFs carry every person's full account number and
legal name, and keeping those in the EU is deliberate.

The actual bill at this volume is tiny — sixteen invoices a month of roughly
20KB, plus thirty compressed backups of about 3KB, is well under a penny a
month in storage. But it is not zero, and R-ENV-06 says to raise that rather
than provision it quietly.

If £0 matters more than EU residency, set `region = "us-east1"` and it falls
inside the free tier. **Ask Robin before doing that** — it changes where
sixteen people's bank details are stored.

## Running it

```bash
cd infra && terraform init
```

```bash
terraform plan -var project_id=your-gcp-project -var environment=staging
```

Read the plan. Then, if it is what you want:

```bash
terraform apply -var project_id=your-gcp-project -var environment=staging
```

Afterwards, set the two outputs it prints as `PORTAL_GCS_BUCKET` and
`PORTAL_SERVICE_ACCOUNT_EMAIL`.

Do staging first, use it for a cycle, and only then run the same commands with
`-var environment=production`.

## State

Local state for now, which is fine for one operator and no CI applying it. If
that changes, move it to a GCS backend in its own bucket before two people ever
run `apply`.

# infra

R-ENV-05: all infrastructure in Terraform, no console clicking.

**Nothing here has been applied.** Applying it creates real resources, and two
of them cost money — see "Two cost decisions" below. Those are calls for Leo,
not for a build step. None of it has been through `terraform validate` either;
see "Unvalidated".

## What this creates today

| Resource | Why |
|---|---|
| `portal-invoices-{env}` bucket | R-STO-01: private, uniform access, versioning on, public access prevention enforced |
| Lifecycle rules | A safety net behind R-JOB-05's own pruning, and a cap on old object versions |
| `portal-invoices-{env}` service account | Cloud Run's attached identity. No key files anywhere (CLAUDE.md §2.7) |
| Object admin on that bucket only | The application reads and writes its own files and nothing else |
| Token creator on itself | Signing V4 URLs without a private key on disk (R-STO-03) |
| Secret accessor, per secret | R-SEC-02, granted individually so staging cannot read production's credentials |
| Cloud Run service | Scale to zero, secrets referenced not inlined, IAP in front |
| Six Cloud Scheduler jobs | R-JOB-01..06, each with its own OIDC token |
| A separate scheduler service account | A compromised scheduler must not be able to read the bank columns |

## What is not here yet

- **The Drive folder** (R-STO-05). Which Shared Drive has not been decided.
- **Artifact Registry.** The image has to live somewhere before Cloud Run can
  pull it.

## Two cost decisions

R-ENV-06 targets £0/month. Two things here are not zero, and both are yours
to decide rather than mine to assume.

### 1. The load balancer you do not need

This is the biggest single cost decision in the project, and it is easy to get
wrong by following the common documentation.

IAP in front of Cloud Run can be arranged two ways:

1. **IAP directly on the Cloud Run service.** No load balancer. Free.
2. **A global external HTTPS load balancer** with a serverless NEG and IAP on
   the backend service. This is the older and far more widely documented
   arrangement, and the forwarding rule alone is roughly **£15–18 a month**
   whether or not anybody uses it.

`cloudrun.tf` uses (1). Option (2) would miss the £0 target of R-ENV-06 on its
own, before a single invoice is stored, for sixteen people invoicing once a
month.

The trade: (1) serves from a `*.run.app` URL rather than
`invoices.example.com`. If the custom domain matters more than the
money, that is a decision to take deliberately rather than drift into.

### 2. The bucket's region

GCS's always-free allowance covers **US regions only**. The bucket defaults to
`europe-west2` because the PDFs carry every person's full account number and
legal name, and keeping those in the EU is deliberate.

The actual bill at this volume is tiny — sixteen invoices a month of roughly
20KB, plus thirty compressed backups of about 3KB, is well under a penny a
month. But it is not zero.

If £0 matters more than EU residency, set `region = "us-east1"` and it falls
inside the free tier. **Ask Robin before doing that** — it changes where
sixteen people's bank details are stored. My recommendation is to keep the EU
and pay the pennies.

## Validated, not applied

`terraform init`, `fmt` and `validate` all pass against provider v7.46.0.
`plan` has **not** been run: it needs live GCP credentials and an image that
does not exist yet.

Validation found two real bugs that would each have cost an afternoon:

1. **`iap_enabled` does not exist in provider 6.x.** `versions.tf` requires
   `~> 7.0` for exactly this reason. On v6 the only way to get IAP is the load
   balancer, with the bill described above.
2. **The ingress setting was wrong.** It was
   `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`, which is right when IAP sits
   behind a load balancer and makes the service unreachable when it does not.
   It is now `INGRESS_TRAFFIC_ALL`; what keeps people out is `iap_enabled`
   plus the IAM, not the ingress setting.

Two things still to confirm on the first real apply, because no amount of
validation can check them:

- **The IAP audience.** Direct-on-Cloud-Run it is the service URL; behind a load
  balancer it is `/projects/<number>/global/backendServices/<id>`. Check the
  `aud` claim on the first login. `app/auth.py` verifies the audience, so a
  wrong value fails closed rather than letting anybody in.
- **Whether `roles/run.invoker` is the right grant for end users** with direct
  IAP, or whether they also need `roles/iap.httpsResourceAccessor`. If people
  can sign in but then get a 403, that is the first thing to add.

## Running it

Terraform is not installed on Leo's machine yet:

```powershell
winget install --id HashiCorp.Terraform -e
```

Copy `terraform.tfvars.example` to `staging.tfvars` and fill it in. Then, in
**PowerShell** — note `;` rather than `&&`, which Windows PowerShell 5.1 does
not accept:

```powershell
cd infra; terraform init
```

```powershell
terraform plan -var-file=staging.tfvars
```

Read the plan properly. Then, if it is what you want:

```powershell
terraform apply -var-file=staging.tfvars
```

Afterwards set the outputs it prints: `PORTAL_GCS_BUCKET`,
`PORTAL_SERVICE_ACCOUNT_EMAIL`, `PORTAL_SCHEDULER_SERVICE_ACCOUNT`, and the service
URL as both `PORTAL_IAP_AUDIENCE` and `PORTAL_SCHEDULER_AUDIENCE`.

Do staging first, use it for a cycle, and only then the same with
`production.tfvars`.

### Before the first apply

- Enable the APIs: `run.googleapis.com`, `iap.googleapis.com`,
  `cloudscheduler.googleapis.com`, `artifactregistry.googleapis.com`.
- Build and push the image somewhere Cloud Run can pull it, and put that
  reference in `image`.
- IAP needs an OAuth consent screen configured for the project. That is a
  one-off in the console and cannot sensibly be Terraformed for an internal
  app.

## State

Local state for now, which is fine for one operator and no CI applying it. If
that changes, move it to a GCS backend in its own bucket before two people ever
run `apply`.

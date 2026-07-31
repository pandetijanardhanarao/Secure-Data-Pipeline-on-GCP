# HabotConnect — Hiring Project Submission
**Position:** Junior Cloud & DevOps Engineer (GCP / Django / React)
**Candidate:** [Your full name here]
**Contact:** [Your email / phone here]

## Scenario Recap

A junior developer pushed unencrypted API credentials to raw application code
and caused a database schema mismatch that broke downstream analytics. This
submission restores system integrity across three layers: infrastructure,
pipeline enforcement, and schema validation.

## Folder Layout

```
habotconnect-project/
├── terraform/
│   └── main.tf                          # Task 1: IaC provisioning
├── .github/workflows/
│   └── ci.yml                           # Task 2: Poka-Yoke build gate
├── django_app/
│   ├── serializers.py                   # Task 3: DCYN schema validation
│   └── tests/
│       └── test_schema_contract.py      # Gate 3 automated proof
├── requirements.txt
└── README.md
```

## Task 1 — Terraform (IaC): Secure Staging Provisioning

- **D0 Raw Landing** (`google_storage_bucket.d0_raw_landing`): uniform bucket-level
  access, public access prevention enforced, CMEK encryption via a dedicated
  KMS key, 30-day lifecycle expiry (raw data isn't meant to live long — it's
  meant to get staged), and versioning for auditability.
- **D1 Staged/Enforced** (`google_bigquery_dataset.d1_staged_enforced`): only the
  pipeline service account can write; the data engineer group can only read.
- **IAM conditions**: the data engineer group's read access on D0 is time-boxed
  (a `condition` block) rather than standing forever — that's Least Privilege
  applied to *time*, not just to actions.
- **Row-Level Security**: two `google_bigquery_row_access_policy` resources —
  LSA ops staff see only their own region's rows (`region_code = SESSION_USER_REGION()`),
  data engineers see everything. This is the RLS control the brief asked for.

## Task 2 — Poka-Yoke CI/CD (Fail-Closed Build Gate)

Three sequential gates, each blocking the next if it fails:

1. **Lint Gate** — Black + Flake8. No auto-fix; if formatting doesn't already
   comply, the build stops. Formatting is not a matter of taste here — it's a
   gate.
2. **Security Gate** — `gitleaks` scans full git history for hardcoded secrets
   (this is exactly what would have caught the raw API credentials incident),
   `bandit` for static security issues in the Django app, `pip-audit` for known
   CVEs in dependencies. Any finding = non-zero exit = quarantined build.
3. **Schema Gate** — runs `test_schema_contract.py`, which proves the DRF
   serializer and the BigQuery table schema haven't drifted apart.

Only if all three pass does `build_approved` run — the single job permitted to
hand off to deployment. There's no path around it.

## Task 3 — DCYN Schema Validation

The **DCYNField** class rejects anything that isn't a literal Python boolean —
no `"yes"`, no `1`, no `"Y"`. This is the direct fix for "ambiguous JSON caused
a schema mismatch": ambiguity is rejected at the serializer boundary before it
ever reaches BigQuery.

A cross-field business rule enforces that `requires_lsa_support=True` requires
`consent_data_sharing=True` — you can't stage a record asking for LSA matching
without recorded consent to share the data that makes that matching possible.

## Running Locally

```bash
pip install -r requirements.txt
python -m pytest django_app/tests/test_schema_contract.py -v

cd terraform
terraform init
terraform plan -var="project_id=YOUR_GCP_PROJECT_ID"
```

## Design Trade-offs (for the presentation)

- Terraform's native `google_bigquery_row_access_policy` resource was used
  instead of a `null_resource` + `local-exec` `bq query` hack — keeps RLS
  policy changes inside Terraform state and plan/diff visibility, rather than
  hidden inside an opaque shell command.
- CMEK (customer-managed encryption key) on D0 is a deliberate step beyond
  the brief's minimum ask — raw landing data is the highest-risk layer since
  it hasn't been schema-validated yet, so it gets the strongest key control.
- The lint gate runs first (cheapest, fastest) so obviously non-compliant
  commits fail fast before burning CI minutes on security scans.

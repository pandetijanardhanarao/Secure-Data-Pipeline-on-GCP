############################################################
# HabotConnect - Task 1: Secure Staging Provisioning (IaC)
# D0 (Raw Landing) -> GCS bucket
# D1 (Staged / Enforced) -> BigQuery dataset with RLS
############################################################

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

############################
# Variables
############################

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Default region for resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment (staging/production)"
  type        = string
  default     = "staging"
}

variable "data_engineer_group" {
  description = "Google Group email for data engineers who need Staged/Enforced read access"
  type        = string
  default     = "data-engineers@habotconnect.com"
}

variable "lsa_ops_group" {
  description = "Google Group email for LSA operations staff, restricted to their own region rows"
  type        = string
  default     = "lsa-ops@habotconnect.com"
}

############################
# D0: Raw Landing (GCS)
############################

resource "google_storage_bucket" "d0_raw_landing" {
  name     = "${var.project_id}-d0-raw-landing-${var.environment}"
  location = var.region
  project  = var.project_id

  # No public access, ever
  public_access_prevention = "enforced"

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  # Raw landing data is transient by design once staged into BigQuery
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.raw_landing_key.id
  }

  labels = {
    layer       = "d0-raw-landing"
    environment = var.environment
    managed_by  = "terraform"
  }
}

# Dedicated CMEK key so raw PII-adjacent student/parent data is encrypted
# with a key we control the rotation and access policy for.
resource "google_kms_key_ring" "habotconnect_keyring" {
  name     = "habotconnect-${var.environment}-keyring"
  location = var.region
  project  = var.project_id
}

resource "google_kms_crypto_key" "raw_landing_key" {
  name            = "d0-raw-landing-key"
  key_ring        = google_kms_key_ring.habotconnect_keyring.id
  rotation_period = "7776000s" # 90 days
}

# Least-privilege IAM: only the pipeline service account may write.
# Human access is read-only and only for the data engineer group.
resource "google_storage_bucket_iam_member" "d0_writer_pipeline_sa" {
  bucket = google_storage_bucket.d0_raw_landing.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.pipeline_sa.email}"
}

resource "google_storage_bucket_iam_member" "d0_reader_data_engineers" {
  bucket = google_storage_bucket.d0_raw_landing.name
  role   = "roles/storage.objectViewer"
  member = "group:${var.data_engineer_group}"

  condition {
    title       = "d0-read-only-non-expired"
    description = "Data engineers may only read objects; no delete/write. Time-boxed to reduce standing access risk."
    expression  = "request.time < timestamp(\"2027-01-01T00:00:00Z\")"
  }
}

############################
# D1: Staged / Enforced (BigQuery)
############################

resource "google_bigquery_dataset" "d1_staged_enforced" {
  dataset_id                 = "d1_staged_enforced"
  project                    = var.project_id
  location                   = var.region
  description                = "Schema-validated, access-controlled staging layer feeding downstream analytics."
  default_table_expiration_ms = null

  labels = {
    layer       = "d1-staged-enforced"
    environment = var.environment
  }

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role           = "READER"
    group_by_email = var.data_engineer_group
  }

  # Explicitly do NOT grant broad WRITER access; only the pipeline SA writes.
  access {
    role          = "WRITER"
    user_by_email = google_service_account.pipeline_sa.email
  }
}

resource "google_bigquery_table" "student_onboarding" {
  dataset_id = google_bigquery_dataset.d1_staged_enforced.dataset_id
  table_id   = "student_onboarding"
  project    = var.project_id

  deletion_protection = true

  schema = jsonencode([
    { name = "student_id", type = "STRING", mode = "REQUIRED" },
    { name = "region_code", type = "STRING", mode = "REQUIRED" },
    { name = "guardian_email", type = "STRING", mode = "REQUIRED" },
    { name = "has_iep", type = "BOOL", mode = "REQUIRED" },        # DCYN: Yes/No
    { name = "requires_lsa_support", type = "BOOL", mode = "REQUIRED" }, # DCYN: Yes/No
    { name = "consent_data_sharing", type = "BOOL", mode = "REQUIRED" }, # DCYN: Yes/No
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}

# Row-Level Security: LSA ops staff can only see rows for their own region.
# Data engineers (broader analytics role) can see all rows.
resource "google_bigquery_row_access_policy" "lsa_region_scoped" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.d1_staged_enforced.dataset_id
  table_id   = google_bigquery_table.student_onboarding.table_id
  policy_id  = "lsa_ops_region_scope"

  filter_predicate = "region_code = SESSION_USER_REGION()"

  grantees = [
    "group:${var.lsa_ops_group}",
  ]
}

resource "google_bigquery_row_access_policy" "data_engineer_full_access" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.d1_staged_enforced.dataset_id
  table_id   = google_bigquery_table.student_onboarding.table_id
  policy_id  = "data_engineer_full_access"

  filter_predicate = "TRUE"

  grantees = [
    "group:${var.data_engineer_group}",
  ]
}

############################
# Pipeline Service Account (Least Privilege)
############################

resource "google_service_account" "pipeline_sa" {
  account_id   = "habot-pipeline-${var.environment}"
  display_name = "HabotConnect ETL Pipeline SA (${var.environment})"
  project      = var.project_id
}

# Pipeline SA gets ONLY what it needs: write raw, write staged. Nothing else.
resource "google_project_iam_member" "pipeline_sa_bq_data_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.pipeline_sa.email}"

  condition {
    title      = "scoped-to-d1-dataset"
    expression = "resource.name.startsWith(\"projects/${var.project_id}/datasets/d1_staged_enforced\")"
  }
}

############################
# Outputs
############################

output "d0_raw_landing_bucket" {
  value = google_storage_bucket.d0_raw_landing.name
}

output "d1_staged_enforced_dataset" {
  value = google_bigquery_dataset.d1_staged_enforced.dataset_id
}

output "pipeline_service_account" {
  value = google_service_account.pipeline_sa.email
}

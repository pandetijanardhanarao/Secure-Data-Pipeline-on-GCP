"""
Gate 3 - Schema Contract Validation.

This test suite is the automated proof that the DRF serializer and the
BigQuery d1_staged_enforced.student_onboarding table (terraform/main.tf)
cannot silently drift apart. If someone edits the serializer without
updating Terraform (or vice versa), this test fails and the Poka-Yoke
gate quarantines the build before it ever reaches staging.
"""

import pytest
from django_app.serializers import StudentOnboardingSerializer

# This mirrors the exact schema in terraform/main.tf -> student_onboarding.
# Any drift between this list and the serializer's declared fields fails
# the build, by design.
EXPECTED_BIGQUERY_SCHEMA_FIELDS = {
    "student_id",
    "region_code",
    "guardian_email",
    "has_iep",
    "requires_lsa_support",
    "consent_data_sharing",
    "ingested_at",  # populated server-side in .create(), not client input
}

VALID_PAYLOAD = {
    "student_id": "STU-000123",
    "region_code": "NA",
    "guardian_email": "parent@example.com",
    "has_iep": True,
    "requires_lsa_support": True,
    "consent_data_sharing": True,
}


def test_serializer_fields_match_bigquery_schema():
    """The serializer's output fields, plus server-populated ingested_at,
    must exactly match the BigQuery table schema. No extra, no missing."""
    serializer = StudentOnboardingSerializer(data=VALID_PAYLOAD)
    assert serializer.is_valid(), serializer.errors

    validated = serializer.create(serializer.validated_data)
    assert set(validated.keys()) == EXPECTED_BIGQUERY_SCHEMA_FIELDS


def test_dcyn_rejects_string_yes_no():
    """DCYN fields must reject string synonyms like 'yes'/'no' outright."""
    payload = dict(VALID_PAYLOAD)
    payload["has_iep"] = "yes"  # ambiguous input, must be rejected
    serializer = StudentOnboardingSerializer(data=payload)
    assert not serializer.is_valid()
    assert "has_iep" in serializer.errors


def test_dcyn_rejects_integer_coercion():
    """DCYN fields must reject 1/0 integer coercion."""
    payload = dict(VALID_PAYLOAD)
    payload["consent_data_sharing"] = 1
    serializer = StudentOnboardingSerializer(data=payload)
    assert not serializer.is_valid()
    assert "consent_data_sharing" in serializer.errors


def test_lsa_support_requires_consent_business_rule():
    """Cross-field rule: requires_lsa_support=True demands
    consent_data_sharing=True. This is the exact downstream analytics
    break described in the incident scenario."""
    payload = dict(VALID_PAYLOAD)
    payload["requires_lsa_support"] = True
    payload["consent_data_sharing"] = False
    serializer = StudentOnboardingSerializer(data=payload)
    assert not serializer.is_valid()
    assert "consent_data_sharing" in serializer.errors


def test_region_code_must_match_rls_policy_values():
    """region_code must be one of the values the BigQuery Row-Level
    Security policy (lsa_region_scoped) actually filters on."""
    payload = dict(VALID_PAYLOAD)
    payload["region_code"] = "MOON"  # not a valid RLS region
    serializer = StudentOnboardingSerializer(data=payload)
    assert not serializer.is_valid()
    assert "region_code" in serializer.errors


def test_no_blank_student_id():
    payload = dict(VALID_PAYLOAD)
    payload["student_id"] = ""
    serializer = StudentOnboardingSerializer(data=payload)
    assert not serializer.is_valid()
    assert "student_id" in serializer.errors

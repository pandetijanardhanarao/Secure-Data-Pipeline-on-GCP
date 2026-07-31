"""
HabotConnect - Task 3: Schema Mapping and DCYN Validation

DCYN = "Deconstructed / Clean Yes-No" library. The goal: every field that
represents a decision must be a strict boolean, with exact validation,
so downstream analytics (Pub/Sub -> BigQuery) never receives an ambiguous
value such as "yes", "Y", "true", 1, or an empty string doing duty as
"unknown". Human judgment is removed entirely from interpretation.
"""

from rest_framework import serializers


class DCYNField(serializers.BooleanField):
    """
    A field that will ONLY accept an actual boolean. No coercion from
    strings, integers, or synonyms. This is the enforcement point that
    eliminates the "raw JSON with ambiguous yes/no" failure mode
    described in the incident scenario.
    """

    def to_internal_value(self, data):
        if not isinstance(data, bool):
            raise serializers.ValidationError(
                f"DCYN violation: expected strict boolean (True/False), "
                f"received {type(data).__name__} = {data!r}. "
                f"No string, integer, or synonym coercion is permitted."
            )
        return data


class StudentOnboardingSerializer(serializers.Serializer):
    """
    Deconstructs an incoming student onboarding JSON payload into a
    validated, schema-locked structure that matches the D1 Staged/Enforced
    BigQuery table exactly (see terraform/main.tf: student_onboarding table).

    Full-form only: no abbreviations, no placeholder defaults. Every field
    is REQUIRED and explicitly typed so the payload cannot silently drift
    from the BigQuery schema.
    """

    student_id = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=64,
        help_text="Unique student identifier. No blank or null values permitted.",
    )

    region_code = serializers.ChoiceField(
        required=True,
        choices=["NA", "EU", "APAC", "MEA", "LATAM"],
        help_text=(
            "Region code used for BigQuery Row-Level Security "
            "(see google_bigquery_row_access_policy.lsa_region_scoped)."
        ),
    )

    guardian_email = serializers.EmailField(
        required=True,
        allow_blank=False,
        help_text="Parent/guardian contact email. Validated as a proper email format.",
    )

    has_iep = DCYNField(
        required=True,
        help_text="Does the student have an Individualized Education Plan? Strict boolean.",
    )

    requires_lsa_support = DCYNField(
        required=True,
        help_text="Does the student require a Learning Support Assistant? Strict boolean.",
    )

    consent_data_sharing = DCYNField(
        required=True,
        help_text="Has the guardian consented to data sharing for LSA matching? Strict boolean.",
    )

    def validate(self, data):
        """
        Cross-field business rule: a student cannot be marked as requiring
        LSA support without recorded guardian consent to share the data
        needed to arrange that support. This is the exact class of
        downstream analytics inconsistency the incident scenario describes.
        """
        if data.get("requires_lsa_support") and not data.get("consent_data_sharing"):
            raise serializers.ValidationError(
                {
                    "consent_data_sharing": (
                        "DCYN business rule violation: requires_lsa_support=True "
                        "requires consent_data_sharing=True. Cannot stage a record "
                        "requesting LSA matching without explicit consent."
                    )
                }
            )
        return data

    def create(self, validated_data):
        """
        Writes only fully-validated, DCYN-clean records to the D1
        Staged/Enforced sink. The pipeline service account (see
        terraform: google_service_account.pipeline_sa) is the only
        identity permitted to perform this write, per Least Privilege.
        """
        from datetime import datetime, timezone

        validated_data["ingested_at"] = datetime.now(timezone.utc).isoformat()
        # In production this would publish to Pub/Sub -> BigQuery streaming
        # insert into d1_staged_enforced.student_onboarding, matching the
        # exact schema defined in terraform/main.tf.
        return validated_data

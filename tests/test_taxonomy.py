"""
Tests for the failure taxonomy data.
"""

from backend.models.schemas import FailureReason, RetryStrategy
from backend.taxonomy.failure_taxonomy import FAILURE_TAXONOMY


class TestFailureTaxonomy:
    """Verify the failure taxonomy is complete and well-formed."""

    def test_all_failure_reasons_have_entries(self) -> None:
        """Every FailureReason enum member must have a taxonomy entry."""
        for reason in FailureReason:
            assert reason in FAILURE_TAXONOMY, f"Missing taxonomy entry for {reason}"

    def test_no_extra_entries(self) -> None:
        """Taxonomy should not have entries outside the FailureReason enum."""
        for key in FAILURE_TAXONOMY:
            assert isinstance(key, FailureReason), f"Unexpected key: {key}"

    def test_entries_have_required_fields(self) -> None:
        """Each taxonomy entry must have description, retryable, default_strategies, base_recovery_rate."""
        required_keys = {"description", "retryable", "default_strategies", "base_recovery_rate"}
        for reason, entry in FAILURE_TAXONOMY.items():
            assert required_keys.issubset(entry.keys()), (
                f"{reason}: missing keys {required_keys - entry.keys()}"
            )

    def test_retryable_flags(self) -> None:
        """Verify specific retryability expectations."""
        assert FAILURE_TAXONOMY[FailureReason.network_error]["retryable"] is True
        assert FAILURE_TAXONOMY[FailureReason.insufficient_funds]["retryable"] is True
        assert FAILURE_TAXONOMY[FailureReason.card_expired]["retryable"] is False
        assert FAILURE_TAXONOMY[FailureReason.fraud_suspected]["retryable"] is False
        assert FAILURE_TAXONOMY[FailureReason.invalid_card]["retryable"] is False
        assert FAILURE_TAXONOMY[FailureReason.duplicate_transaction]["retryable"] is False

    def test_strategies_non_empty(self) -> None:
        """Each entry must have at least one default strategy."""
        for reason, entry in FAILURE_TAXONOMY.items():
            strategies = entry["default_strategies"]
            assert len(strategies) >= 1, f"{reason}: no default strategies"
            for s in strategies:
                assert isinstance(s, RetryStrategy), f"{reason}: invalid strategy {s}"

    def test_recovery_rates_in_bounds(self) -> None:
        """Base recovery rates must be between 0 and 1."""
        for reason, entry in FAILURE_TAXONOMY.items():
            rate = entry["base_recovery_rate"]
            assert 0.0 <= rate <= 1.0, f"{reason}: rate {rate} out of bounds"

    def test_non_retryable_have_low_recovery(self) -> None:
        """Non-retryable failures should have recovery rate <= 0.10."""
        for reason, entry in FAILURE_TAXONOMY.items():
            if not entry["retryable"]:
                assert entry["base_recovery_rate"] <= 0.10, (
                    f"{reason}: non-retryable but high recovery rate {entry['base_recovery_rate']}"
                )

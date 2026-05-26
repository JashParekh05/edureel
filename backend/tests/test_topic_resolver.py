"""Conservative resolver guard: same-concept merges allowed, specificity drift blocked."""
from app.services.topic_resolver import _is_specificity_drift


class TestSpecificityDrift:
    def test_same_concept_with_filler_allowed(self):
        # "fundamentals" is filler → same core as binary-search → safe to merge
        assert not _is_specificity_drift("Binary Search Fundamentals", "binary-search-fundamentals", "binary-search")

    def test_exact_core_allowed(self):
        assert not _is_specificity_drift("Understanding DNS Servers", "understanding-dns-servers", "dns-servers")

    def test_compound_word_allowed(self):
        # "Hash Maps" vs slug "hashmaps" — disjoint tokens, neither is broader → defer to cosine
        assert not _is_specificity_drift("Hash Maps", "intro-hash-maps", "hashmaps")

    def test_specific_into_generic_blocked(self):
        # binary-search-trees is more specific than binary-search → block
        assert _is_specificity_drift("Binary Search Trees", "binary-search-trees", "binary-search")

    def test_parent_into_child_blocked(self):
        # broad "search" should not collapse into the specific "binary-search"
        assert _is_specificity_drift("Search", "search", "binary-search")

    def test_no_lexical_signal_defers(self):
        # nothing lexical to judge → not flagged here (cosine threshold still gates it)
        assert not _is_specificity_drift("", "", "binary-search")

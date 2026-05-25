from app.services.discover_seeding import _interest_seed_slugs, _match_interest_slugs, GRADE_LEVEL_TOPIC_MAP


class TestInterestSeedSlugs:
    def test_maps_interest_to_grade_topics(self):
        slugs = _interest_seed_slugs(["science"], "high_school")
        assert slugs == GRADE_LEVEL_TOPIC_MAP["high_school"]["science"]

    def test_interest_alias(self):
        # "space" is aliased to "science"
        assert _interest_seed_slugs(["space"], "high_school") == \
            GRADE_LEVEL_TOPIC_MAP["high_school"]["science"]

    def test_grade_alias(self):
        # "preschool" is aliased to "elementary_school"
        assert _interest_seed_slugs(["math"], "preschool") == \
            GRADE_LEVEL_TOPIC_MAP["elementary_school"]["math"]

    def test_unknown_grade_falls_back_to_high_school(self):
        assert _interest_seed_slugs(["math"], "nonsense-grade") == \
            GRADE_LEVEL_TOPIC_MAP["high_school"]["math"]

    def test_dedupes_across_interests(self):
        slugs = _interest_seed_slugs(["science", "science"], "high_school")
        assert len(slugs) == len(set(slugs))


class TestMatchInterestSlugsKeyword:
    def test_keyword_overlap(self):
        out = _match_interest_slugs(["python"], ["python-intro", "biology", "python-web"])
        assert set(out) == {"python-intro", "python-web"}

    def test_no_interest_no_taste_returns_prefix(self):
        all_slugs = [f"s{i}" for i in range(20)]
        assert _match_interest_slugs([], all_slugs) == all_slugs[:10]

    def test_no_match_falls_back(self):
        out = _match_interest_slugs(["zzz"], ["alpha", "beta"])
        assert out == ["alpha", "beta"]

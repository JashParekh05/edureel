from app.services.feed_scoring import (
    _parse_vector,
    _compute_scores,
    _transcript_boost,
    _interleave_topics,
    _spread_by_source,
)
from app.models.schemas import FeedResponse
from tests.conftest import make_clip


class TestParseVector:
    def test_list_passthrough(self):
        assert _parse_vector([0.1, 0.2]) == [0.1, 0.2]

    def test_json_string(self):
        assert _parse_vector("[0.1, 0.2]") == [0.1, 0.2]

    def test_none(self):
        assert _parse_vector(None) is None

    def test_garbage_string(self):
        assert _parse_vector("not json") is None


class TestComputeScores:
    def test_neutral_baseline(self):
        # No pop stats, no avg watch, no interest/taste, no created_at:
        # 0.28*0.5 + 0.23*0.5 + 0.18*1.0 + 0.13*0.5 + 0.10*0.5 + 0.08*0.5 = 0.59
        clip = make_clip(hook_score=0.5)
        _compute_scores([clip], {}, None)
        assert clip.final_score == 0.59

    def test_liked_topic_scores_higher(self):
        neutral = make_clip(topic_slug="a", hook_score=0.5)
        liked = make_clip(topic_slug="b", hook_score=0.5)
        _compute_scores([neutral], {}, None, interest_vector={})
        _compute_scores([liked], {}, None, interest_vector={"b": 1.0})
        assert liked.final_score > neutral.final_score

    def test_higher_hook_scores_higher(self):
        low = make_clip(hook_score=0.2)
        high = make_clip(hook_score=0.9)
        _compute_scores([low, high], {}, None)
        assert high.final_score > low.final_score


class TestTranscriptBoost:
    def test_keyword_match_boosts(self):
        clip = make_clip(transcript="this explains binary search clearly", final_score=0.5)
        _transcript_boost([clip], "binary search")
        assert clip.final_score > 0.5

    def test_no_query_unchanged(self):
        clip = make_clip(transcript="anything", final_score=0.5)
        _transcript_boost([clip], "")
        assert clip.final_score == 0.5

    def test_only_stopwords_unchanged(self):
        clip = make_clip(transcript="the and for that", final_score=0.5)
        _transcript_boost([clip], "the and for")
        assert clip.final_score == 0.5


class TestSpreadBySource:
    def test_no_consecutive_same_source(self):
        clips = [
            make_clip(source_url="A"),
            make_clip(source_url="A"),
            make_clip(source_url="B"),
        ]
        out = _spread_by_source(clips)
        sources = [c.source_url for c in out]
        assert sources == ["A", "B", "A"]

    def test_single_clip_unchanged(self):
        clips = [make_clip(source_url="A")]
        assert _spread_by_source(clips) == clips


class TestInterleaveTopics:
    def test_single_feed_passthrough(self):
        feed = FeedResponse(topic_slug="a", clips=[make_clip()], processing=False)
        assert _interleave_topics([feed]) == [feed]

    def test_no_clips_lost(self):
        a = FeedResponse(topic_slug="a", clips=[make_clip(topic_slug="a") for _ in range(8)], processing=False)
        b = FeedResponse(topic_slug="b", clips=[make_clip(topic_slug="b") for _ in range(3)], processing=False)
        out = _interleave_topics([a, b])
        before = {c.id for f in [a, b] for c in f.clips}
        after = {c.id for f in out for c in f.clips}
        assert before == after

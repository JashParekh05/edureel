"""Lever 2: segmentation candidate bounding in pipeline_agent._node_transcribe."""
import app.services.youtube as youtube
from app.agents import pipeline_agent


def _state(section_index, n_videos=6, missing=()):
    return {
        "videos": [{"video_id": str(i)} for i in range(1, n_videos + 1)],
        "section_index": section_index,
        "topic_slug": "t",
        "topic_name": "T",
    }


def _stub_transcripts(monkeypatch, missing=()):
    def fake(vid):
        return None if vid in missing else [{"start": 0, "duration": 1, "text": "x"}]
    monkeypatch.setattr(youtube, "_fetch_transcript", fake)


def test_first_section_keeps_one_video(monkeypatch):
    _stub_transcripts(monkeypatch)
    out = pipeline_agent._node_transcribe(_state(0))
    assert [v["video_id"] for v in out["videos"]] == ["1"]


def test_other_sections_keep_two_videos(monkeypatch):
    _stub_transcripts(monkeypatch)
    for sec in (1, 2, 3):
        out = pipeline_agent._node_transcribe(_state(sec))
        assert len(out["videos"]) == 2


def test_none_section_keeps_two(monkeypatch):
    _stub_transcripts(monkeypatch)
    out = pipeline_agent._node_transcribe(_state(None))
    assert len(out["videos"]) == 2


def test_skips_videos_without_transcript(monkeypatch):
    # videos 1 and 2 lack transcripts → should keep 3 and 4 for a non-first section
    _stub_transcripts(monkeypatch, missing={"1", "2"})
    out = pipeline_agent._node_transcribe(_state(1))
    assert [v["video_id"] for v in out["videos"]] == ["3", "4"]
    assert len(out["errors"]) == 2


def test_all_missing_keeps_none(monkeypatch):
    _stub_transcripts(monkeypatch, missing={"1", "2", "3", "4", "5", "6"})
    out = pipeline_agent._node_transcribe(_state(1))
    assert out["videos"] == []

from datetime import datetime, timedelta, timezone

from pipeline.memory import HeadlineMemory, title_overlap
from pipeline.stages.curator import _merge_llm_brief, dedup_stories, score_story, simple_dedup
from pipeline.storage import Storage

NOW = datetime(2026, 9, 26, 6, 0, tzinfo=timezone.utc)


def _story(title, hours_ago=1, tier="1", coverage=1, url=None):
    return {
        "title": title,
        "url": url or f"https://example.com/{abs(hash(title))}",
        "summary": "",
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "source_tier": tier,
        "coverage_count": coverage,
    }


def test_score_rubric_matches_original():
    assert score_story(_story("a", 1, "0", 3), NOW)["score"] == 3 + 3 + 3
    assert score_story(_story("b", 20, "1", 2), NOW)["score"] == 2 + 1 + 1
    assert score_story(_story("c", 30, "2", 1), NOW)["score"] == 1
    assert score_story(_story("d", 48, "2", 0), NOW)["score"] == 0


def test_score_tolerates_missing_date():
    s = _story("x", tier="0")
    s["published_at"] = None
    assert score_story(s, NOW)["score"] == 3


def test_title_overlap_and_simple_dedup():
    assert title_overlap("OpenAI releases new model today", "OpenAI releases new model today") == 1.0
    stories = [_story("OpenAI releases GPT-6 model today"), _story("OpenAI releases GPT-6 model today, analysts say"), _story("Anthropic ships agent SDK")]
    kept = simple_dedup(stories)
    assert [s["title"] for s in kept] == ["OpenAI releases GPT-6 model today", "Anthropic ships agent SDK"]


def test_dedup_against_memory(settings):
    storage = Storage(settings)
    memory = HeadlineMemory(storage)
    memory.add("headlines", NOW.date().isoformat(), [_story("Meta open sources Llama 5 weights", url="https://meta.ai/llama5")])
    memory.save(today=NOW.date())

    fresh = HeadlineMemory(storage)
    stories = [
        _story("Meta open sources Llama 5 weights", url="https://other.com/x"),  # title overlap
        _story("Different story", url="https://meta.ai/llama5"),  # url match
        _story("Genuinely new thing happened"),
    ]
    cfg = {"dedup_window_days": 21, "title_overlap_threshold": 0.6}
    kept = dedup_stories(settings, fresh, stories, cfg)
    assert [s["title"] for s in kept] == ["Genuinely new thing happened"]


def test_memory_window_prunes_old_entries(settings):
    storage = Storage(settings)
    memory = HeadlineMemory(storage)
    old_date = (NOW - timedelta(days=30)).date().isoformat()
    memory.add("headlines", old_date, [_story("Ancient news")])
    memory.add("headlines", NOW.date().isoformat(), [_story("Recent news")])
    assert len(memory.recent("headlines", 21, today=NOW.date())) == 1
    memory.save(today=NOW.date())
    reloaded = HeadlineMemory(storage)
    reloaded.load()
    assert len(reloaded.recent("headlines", 45, today=NOW.date())) == 2  # inside hard prune window


def test_merge_llm_brief_keeps_full_story_data_and_applies_allocations():
    stories = [_story("Alpha story"), _story("Beta story"), _story("Gamma story")]
    llm = {
        "stories": [{"title": "Beta story", "time_allocation": 120, "comedy_angle": "lol"}, {"title": "Alpha story", "time_allocation": 60, "comedy_angle": None}],
        "deep_dives": ["Beta story"],
        "cold_open_idea": "irony",
        "story_count": 2,
    }
    brief = _merge_llm_brief(stories, llm)
    titles = [s["title"] for s in brief["stories"]]
    assert titles == ["Beta story", "Alpha story", "Gamma story"]  # dropped stories appended
    assert brief["stories"][0]["time_allocation"] == 120
    assert brief["stories"][0]["url"].startswith("https://")  # full story data preserved
    assert brief["stories"][0]["is_deep_dive"] is True
    assert brief["deep_dives"] == ["Beta story"]
    assert brief["cold_open_idea"] == "irony"


def test_cluster_coverage_counts_distinct_sources():
    from pipeline.stages.curator import cluster_coverage

    stories = [
        {**_story("OpenAI releases GPT-6 model today"), "source_name": "OpenAI"},
        {**_story("OpenAI releases GPT-6 model today, analysts react"), "source_name": "TechCrunch"},
        {**_story("OpenAI releases GPT-6 model today: what it means"), "source_name": "The Verge"},
        {**_story("OpenAI releases GPT-6 model today (duplicate same outlet)"), "source_name": "OpenAI"},
        {**_story("Unrelated robotics paper"), "source_name": "arXiv", "coverage_count": 5},
    ]
    out = {s["source_name"] + s["title"][-6:]: s["coverage_count"] for s in cluster_coverage(stories, 0.6)}
    assert out["OpenAI today"] == 3  # three distinct outlets; the same-outlet duplicate doesn't count
    assert out["TechCrunch react"] >= 2
    assert out["arXiv paper"] == 5  # never lowers an existing (Serper) count


def test_cap_per_source_keeps_order_and_limits_one_outlet():
    from pipeline.stages.curator import cap_per_source

    stories = [{**_story(f"OpenAI post {i}"), "source_name": "OpenAI"} for i in range(6)] + [{**_story("Verge story"), "source_name": "The Verge"}]
    kept = cap_per_source(stories, 3)
    assert [s["title"] for s in kept] == ["OpenAI post 0", "OpenAI post 1", "OpenAI post 2", "Verge story"]
    assert len(cap_per_source(stories, 0)) == 7  # 0 disables

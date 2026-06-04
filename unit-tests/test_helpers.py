"""Unit tests for pure helper functions in app.py."""
import time
import xml.etree.ElementTree as ET

import requests
import pytest


# ---------------------------------------------------------------------------
# truncate_to_words
# ---------------------------------------------------------------------------

def test_truncate_under_limit(app_module):
    assert app_module.truncate_to_words("one two", 5) == "one two"


def test_truncate_exact_limit(app_module):
    assert app_module.truncate_to_words("one two three", 3) == "one two three"


def test_truncate_over_limit(app_module):
    assert app_module.truncate_to_words("one two three four", 2) == "one two..."


def test_truncate_empty_string(app_module):
    assert app_module.truncate_to_words("", 3) == ""


def test_truncate_single_word_over_limit(app_module):
    assert app_module.truncate_to_words("hello", 0) == "..."


# ---------------------------------------------------------------------------
# safe_text
# ---------------------------------------------------------------------------

def test_safe_text_present(app_module):
    assert app_module.safe_text({"title": "x"}, "title") == "x"


def test_safe_text_none_uses_fallback(app_module):
    assert app_module.safe_text({"title": None}, "title", "fallback") == "fallback"


def test_safe_text_missing_key_uses_fallback(app_module):
    assert app_module.safe_text({}, "title", "fallback") == "fallback"


def test_safe_text_empty_string_uses_fallback(app_module):
    assert app_module.safe_text({"title": ""}, "title", "fallback") == "fallback"


def test_safe_text_default_fallback_is_empty_string(app_module):
    assert app_module.safe_text({}, "title") == ""


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_missing_file_returns_defaults(app_module, tmp_path):
    cfg = app_module.load_config(str(tmp_path / "missing.yaml"))
    assert cfg == app_module.DEFAULTS


def test_load_config_valid_yaml_merges_over_defaults(app_module, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("summary_max_words: 9\nlog_level: DEBUG\n", encoding="utf-8")
    cfg = app_module.load_config(str(path))
    assert cfg["summary_max_words"] == 9
    assert cfg["log_level"] == "DEBUG"
    # unspecified keys fall through to defaults
    assert cfg["article_max_paragraphs"] == app_module.DEFAULTS["article_max_paragraphs"]


def test_load_config_empty_yaml_returns_defaults(app_module, tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    cfg = app_module.load_config(str(path))
    assert cfg == app_module.DEFAULTS


def test_load_config_malformed_yaml_returns_defaults(app_module, tmp_path):
    path = tmp_path / "bad.yaml"
    # Valid YAML that parses to a non-dict type will fall through to defaults
    path.write_text("- list\n- item\n", encoding="utf-8")
    cfg = app_module.load_config(str(path))
    assert cfg == app_module.DEFAULTS


# ---------------------------------------------------------------------------
# format_pub_date
# ---------------------------------------------------------------------------

def _make_entry(published_parsed=None, published=None):
    """Build a minimal feedparser-style entry object."""
    d = {}
    if published is not None:
        d["published"] = published
    obj = type("Entry", (dict,), {"__getattr__": dict.get})(d)
    obj.published_parsed = published_parsed
    return obj


def test_format_pub_date_uses_parsed_struct(app_module):
    entry = _make_entry(published_parsed=time.gmtime(0))
    assert app_module.format_pub_date(entry) == "Thu, 01 Jan 1970 00:00:00 GMT"


def test_format_pub_date_known_timestamp(app_module):
    # 2025-06-10 12:34:56 UTC
    ts = 1749555296
    entry = _make_entry(published_parsed=time.gmtime(ts))
    result = app_module.format_pub_date(entry)
    assert "2025" in result
    assert "GMT" in result


def test_format_pub_date_falls_back_to_raw_string(app_module):
    entry = _make_entry(published_parsed=None, published="Tue, 10 Jun 2025 12:34:56 GMT")
    assert app_module.format_pub_date(entry) == "Tue, 10 Jun 2025 12:34:56 GMT"


def test_format_pub_date_returns_none_when_both_missing(app_module):
    entry = _make_entry(published_parsed=None)
    assert app_module.format_pub_date(entry) is None


# ---------------------------------------------------------------------------
# extract_entities
# ---------------------------------------------------------------------------

def _ent(text, label_):
    return type("Ent", (), {"text": text, "label_": label_})()


def test_extract_entities_basic(app_module):
    app_module.nlp.mapping["test"] = [
        _ent("Zelensky", "PERSON"),
        _ent("NATO", "ORG"),
    ]
    assert app_module.extract_entities("test") == [
        "ENTITY:PERSON:Zelensky",
        "ENTITY:ORG:NATO",
    ]


def test_extract_entities_deduplicates(app_module):
    app_module.nlp.mapping["text"] = [
        _ent("NATO", "ORG"),
        _ent("NATO", "ORG"),
    ]
    assert app_module.extract_entities("text") == ["ENTITY:ORG:NATO"]


def test_extract_entities_normalizes_whitespace(app_module):
    app_module.nlp.mapping["text"] = [
        _ent("Volodymyr\n Zelensky", "PERSON"),
    ]
    assert app_module.extract_entities("text") == ["ENTITY:PERSON:Volodymyr Zelensky"]


def test_extract_entities_filters_unlabeled_types(app_module):
    app_module.nlp.mapping["text"] = [
        _ent("42", "CARDINAL"),
        _ent("Pentagon", "FAC"),
    ]
    assert app_module.extract_entities("text") == ["ENTITY:FAC:Pentagon"]


def test_extract_entities_gpe_maps_to_region(app_module):
    app_module.nlp.mapping["text"] = [_ent("Ukraine", "GPE")]
    assert app_module.extract_entities("text") == ["ENTITY:REGION:Ukraine"]


def test_extract_entities_loc_maps_to_region(app_module):
    app_module.nlp.mapping["text"] = [_ent("Black Sea", "LOC")]
    assert app_module.extract_entities("text") == ["ENTITY:REGION:Black Sea"]


def test_extract_entities_empty_text_returns_empty(app_module):
    assert app_module.extract_entities("") == []


def test_extract_entities_returns_empty_on_nlp_exception(app_module):
    app_module.nlp.raise_on_call = True
    assert app_module.extract_entities("anything") == []


def test_extract_entities_skips_blank_entity_text(app_module):
    app_module.nlp.mapping["text"] = [_ent("   ", "PERSON")]
    assert app_module.extract_entities("text") == []


# ---------------------------------------------------------------------------
# fetch_full_text
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, text="", exc=None):
        self.text = text
        self._exc = exc
    def raise_for_status(self):
        if self._exc:
            raise self._exc


def test_fetch_full_text_joins_paragraphs(app_module, monkeypatch):
    html = "<html><body><p>a</p><p>b</p><p>c</p><p>d</p></body></html>"
    monkeypatch.setattr(app_module.requests, "get", lambda *a, **k: _Resp(html))
    app_module.config["article_max_paragraphs"] = 3
    assert app_module.fetch_full_text("http://x") == "a b c"


def test_fetch_full_text_respects_paragraph_cap(app_module, monkeypatch):
    html = "<html><body>" + "".join(f"<p>p{i}</p>" for i in range(10)) + "</body></html>"
    monkeypatch.setattr(app_module.requests, "get", lambda *a, **k: _Resp(html))
    app_module.config["article_max_paragraphs"] = 4
    result = app_module.fetch_full_text("http://x")
    assert result == "p0 p1 p2 p3"


def test_fetch_full_text_returns_empty_on_http_error(app_module, monkeypatch):
    monkeypatch.setattr(app_module.requests, "get",
        lambda *a, **k: _Resp(exc=requests.RequestException("fail")))
    assert app_module.fetch_full_text("http://x") == ""


def test_fetch_full_text_returns_empty_when_no_paragraphs(app_module, monkeypatch):
    html = "<html><body><div>no paragraphs here</div></body></html>"
    monkeypatch.setattr(app_module.requests, "get", lambda *a, **k: _Resp(html))
    assert app_module.fetch_full_text("http://x") == ""


def test_fetch_full_text_returns_empty_on_connection_error(app_module, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(app_module.requests, "get", boom)
    assert app_module.fetch_full_text("http://x") == ""


# ---------------------------------------------------------------------------
# build_rss
# ---------------------------------------------------------------------------

def _feed_obj(title="Feed", link="http://feed", description="desc"):
    f = type("FeedObj", (), {})()
    f.feed = {"title": title, "link": link, "description": description}
    return f


def _entry(title="Item", link="http://item", description="desc",
           published_parsed=None, published=None):
    d = {"title": title, "link": link, "description": description}
    if published is not None:
        d["published"] = published
    e = type("Entry", (dict,), {"__getattr__": dict.get})(d)
    e.published_parsed = published_parsed
    return e


def test_build_rss_channel_fields(app_module):
    feed = _feed_obj(title="My Feed", link="http://f", description="My Desc")
    feed.entries = []
    xml = app_module.build_rss(feed, [])
    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel.findtext("title") == "My Feed"
    assert channel.findtext("link") == "http://f"
    assert channel.findtext("description") == "My Desc"


def test_build_rss_default_channel_title_when_missing(app_module):
    feed = type("F", (), {})()
    feed.feed = {}
    feed.entries = []
    xml = app_module.build_rss(feed, [])
    assert ET.fromstring(xml).find("channel").findtext("title") == "Proxy Feed"


def test_build_rss_item_pubdate_from_parsed(app_module):
    feed = _feed_obj()
    feed.entries = [_entry(published_parsed=time.gmtime(0))]
    xml = app_module.build_rss(feed, [{"tags": [], "summary": None}])
    item = ET.fromstring(xml).find("channel").find("item")
    assert item.findtext("pubDate") == "Thu, 01 Jan 1970 00:00:00 GMT"


def test_build_rss_item_pubdate_from_raw_string(app_module):
    feed = _feed_obj()
    feed.entries = [_entry(published="Tue, 10 Jun 2025 12:34:56 GMT")]
    xml = app_module.build_rss(feed, [{"tags": [], "summary": None}])
    item = ET.fromstring(xml).find("channel").find("item")
    assert item.findtext("pubDate") == "Tue, 10 Jun 2025 12:34:56 GMT"


def test_build_rss_item_pubdate_absent_when_no_date(app_module):
    feed = _feed_obj()
    feed.entries = [_entry()]
    xml = app_module.build_rss(feed, [{"tags": [], "summary": None}])
    item = ET.fromstring(xml).find("channel").find("item")
    assert item.find("pubDate") is None


def test_build_rss_category_tags(app_module):
    feed = _feed_obj()
    feed.entries = [_entry()]
    xml = app_module.build_rss(feed, [{"tags": ["ENTITY:PERSON:Alice", "ENTITY:ORG:NATO"], "summary": None}])
    item = ET.fromstring(xml).find("channel").find("item")
    cats = [c.text for c in item.findall("category")]
    assert cats == ["ENTITY:PERSON:Alice", "ENTITY:ORG:NATO"]


def test_build_rss_sw_summary_written_with_correct_namespace(app_module):
    feed = _feed_obj()
    feed.entries = [_entry()]
    xml = app_module.build_rss(feed, [{"tags": [], "summary": "short summary text"}])
    item = ET.fromstring(xml).find("channel").find("item")
    summary = item.find("{urn:signalweave:ns}summary")
    assert summary is not None
    assert summary.text == "short summary text"


def test_build_rss_sw_summary_omitted_when_none(app_module):
    feed = _feed_obj()
    feed.entries = [_entry()]
    xml = app_module.build_rss(feed, [{"tags": [], "summary": None}])
    item = ET.fromstring(xml).find("channel").find("item")
    assert item.find("{urn:signalweave:ns}summary") is None


def test_build_rss_multiple_entries(app_module):
    feed = _feed_obj()
    feed.entries = [_entry(title="A"), _entry(title="B")]
    xml = app_module.build_rss(feed, [
        {"tags": ["ENTITY:ORG:NATO"], "summary": "s1"},
        {"tags": [], "summary": None},
    ])
    items = ET.fromstring(xml).find("channel").findall("item")
    assert len(items) == 2
    assert items[0].findtext("title") == "A"
    assert items[1].findtext("title") == "B"

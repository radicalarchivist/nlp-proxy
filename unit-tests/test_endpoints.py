"""Endpoint tests for /health and /feed via FastAPI TestClient."""
from types import SimpleNamespace
import time
import xml.etree.ElementTree as ET

import pytest


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_returns_500_when_nlp_raises(client, app_module):
    app_module.nlp.raise_on_call = True
    resp = client.get("/health")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "NLP engine unavailable"


# ---------------------------------------------------------------------------
# /feed — error paths
# ---------------------------------------------------------------------------

def test_feed_missing_url_param_returns_422(client):
    resp = client.get("/feed")
    assert resp.status_code == 422


def test_feed_returns_502_when_feedparser_raises(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: (_ for _ in ()).throw(RuntimeError("parse fail")))
    # RuntimeError from feedparser.parse triggers the except block
    def boom(url):
        raise RuntimeError("parse fail")
    monkeypatch.setattr(app_module.feedparser, "parse", boom)
    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 502
    assert "Failed to fetch feed" in resp.json()["detail"]


def test_feed_returns_502_on_bozo_with_no_entries(client, app_module, monkeypatch):
    fake = SimpleNamespace(
        bozo=True,
        bozo_exception=RuntimeError("bad xml"),
        entries=[],
        feed={"title": "Bad"},
    )
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: fake)
    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 502
    assert "Failed to parse feed" in resp.json()["detail"]


def test_feed_accepts_bozo_feed_when_entries_present(client, app_module, monkeypatch):
    """bozo=True is not fatal if entries are present (malformed but parseable feeds)."""
    Entry = type("Entry", (dict,), {"__getattr__": dict.get})
    e = Entry({"title": "item", "description": "d"})
    e.published_parsed = None
    fake = SimpleNamespace(bozo=True, bozo_exception=None, entries=[e], feed={"title": "F", "link": "", "description": ""})
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: fake)
    monkeypatch.setattr(app_module, "fetch_full_text", lambda url: "")
    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /feed — happy path
# ---------------------------------------------------------------------------

def _ent(text, label_):
    return type("Ent", (), {"text": text, "label_": label_})()


def _make_feed(entries, title="My Feed", link="http://feed", description="desc"):
    f = SimpleNamespace(
        bozo=False,
        entries=entries,
        feed={"title": title, "link": link, "description": description},
    )
    return f


def _make_entry(title="Article", link="http://item", description="desc",
                published_parsed=None, published=None):
    d = {"title": title, "link": link, "description": description}
    if published is not None:
        d["published"] = published
    e = type("Entry", (dict,), {"__getattr__": dict.get})(d)
    e.published_parsed = published_parsed
    return e


def test_feed_full_round_trip(client, app_module, monkeypatch):
    article_text = "Volodymyr Zelensky met NATO leaders in Ukraine today"
    entry = _make_entry(
        title="Ukraine update",
        link="http://news/item1",
        published_parsed=time.gmtime(0),
    )
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: _make_feed([entry]))
    monkeypatch.setattr(app_module, "fetch_full_text", lambda url: article_text)
    app_module.nlp.mapping[article_text] = [
        _ent("Volodymyr Zelensky", "PERSON"),
        _ent("NATO", "ORG"),
        _ent("Ukraine", "GPE"),
    ]
    app_module.config["summary_max_words"] = 5

    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/rss+xml")

    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    assert channel.findtext("title") == "My Feed"

    item = channel.find("item")
    assert item.findtext("title") == "Ukraine update"
    assert item.findtext("pubDate") == "Thu, 01 Jan 1970 00:00:00 GMT"

    cats = [c.text for c in item.findall("category")]
    assert cats == ["ENTITY:PERSON:Volodymyr Zelensky", "ENTITY:ORG:NATO", "ENTITY:REGION:Ukraine"]

    summary = item.find("{urn:signalweave:ns}summary")
    assert summary is not None
    assert summary.text.endswith("...")
    assert len(summary.text.split()) <= 6  # 5 words + "..."


def test_feed_falls_back_to_title_when_no_link(client, app_module, monkeypatch):
    entry = _make_entry(title="NATO in Brussels", link=None)
    entry["link"] = None
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: _make_feed([entry]))
    app_module.nlp.mapping["NATO in Brussels"] = [
        _ent("NATO", "ORG"),
        _ent("Brussels", "GPE"),
    ]

    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200

    item = ET.fromstring(resp.content).find("channel").find("item")
    cats = [c.text for c in item.findall("category")]
    assert cats == ["ENTITY:ORG:NATO", "ENTITY:REGION:Brussels"]
    # No article fetched, so no summary
    assert item.find("{urn:signalweave:ns}summary") is None


def test_feed_falls_back_to_title_when_article_fetch_returns_empty(client, app_module, monkeypatch):
    entry = _make_entry(title="Pentagon statement", link="http://news/item1")
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: _make_feed([entry]))
    monkeypatch.setattr(app_module, "fetch_full_text", lambda url: "")
    app_module.nlp.mapping["Pentagon statement"] = [_ent("Pentagon", "FAC")]

    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200

    item = ET.fromstring(resp.content).find("channel").find("item")
    cats = [c.text for c in item.findall("category")]
    assert cats == ["ENTITY:FAC:Pentagon"]
    assert item.find("{urn:signalweave:ns}summary") is None


def test_feed_response_is_valid_xml(client, app_module, monkeypatch):
    entry = _make_entry(title="Generic item")
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: _make_feed([entry]))
    monkeypatch.setattr(app_module, "fetch_full_text", lambda url: "")

    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200
    # ET.fromstring raises if content is not valid XML
    root = ET.fromstring(resp.content)
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"


def test_feed_multiple_entries_all_enriched(client, app_module, monkeypatch):
    entries = [
        _make_entry(title=f"Article {i}", link=f"http://news/{i}")
        for i in range(3)
    ]
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: _make_feed(entries))
    monkeypatch.setattr(app_module, "fetch_full_text", lambda url: "some text")
    app_module.nlp.mapping["some text"] = [_ent("NATO", "ORG")]

    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200
    items = ET.fromstring(resp.content).find("channel").findall("item")
    assert len(items) == 3
    for item in items:
        cats = [c.text for c in item.findall("category")]
        assert cats == ["ENTITY:ORG:NATO"]


def test_feed_returns_xml_declaration(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module.feedparser, "parse", lambda url: _make_feed([]))
    resp = client.get("/feed", params={"url": "http://example.com/feed.xml"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"<?xml")

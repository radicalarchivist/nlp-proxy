import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from datetime import datetime
import xml.etree.ElementTree as ET

import yaml
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import Response
import feedparser
import requests
from bs4 import BeautifulSoup
import spacy

# ----------------------
# Config
# ----------------------
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")

DEFAULTS = {
    "entity_types": ["PERSON", "ORG", "GPE", "LOC", "NORP", "EVENT", "LAW", "FAC"],
    "summary_max_words": 200,
    "article_max_paragraphs": 20,
    "log_level": "INFO",
}

def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return {**DEFAULTS, **data}
    except FileNotFoundError:
        return DEFAULTS.copy()
    except Exception as e:
        print(f"[WARN] Failed to load config from {path}: {e} — using defaults")
        return DEFAULTS.copy()

config = load_config(CONFIG_PATH)

# ----------------------
# Logging
# ----------------------
LOG_DIR = "/var/log/nlp-proxy"
LOG_FILE = os.path.join(LOG_DIR, "nlp-proxy.log")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("nlp-proxy")
logger.setLevel(getattr(logging, config["log_level"].upper(), logging.INFO))

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
stream_handler.setFormatter(fmt)

file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(fmt)

logger.addHandler(stream_handler)
logger.addHandler(file_handler)

# ----------------------
# XML Namespace
# ----------------------
# URN-based namespace — no external domain, self-contained identifier (D011)
SW_NS = "urn:signalweave:ns"
ET.register_namespace("sw", SW_NS)

# ----------------------
# Tag Schema (D007-D009)
# ----------------------
LABEL_MAP = {
    "PERSON": "ENTITY:PERSON",
    "ORG":    "ENTITY:ORG",
    "GPE":    "ENTITY:REGION",
    "LOC":    "ENTITY:REGION",
    "NORP":   "ENTITY:GROUP",
    "EVENT":  "ENTITY:EVENT",
    "LAW":    "ENTITY:LAW",
    "FAC":    "ENTITY:FAC",
}

ALLOWED_LABELS = set(config["entity_types"])

# ----------------------
# HTTP Headers
# ----------------------
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ----------------------
# NLP Model
# ----------------------
try:
    nlp = spacy.load("en_core_web_sm")
    logger.info("Loaded spaCy model: en_core_web_sm")
except Exception as e:
    logger.error(f"Failed to load spaCy model: {e}")
    raise

# ----------------------
# FastAPI App
# ----------------------
app = FastAPI(title="SignalWeave NLP Proxy")


def fetch_full_text(url: str) -> str:
    """Fetch article body text up to article_max_paragraphs <p> tags."""
    max_paragraphs = config["article_max_paragraphs"]
    try:
        logger.debug(f"Fetching article text from {url}")
        r = requests.get(url, timeout=10, headers=REQUEST_HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        # Join with space to prevent cross-paragraph entity boundary splits
        paragraphs = [p.get_text() for p in soup.find_all("p")[:max_paragraphs]]
        text = " ".join(paragraphs)
        logger.debug(f"Extracted {len(text)} chars from {url}")
        return text
    except Exception as e:
        logger.warning(f"Failed to fetch/parse full text for {url}: {e}")
        return ""


def truncate_to_words(text: str, max_words: int) -> str:
    """Truncate text at a word boundary to max_words words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def extract_entities(text: str) -> list[str]:
    """Extract NER entities, filter to allowlist, emit as ENTITY:TYPE:Value tags."""
    tags = []
    seen = set()
    try:
        doc = nlp(text)
        for ent in doc.ents:
            if ent.label_ not in ALLOWED_LABELS:
                continue
            prefix = LABEL_MAP.get(ent.label_)
            if not prefix:
                continue
            # Normalize whitespace in entity text to prevent embedded newlines
            value = " ".join(ent.text.split())
            if not value:
                continue
            tag = f"{prefix}:{value}"
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
        logger.debug(f"Extracted {len(tags)} entity tags")
    except Exception as e:
        logger.error(f"Entity extraction failed: {e}")
    return tags


def safe_text(entry: dict, key: str, fallback: str = "") -> str:
    """Return entry field as string, treating None as fallback."""
    return entry.get(key) or fallback


def format_pub_date(entry) -> str | None:
    """Return RFC 2822 pubDate string from feedparser entry, or None."""
    # feedparser provides published_parsed as a time.struct_time in UTC
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        import email.utils
        import calendar
        ts = calendar.timegm(entry.published_parsed)
        return email.utils.formatdate(ts, usegmt=True)
    # Fall back to the raw string if present
    return entry.get("published") or None


def build_rss(feed, enriched_entries) -> bytes:
    """Rebuild RSS with structured entity tags, word-limited sw:summary, and pubDate."""
    SW_SUMMARY = f"{{{SW_NS}}}summary"

    rss = ET.Element("rss", attrib={"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = safe_text(feed.feed, "title", "Proxy Feed")
    ET.SubElement(channel, "link").text = safe_text(feed.feed, "link")
    ET.SubElement(channel, "description").text = safe_text(feed.feed, "description")

    for entry, enrich in zip(feed.entries, enriched_entries):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = safe_text(entry, "title")
        ET.SubElement(item, "link").text = safe_text(entry, "link")
        ET.SubElement(item, "description").text = safe_text(entry, "description")
        pub_date = format_pub_date(entry)
        if pub_date:
            ET.SubElement(item, "pubDate").text = pub_date
        for tag in enrich.get("tags", []):
            ET.SubElement(item, "category").text = tag
        if enrich.get("summary"):
            ET.SubElement(item, SW_SUMMARY).text = enrich["summary"]

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


# ----------------------
# Middleware & Routes
# ----------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.utcnow()
    logger.info(f"Incoming request: {request.url}")
    response = await call_next(request)
    duration = (datetime.utcnow() - start_time).total_seconds()
    logger.info(f"Completed {request.url} — {response.status_code} in {duration:.2f}s")
    return response


@app.get("/feed")
def proxy_feed(url: str = Query(..., description="Original RSS feed URL")):
    """Fetch, enrich, and return a structured RSS feed."""
    logger.info(f"Proxying feed: {url}")
    try:
        # Use feedparser directly with URL for correct encoding detection
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error(f"Failed to fetch/parse feed {url}: {e}")
        raise HTTPException(502, f"Failed to fetch feed: {url}")

    if feed.bozo and not feed.entries:
        logger.error(f"feedparser bozo error for {url}: {feed.bozo_exception}")
        raise HTTPException(502, f"Failed to parse feed: {url}")

    logger.info(f"Parsed: {feed.feed.get('title', 'Unknown')} ({len(feed.entries)} entries)")

    max_words = config["summary_max_words"]
    enriched_entries = []

    for entry in feed.entries:
        text = ""
        if entry.get("link"):
            text = fetch_full_text(entry["link"])
        source_text = text or safe_text(entry, "title")
        tags = extract_entities(source_text)
        summary = truncate_to_words(text, max_words) if text else None
        enriched_entries.append({"tags": tags, "summary": summary})

    rss_xml = build_rss(feed, enriched_entries)
    logger.info(f"Enriched feed built for {url}")
    return Response(content=rss_xml, media_type="application/rss+xml")


@app.get("/health")
def healthcheck():
    """Liveness check — verifies spaCy model is loaded and responsive."""
    try:
        _ = nlp("Healthcheck test")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Healthcheck failed: {e}")
        raise HTTPException(500, "NLP engine unavailable")

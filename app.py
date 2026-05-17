import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from datetime import datetime

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
stream_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
stream_handler.setFormatter(stream_fmt)

file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(file_fmt)

logger.addHandler(stream_handler)
logger.addHandler(file_handler)

# ----------------------
# Tag Schema
# ----------------------
# Maps spaCy NER labels to structured ENTITY:TYPE tag prefixes.
# GPE and LOC both normalize to ENTITY:REGION (geopolitically equivalent).
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
    """Fetch article text up to article_max_paragraphs <p> tags."""
    max_paragraphs = config["article_max_paragraphs"]
    try:
        logger.debug(f"Fetching article text from {url}")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        paragraphs = [p.get_text() for p in soup.find_all("p")[:max_paragraphs]]
        text = "\n".join(paragraphs)
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
            value = ent.text.strip()
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


def build_rss(feed, enriched_entries):
    """Rebuild RSS with structured entity tags and word-limited summaries."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    # URN-based namespace — no external domain, self-contained identifier
    SW_NS = "urn:signalweave:ns"

    rss = Element("rss", version="2.0", attrib={"xmlns:sw": SW_NS})
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = feed.feed.get("title", "Proxy Feed")
    SubElement(channel, "link").text = feed.feed.get("link", "")
    SubElement(channel, "description").text = feed.feed.get("description", "")

    for entry, enrich in zip(feed.entries, enriched_entries):
        item = SubElement(channel, "item")
        SubElement(item, "title").text = entry.get("title", "")
        SubElement(item, "link").text = entry.get("link", "")
        SubElement(item, "description").text = entry.get("description", "")
        for tag in enrich.get("tags", []):
            SubElement(item, "category").text = tag
        if enrich.get("summary"):
            SubElement(item, "sw:summary").text = enrich["summary"]

    return tostring(rss, encoding="utf-8", xml_declaration=True)


# ----------------------
# Middleware & Routes
# ----------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.utcnow()
    logger.info(f"Incoming request: {request.url}")
    response = await call_next(request)
    duration = (datetime.utcnow() - start_time).total_seconds()
    logger.info(
        f"Completed {request.url} in {duration:.2f}s with status {response.status_code}"
    )
    return response


@app.get("/feed")
def proxy_feed(url: str = Query(..., description="Original RSS feed URL")):
    """Fetch feed, enrich with structured NLP tags and summary, return modified RSS."""
    logger.info(f"Proxying feed: {url}")
    try:
        raw = requests.get(url, timeout=10)
        raw.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch feed {url}: {e}")
        raise HTTPException(502, f"Failed to fetch feed: {url}")

    feed = feedparser.parse(raw.content)
    logger.info(f"Parsed feed: {feed.feed.get('title', 'Unknown')} ({len(feed.entries)} entries)")

    max_words = config["summary_max_words"]
    enriched_entries = []

    for entry in feed.entries:
        text = ""
        if "link" in entry:
            text = fetch_full_text(entry["link"])
        source_text = text or entry.get("title", "")
        tags = extract_entities(source_text)
        summary = truncate_to_words(text, max_words) if text else None
        enriched_entries.append({"tags": tags, "summary": summary})

    rss_xml = build_rss(feed, enriched_entries)
    logger.info(f"Built enriched feed for {url}")
    return Response(content=rss_xml, media_type="application/rss+xml")


@app.get("/health")
def healthcheck():
    """Simple healthcheck endpoint."""
    try:
        _ = nlp("Healthcheck test")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Healthcheck failed: {e}")
        raise HTTPException(500, "NLP engine unavailable")

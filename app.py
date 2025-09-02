import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from datetime import datetime

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import Response
import feedparser
import requests
from bs4 import BeautifulSoup
import spacy

# ----------------------
# Logging Configuration
# ----------------------
LOG_DIR = "/var/log/nlp-proxy"
LOG_FILE = os.path.join(LOG_DIR, "nlp-proxy.log")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("nlp-proxy")
logger.setLevel(logging.INFO)

# Stream handler (stdout → Docker/Dozzle)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)
stream_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
stream_handler.setFormatter(stream_fmt)

# Rotating file handler (5 MB x 5 backups)
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)  # more verbose in file logs
file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(file_fmt)

logger.addHandler(stream_handler)
logger.addHandler(file_handler)

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
    """Fetch and extract main article text (simple)."""
    try:
        logger.debug(f"Fetching article text from {url}")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        paragraphs = [p.get_text() for p in soup.find_all("p")]
        text = "\n".join(paragraphs[:10])
        logger.debug(f"Extracted {len(text)} chars of text from {url}")
        return text
    except Exception as e:
        logger.warning(f"Failed to fetch/parse full text for {url}: {e}")
        return ""

def extract_entities(text: str) -> list[str]:
    """Use spaCy NER to extract entities."""
    tags = []
    try:
        doc = nlp(text)
        for ent in doc.ents:
            clean = ent.text.strip()
            if clean and clean not in tags:
                tags.append(clean)
        logger.debug(f"Extracted entities: {tags}")
    except Exception as e:
        logger.error(f"Entity extraction failed: {e}")
    return tags

def build_rss(feed, enriched_entries):
    """Rebuild RSS with added tags and summaries."""
    from xml.etree.ElementTree import Element, SubElement, tostring
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = feed.feed.get("title", "Proxy Feed")
    SubElement(channel, "link").text = feed.feed.get("link", "")
    SubElement(channel, "description").text = feed.feed.get("description", "")

    for entry, enrich in zip(feed.entries, enriched_entries):
        item = SubElement(channel, "item")
        SubElement(item, "title").text = entry.get("title", "")
        SubElement(item, "link").text = entry.get("link", "")
        SubElement(item, "description").text = entry.get("description", "")
        # Add tags
        for tag in enrich.get("tags", []):
            SubElement(item, "category").text = tag
        # Add summary
        if enrich.get("summary"):
            SubElement(item, "sw:summary").text = enrich["summary"]

    return tostring(rss, encoding="utf-8")

# ----------------------
# Middleware & Routes
# ----------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log incoming requests and response times."""
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
    """Fetch feed, enrich with NLP, return modified RSS."""
    logger.info(f"Proxying feed: {url}")
    try:
        raw = requests.get(url, timeout=10)
        raw.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch feed {url}: {e}")
        raise HTTPException(502, f"Failed to fetch feed: {url}")

    feed = feedparser.parse(raw.content)
    logger.info(f"Parsed feed: {feed.feed.get('title', 'Unknown')} ({len(feed.entries)} entries)")

    enriched_entries = []
    for entry in feed.entries:
        text = ""
        if "link" in entry:
            text = fetch_full_text(entry["link"])
        tags = extract_entities(text or entry.get("title", ""))
        enriched_entries.append({
            "tags": tags,
            "summary": text[:280] + "..." if text else None
        })

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

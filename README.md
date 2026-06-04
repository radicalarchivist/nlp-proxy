# SignalWeave NLP Proxy

A self-hosted FastAPI RSS proxy that enriches feed entries with NLP-extracted named entities and article summaries. All processing happens on-host — no data leaves your machine.

---

## Features

- Named entity extraction via **spaCy** `en_core_web_sm`
- Structured `ENTITY:TYPE:Value` tag schema in `<category>` elements
- `pubDate` passthrough — original publish timestamps preserved on enriched items
- Word-limited article summaries in `<sw:summary>` (custom namespace)
- Fully configurable via `config.yaml` at container startup
- Rotating file logs + stdout logging
- `/health` endpoint for Docker healthchecks
- Fully Dockerized

---

## Tag Schema

Entities are emitted as RSS `<category>` tags in `ENTITY:TYPE:Value` format:

| spaCy Label | Tag Prefix | Example |
|---|---|---|
| PERSON | `ENTITY:PERSON` | `ENTITY:PERSON:Zelensky` |
| ORG | `ENTITY:ORG` | `ENTITY:ORG:NATO` |
| GPE | `ENTITY:REGION` | `ENTITY:REGION:Ukraine` |
| LOC | `ENTITY:REGION` | `ENTITY:REGION:Black Sea` |
| NORP | `ENTITY:GROUP` | `ENTITY:GROUP:Russians` |
| EVENT | `ENTITY:EVENT` | `ENTITY:EVENT:World War II` |
| LAW | `ENTITY:LAW` | `ENTITY:LAW:Article 5` |
| FAC | `ENTITY:FAC` | `ENTITY:FAC:Pentagon` |

---

## Configuration (`config.yaml`)

```yaml
# Entity types to extract. Must be valid spaCy NER labels from the allowlist.
entity_types:
  - PERSON
  - ORG
  - GPE
  - LOC
  - NORP
  - EVENT
  - LAW
  - FAC

# Maximum words in article summary (truncated at word boundary)
summary_max_words: 200

# Maximum <p> tags to fetch per article before truncation
article_max_paragraphs: 20

# Log level: DEBUG, INFO, WARNING, ERROR
log_level: INFO
```

---

## Deployment

### Docker Compose (recommended)

Run from within the `nlp-proxy/` directory:

```bash
git clone https://github.com/radicalarchivist/nlp-proxy.git
cd nlp-proxy
docker compose up -d --build
```

This creates the `nlp-proxy_default` Docker network that the TTRSS plugin joins via `docker-compose.override.yml`. Config is mounted read-only from `./config.yaml`. Edit and restart to apply changes.

### Standalone Docker

```bash
docker build -t nlp-proxy .
docker run -d \
  -p 5000:5000 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/nlp-logs:/var/log/nlp-proxy \
  --name nlp-proxy \
  nlp-proxy
```

---

## Usage

Pass any RSS feed URL as the `url` query parameter:

```
http://localhost:5000/feed?url=https://feeds.bbci.co.uk/news/rss.xml
```

The proxy returns a valid RSS feed with enriched `<category>` tags, `<pubDate>`, and `<sw:summary>` elements appended to each `<item>`.

### Health check

```
http://localhost:5000/health
```

Returns `{"status": "ok"}` when the NLP engine is loaded and ready.

---

## TTRSS Integration

In Tiny Tiny RSS, add a new feed with the proxy URL:

```
http://<your-server-ip>:5000/feed?url=<encoded-feed-url>
```

Example:
```
http://192.168.1.10:5000/feed?url=https%3A%2F%2Ffeeds.bbci.co.uk%2Fnews%2Frss.xml
```

URL-encode the original feed URL before passing it as the `url` parameter. TTRSS will poll the proxy on its normal schedule and receive enriched entries.

---

## Namespace

The `sw:summary` element uses the `urn:signalweave:ns` URN namespace. This is a local identifier with no external endpoint — consistent with SignalWeaver's data-local design principle.

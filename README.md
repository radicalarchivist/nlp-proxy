# SignalWeave NLP Proxy

A FastAPI-based RSS proxy that enriches feed entries with NLP-extracted entities and summaries. Designed for flexible configuration, real-time logging, and Docker deployment.

---

## Features

- Extract named entities (PERSON, ORG, GPE, LOC, etc.) using **spaCy**.
- Generate short summaries of feed content, configurable in length.
- Configurable via a YAML file at container startup (`config.yaml`).
- Live logging to stdout and rotating file logs.
- `/health` endpoint for easy health checks.
- Fully Dockerized with `Dockerfile` and optional `docker-compose` setup.

---

## ⚙️ Configuration (`config.yaml`)

```yaml
entity_types:
  - PERSON
  - ORG
  - GPE
  - LOC

summary_max_chars: 280
log_level: INFO


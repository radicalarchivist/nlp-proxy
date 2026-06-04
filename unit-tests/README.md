# unit-tests

Pytest suite for `nlp-proxy`. Covers all helper functions and both HTTP endpoints.

## Requirements

```bash
pip install pytest httpx
```

(All runtime dependencies from `requirements.txt` must also be installed.)

## Running

Run from the **repo root** (`nlp-proxy/`):

```bash
pytest unit-tests -v
```

## Design notes

- `conftest.py` patches `spacy.load` **before** importing `app.py`, so the real
  `en_core_web_sm` model is never loaded during tests. Fast cold starts.
- A `FakeNLP` instance replaces `app.nlp` in every test. Set
  `fake_nlp.mapping[text] = [...]` to control entity extraction results.
  Set `fake_nlp.raise_on_call = True` to simulate NLP engine failure.
- Endpoint tests use FastAPI `TestClient` (synchronous).
- All external I/O (`requests.get`, `feedparser.parse`, `fetch_full_text`) is
  monkeypatched per test — no network required.
- `tmp_path` fixture provides an isolated config directory per test invocation.

## Coverage map

| Module area | Tests in |
|---|---|
| `truncate_to_words` | `test_helpers.py` |
| `safe_text` | `test_helpers.py` |
| `load_config` | `test_helpers.py` |
| `format_pub_date` | `test_helpers.py` |
| `extract_entities` | `test_helpers.py` |
| `fetch_full_text` | `test_helpers.py` |
| `build_rss` | `test_helpers.py` |
| `GET /health` | `test_endpoints.py` |
| `GET /feed` | `test_endpoints.py` |

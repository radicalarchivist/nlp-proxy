import importlib
import sys
import types
from pathlib import Path

import pytest


class FakeEnt:
    def __init__(self, text, label_):
        self.text = text
        self.label_ = label_


class FakeDoc:
    def __init__(self, ents=None):
        self.ents = ents or []


class FakeNLP:
    def __init__(self):
        self.raise_on_call = False
        self.mapping = {}

    def __call__(self, text):
        if self.raise_on_call:
            raise RuntimeError("fake nlp failure")
        return FakeDoc(self.mapping.get(text, []))


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    # Point config at a temp dir so no real /app/config.yaml is needed
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.yaml"))

    # Patch spacy.load before importing app so the real model is never loaded
    import spacy
    fake_nlp = FakeNLP()
    monkeypatch.setattr(spacy, "load", lambda name: fake_nlp)

    # Ensure a clean import every fixture invocation
    if "app" in sys.modules:
        del sys.modules["app"]

    # Add repo root to sys.path so `import app` resolves
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    app_mod = importlib.import_module("app")

    # Silence logging handlers added at import time
    app_mod.logger.handlers.clear()

    # Wire the fake NLP instance into the module
    app_mod.nlp = fake_nlp

    # Predictable config for all tests
    app_mod.config = {
        "entity_types": ["PERSON", "ORG", "GPE", "LOC", "NORP", "EVENT", "LAW", "FAC"],
        "summary_max_words": 5,
        "article_max_paragraphs": 3,
        "log_level": "INFO",
    }
    app_mod.ALLOWED_LABELS = set(app_mod.config["entity_types"])

    return app_mod


@pytest.fixture
def client(app_module):
    from fastapi.testclient import TestClient
    return TestClient(app_module.app)

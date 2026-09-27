"""Test setup: give the app a fake key BEFORE it's imported, and a fake Claude client.

No real Anthropic calls are made anywhere in this suite — the client is monkeypatched — so
the tests run with no API key and cost nothing. What they CAN'T prove is live generation
quality; that needs the real key (run one live call after inserting it).
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# app.py reads ANTHROPIC_API_KEY at import time — set it before importing anything.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeMessages:
    """Stands in for client.messages. Records the last create() call and returns a canned reply."""
    def __init__(self):
        self.calls = []
        self.next_text = "Sure — how can I help?"
        self.raise_exc = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.next_text)])


@pytest.fixture
def app_and_fake(monkeypatch):
    import app as app_module
    fake = FakeMessages()
    monkeypatch.setattr(app_module.client, "messages", fake)
    app_module._hits.clear()  # reset rate-limit state between tests
    from fastapi.testclient import TestClient
    return app_module, fake, TestClient(app_module.app)

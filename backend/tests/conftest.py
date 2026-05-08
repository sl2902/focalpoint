"""
Shared pytest fixtures for backend tests.

Overrides that isolate tests from the local .env so the test suite
passes regardless of which inference backend is configured.
"""

import pytest


@pytest.fixture(autouse=True)
def disable_ollama(monkeypatch):
    """Force OLLAMA_ENABLED=False for every test.

    Tests that explicitly exercise the Ollama path can override this by
    calling monkeypatch.setattr directly inside the test body.
    """
    import backend.processors.gemma_client as gmc
    monkeypatch.setattr(gmc.settings, "OLLAMA_ENABLED", False)

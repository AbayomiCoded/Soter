"""
Startup validation tests (issue #987).

Verifies that an invalid configuration aborts the FastAPI lifespan so the
service never starts serving traffic, and that a valid configuration boots.
"""

import pytest
from fastapi.testclient import TestClient

import config
import main
from config import ConfigurationError


def test_invalid_configuration_prevents_startup(monkeypatch):
    def _raise(self):
        raise ConfigurationError(
            "Invalid configuration:\n  - REDIS_URL: must use redis:// scheme"
        )

    # Patch on the class: pydantic instances reject non-field attributes.
    monkeypatch.setattr(config.Settings, "validate_configuration", _raise)

    with pytest.raises(ConfigurationError):
        with TestClient(main.app):
            pass  # lifespan raises before the app can serve any request


def test_valid_configuration_boots_and_serves():
    with TestClient(main.app) as client:
        response = client.get("/health")
        assert response.status_code == 200

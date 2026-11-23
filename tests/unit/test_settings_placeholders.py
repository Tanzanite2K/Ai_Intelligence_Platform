"""Placeholder credentials (no letter or digit) count as "not configured", like blank ones."""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.extraction.providers.vendors import build_providers


@pytest.mark.parametrize("placeholder", ["", "  ", "-", "#", "*", "--", "''"])
def test_punctuation_only_credentials_are_unset(placeholder: str) -> None:
    s = Settings(_env_file=None, groq_api_key=placeholder, deepseek_api_key=placeholder, github_token=placeholder,  # type: ignore[call-arg]
                 neo4j_password=placeholder, redis_url=placeholder)
    assert s.groq_api_key is None and s.deepseek_api_key is None and s.github_token is None
    assert s.neo4j_password is None and s.redis_url is None
    configured = {p.name for p in build_providers(s) if p.configured}
    assert "groq" not in configured and "deepseek" not in configured


def test_real_looking_values_are_kept() -> None:
    s = Settings(_env_file=None, groq_api_key="gsk_example123", github_token="ghp_x1")  # type: ignore[call-arg]
    assert s.groq_api_key is not None and s.groq_api_key.get_secret_value() == "gsk_example123"
    assert s.github_token is not None

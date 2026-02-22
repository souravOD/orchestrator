"""
Tests for orchestrator.config
==============================

Validates Pydantic Settings loads correctly from env vars
with proper defaults.
"""

import os
import pytest


class TestConfig:
    def test_settings_load(self, mock_env):
        """Settings should load from environment variables."""
        # Re-create settings to pick up monkeypatched env
        from pydantic_settings import BaseSettings
        from orchestrator.config import OrchestratorSettings

        s = OrchestratorSettings()
        assert s.supabase_url == "https://test-project.supabase.co"
        assert s.supabase_service_role_key == "test-key-1234567890"
        assert s.openai_api_key == "sk-test-key"

    def test_defaults(self, mock_env):
        from orchestrator.config import OrchestratorSettings

        s = OrchestratorSettings()
        assert s.openai_model_name == "gpt-4o-mini"
        assert s.orchestrator_log_level == "DEBUG"  # overridden by mock_env
        assert s.orchestrator_default_batch_size == 50  # overridden
        assert s.orchestrator_max_retries == 2  # overridden
        assert s.webhook_host == "0.0.0.0"
        assert s.webhook_port == 8100
        assert s.webhook_secret == "test-secret"

    def test_optional_fields_can_be_none(self, monkeypatch):
        """Optional fields like openai_api_key should default to None."""
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "key")
        # Don't set OPENAI_API_KEY
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from orchestrator.config import OrchestratorSettings
        s = OrchestratorSettings()
        assert s.openai_api_key is None
        assert s.openai_base_url is None

    def test_extra_env_vars_ignored(self, mock_env, monkeypatch):
        """Extra environment variables should be ignored (extra='ignore')."""
        monkeypatch.setenv("SOME_RANDOM_VAR", "value")
        from orchestrator.config import OrchestratorSettings
        s = OrchestratorSettings()  # Should not raise
        assert s.supabase_url == "https://test-project.supabase.co"

"""
Tests for orchestrator.triggers
================================

Tests webhook event handling and schedule registration logic,
with all DB and flow calls mocked.
"""

import pytest
from unittest.mock import patch, MagicMock

from orchestrator.triggers import handle_webhook_event


class TestHandleWebhookEvent:
    def test_malformed_payload(self):
        """Missing type or table should return skipped."""
        result = handle_webhook_event({"type": "", "table": ""})
        assert result.get("skipped") is True
        assert "error" in result or "skipped" in result

    @patch("orchestrator.triggers.db.list_active_event_triggers")
    @patch("orchestrator.flows.realtime_event_flow")
    def test_default_routing_bronze_insert(self, mock_flow, mock_triggers):
        """INSERT on bronze.raw_products → default routing to bronze_to_silver."""
        mock_triggers.return_value = []  # No explicit triggers
        mock_flow.return_value = {"completed": True}

        result = handle_webhook_event({
            "type": "INSERT",
            "table": "raw_products",
            "schema": "bronze",
            "record": {"id": "123"},
        })

        mock_flow.assert_called_once()
        call_kwargs = mock_flow.call_args
        assert call_kwargs[1]["source_schema"] == "bronze" or call_kwargs[0][1] == "bronze"

    @patch("orchestrator.triggers.db.list_active_event_triggers")
    @patch("orchestrator.flows.FLOW_REGISTRY", {"single_layer": MagicMock(return_value={"ok": True})})
    def test_explicit_trigger_match(self, mock_triggers):
        """When an explicit trigger matches, dispatch to its configured flow."""
        mock_triggers.return_value = [{
            "trigger_name": "bronze_product_insert",
            "event_type": "supabase_insert",
            "source_schema": "bronze",
            "source_table": "raw_products",
            "flow_name": "single_layer",
            "filter_config": {"layer": "bronze_to_silver"},
        }]

        result = handle_webhook_event({
            "type": "INSERT",
            "table": "raw_products",
            "schema": "bronze",
            "record": {"id": "456"},
        })

        assert result is not None

    @patch("orchestrator.triggers.db.list_active_event_triggers")
    def test_trigger_unknown_flow(self, mock_triggers):
        """Trigger referencing unknown flow should return error."""
        mock_triggers.return_value = [{
            "trigger_name": "bad_trigger",
            "event_type": "supabase_insert",
            "source_schema": "bronze",
            "source_table": "raw_products",
            "flow_name": "nonexistent_flow",
            "filter_config": {},
        }]

        result = handle_webhook_event({
            "type": "INSERT",
            "table": "raw_products",
            "schema": "bronze",
            "record": {},
        })

        assert result.get("skipped") is True
        assert "Unknown flow" in result.get("error", "")

    @patch("orchestrator.triggers.db.list_active_event_triggers")
    @patch("orchestrator.flows.realtime_event_flow")
    def test_event_type_mapping(self, mock_flow, mock_triggers):
        """Supabase event types should be mapped correctly."""
        mock_triggers.return_value = []
        mock_flow.return_value = {}

        for supabase_type, expected_type in [
            ("INSERT", "supabase_insert"),
            ("UPDATE", "supabase_update"),
            ("DELETE", "supabase_delete"),
        ]:
            handle_webhook_event({
                "type": supabase_type,
                "table": "raw_products",
                "schema": "bronze",
                "record": {},
            })

            call_kwargs = mock_flow.call_args
            # The first positional arg should be the mapped event type
            assert call_kwargs[1].get("event_type") == expected_type or \
                   call_kwargs[0][0] == expected_type

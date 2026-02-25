"""
Tests for orchestrator.pipelines
=================================

Tests pipeline task wrappers with subprocess and DB calls mocked.
Focuses on:
- PreBronze detected_table extraction from stdout
- USDA Nutrition Fetch task structure and CLI args
"""

import subprocess
import pytest
from unittest.mock import patch, MagicMock, ANY

from orchestrator.pipelines import (
    _parse_pipeline_stdout,
    run_prebronze_to_bronze,
    run_usda_nutrition_fetch,
    PipelineTimeoutError,
)


# ══════════════════════════════════════════════════════
# Tests for detected_table parsing in PreBronze
# ══════════════════════════════════════════════════════


class TestPreBronzeDetectedTable:
    """Verify that run_prebronze_to_bronze extracts detected_table from stdout."""

    STDOUT_WITH_TABLE = (
        "Bronze-ready CSV written to: /tmp/output.csv\n"
        "Supabase load complete: 500 rows upserted into raw_recipes.\n"
        "Pipeline completed in (1.23 minutes)\n"
    )

    STDOUT_NO_TABLE = (
        "Bronze-ready CSV written to: /tmp/output.csv\n"
        "Pipeline completed in (0.50 minutes)\n"
    )

    STDOUT_WITH_PRODUCTS = (
        "Bronze-ready CSV written to: /tmp/output.csv\n"
        "Supabase load complete: 200 rows upserted into raw_products.\n"
        "Pipeline completed in (0.80 minutes)\n"
    )

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_extracts_raw_recipes_table(self, mock_subprocess, mock_db):
        """When stdout contains 'upserted into raw_recipes', result should have detected_table."""
        mock_db.create_pipeline_run.return_value = {"id": "run-1"}
        mock_db.get_pipeline_timeout.return_value = 300
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = self.STDOUT_WITH_TABLE
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = run_prebronze_to_bronze.fn(
            orchestration_run_id="orch-1",
            source_name="test_source",
            raw_input=[{"name": "test"}],
        )

        assert result["detected_table"] == "raw_recipes"

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_extracts_raw_products_table(self, mock_subprocess, mock_db):
        """When stdout contains 'upserted into raw_products', detected_table should be raw_products."""
        mock_db.create_pipeline_run.return_value = {"id": "run-1"}
        mock_db.get_pipeline_timeout.return_value = 300
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = self.STDOUT_WITH_PRODUCTS
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = run_prebronze_to_bronze.fn(
            orchestration_run_id="orch-1",
            source_name="test_source",
            raw_input=[{"name": "test"}],
        )

        assert result["detected_table"] == "raw_products"

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_no_table_in_stdout(self, mock_subprocess, mock_db):
        """When stdout doesn't contain table info, detected_table should be absent."""
        mock_db.create_pipeline_run.return_value = {"id": "run-1"}
        mock_db.get_pipeline_timeout.return_value = 300
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = self.STDOUT_NO_TABLE
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = run_prebronze_to_bronze.fn(
            orchestration_run_id="orch-1",
            source_name="test_source",
            raw_input=[{"name": "test"}],
        )

        assert "detected_table" not in result

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_pipeline_run_marked_completed(self, mock_subprocess, mock_db):
        """Pipeline run should be updated to 'completed' status on success."""
        mock_db.create_pipeline_run.return_value = {"id": "run-1"}
        mock_db.get_pipeline_timeout.return_value = 300
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = self.STDOUT_WITH_TABLE
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        run_prebronze_to_bronze.fn(
            orchestration_run_id="orch-1",
            source_name="test_source",
            raw_input=[{"name": "test"}],
        )

        mock_db.update_pipeline_run.assert_called_once()
        call_kwargs = mock_db.update_pipeline_run.call_args
        assert call_kwargs[1]["status"] == "completed" or call_kwargs[0][1] == "completed"

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_subprocess_called_with_correct_args(self, mock_subprocess, mock_db):
        """Subprocess should be called with correct module and CLI args."""
        mock_db.create_pipeline_run.return_value = {"id": "run-1"}
        mock_db.get_pipeline_timeout.return_value = 600
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = self.STDOUT_WITH_TABLE
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        run_prebronze_to_bronze.fn(
            orchestration_run_id="orch-1",
            source_name="my_vendor",
            raw_input=[{"x": 1}],
        )

        mock_subprocess.assert_called_once()
        call_kwargs = mock_subprocess.call_args[1]
        assert call_kwargs["module"] == "prebronze.orchestrator"
        assert call_kwargs["pipeline_name"] == "prebronze_to_bronze"

        cli_args = call_kwargs["cli_args"]
        assert "--source-name" in cli_args
        idx = cli_args.index("--source-name")
        assert cli_args[idx + 1] == "my_vendor"


# ══════════════════════════════════════════════════════
# Tests for USDA Nutrition Fetch task
# ══════════════════════════════════════════════════════


class TestUsdaNutritionFetch:
    """Verify run_usda_nutrition_fetch task wrapper."""

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_success_flow(self, mock_subprocess, mock_db):
        """Happy path: subprocess succeeds, pipeline run marked completed."""
        mock_db.create_pipeline_run.return_value = {"id": "usda-run-1"}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = "[COMPLETE] Connector workflow finished successfully!\n"
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = run_usda_nutrition_fetch.fn(
            orchestration_run_id="orch-1",
            config={"usda_limit": 10, "usda_max_workers": 2},
        )

        # Verify subprocess got correct CLI args
        call_kwargs = mock_subprocess.call_args[1]
        assert call_kwargs["module"] == "main_enhanced"
        assert call_kwargs["pipeline_name"] == "usda_nutrition_fetch"

        cli_args = call_kwargs["cli_args"]
        assert "--limit" in cli_args
        assert "10" in cli_args
        assert "--max-workers" in cli_args
        assert "2" in cli_args

        # Verify pipeline run marked completed
        mock_db.update_pipeline_run.assert_called_once()
        update_args = mock_db.update_pipeline_run.call_args
        assert update_args[1]["status"] == "completed" or update_args[0][1] == "completed"

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_default_workers_when_not_specified(self, mock_subprocess, mock_db):
        """Without usda_max_workers config, should default to 3."""
        mock_db.create_pipeline_run.return_value = {"id": "usda-run-2"}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        run_usda_nutrition_fetch.fn(
            orchestration_run_id="orch-1",
            config={},
        )

        cli_args = mock_subprocess.call_args[1]["cli_args"]
        assert "--max-workers" in cli_args
        idx = cli_args.index("--max-workers")
        assert cli_args[idx + 1] == "3"

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_no_limit_flag_when_not_specified(self, mock_subprocess, mock_db):
        """Without usda_limit config, --limit flag should be absent."""
        mock_db.create_pipeline_run.return_value = {"id": "usda-run-3"}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        run_usda_nutrition_fetch.fn(
            orchestration_run_id="orch-1",
            config={},
        )

        cli_args = mock_subprocess.call_args[1]["cli_args"]
        assert "--limit" not in cli_args

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_timeout_defaults_to_3600(self, mock_subprocess, mock_db):
        """Default timeout should be 3600s (1 hour) for USDA API calls."""
        mock_db.create_pipeline_run.return_value = {"id": "usda-run-4"}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_proc = MagicMock()
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        run_usda_nutrition_fetch.fn(
            orchestration_run_id="orch-1",
        )

        call_kwargs = mock_subprocess.call_args[1]
        assert call_kwargs["timeout"] == 3600

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_failure_marks_pipeline_failed(self, mock_subprocess, mock_db):
        """Subprocess error should mark pipeline run as 'failed'."""
        mock_db.create_pipeline_run.return_value = {"id": "usda-run-5"}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_subprocess.side_effect = RuntimeError("USDA API unreachable")

        with pytest.raises(RuntimeError, match="USDA API unreachable"):
            run_usda_nutrition_fetch.fn(orchestration_run_id="orch-1")

        update_args = mock_db.update_pipeline_run.call_args
        assert update_args[1]["status"] == "failed" or update_args[0][1] == "failed"

    @patch("orchestrator.pipelines.db")
    @patch("orchestrator.pipelines._run_pipeline_subprocess")
    def test_timeout_marks_pipeline_timed_out(self, mock_subprocess, mock_db):
        """PipelineTimeoutError should mark pipeline run as 'timed_out'."""
        mock_db.create_pipeline_run.return_value = {"id": "usda-run-6"}
        mock_db._utcnow.return_value = "2026-01-01T00:00:00"
        mock_db.update_orchestration_run.return_value = {}
        mock_db.update_pipeline_run.return_value = {}

        mock_subprocess.side_effect = PipelineTimeoutError("usda_nutrition_fetch", 3600)

        with pytest.raises(PipelineTimeoutError):
            run_usda_nutrition_fetch.fn(orchestration_run_id="orch-1")

        update_args = mock_db.update_pipeline_run.call_args
        assert update_args[1]["status"] == "timed_out" or update_args[0][1] == "timed_out"

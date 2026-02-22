"""
Tests for orchestrator.cli
===========================

Validates CLI argument parsing and help output.
"""

import pytest
from unittest.mock import patch, MagicMock

from orchestrator.cli import main


class TestCLIHelp:
    def test_main_no_args_shows_help(self, capsys):
        """Running with no args should show help and exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["orchestrator"]):
                main()
        assert exc_info.value.code == 0

    def test_run_help(self, capsys):
        """'run --help' should show run subcommand help."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["orchestrator", "run", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--flow" in captured.out
        assert "--source-name" in captured.out

    def test_serve_help(self, capsys):
        """'serve --help' should show serve subcommand help."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["orchestrator", "serve", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--host" in captured.out
        assert "--port" in captured.out

    def test_schedule_help(self, capsys):
        """'schedule --help' should show schedule subcommand help."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["orchestrator", "schedule", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--register" in captured.out

    def test_runs_help(self, capsys):
        """'runs --help' should show runs subcommand help."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["orchestrator", "runs", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--limit" in captured.out
        assert "--status" in captured.out


class TestCLIRunCommand:
    def test_run_requires_flow(self):
        """'run' without --flow should fail."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["orchestrator", "run"]):
                main()
        assert exc_info.value.code == 2  # argparse error code

    @patch("orchestrator.cli.cmd_run")
    def test_run_parses_args(self, mock_cmd, mock_env):
        """Arguments should be parsed and passed to cmd_run."""
        with patch("sys.argv", [
            "orchestrator", "run",
            "--flow", "full_ingestion",
            "--source-name", "walmart",
            "--input", "/data/products.csv",
            "--batch-size", "500",
        ]):
            main()

        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.flow == "full_ingestion"
        assert args.source_name == "walmart"
        assert args.input == "/data/products.csv"
        assert args.batch_size == 500

    @patch("orchestrator.cli.cmd_run")
    def test_run_single_layer(self, mock_cmd, mock_env):
        """Single layer run should parse the --layer argument."""
        with patch("sys.argv", [
            "orchestrator", "run",
            "--flow", "single_layer",
            "--layer", "bronze_to_silver",
        ]):
            main()

        args = mock_cmd.call_args[0][0]
        assert args.flow == "single_layer"
        assert args.layer == "bronze_to_silver"


class TestCLIServeCommand:
    @patch("orchestrator.cli.cmd_serve")
    def test_serve_defaults(self, mock_cmd, mock_env):
        with patch("sys.argv", ["orchestrator", "serve"]):
            main()

        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.host is None  # Will use settings default
        assert args.port is None
        assert args.reload is False

    @patch("orchestrator.cli.cmd_serve")
    def test_serve_custom(self, mock_cmd, mock_env):
        with patch("sys.argv", [
            "orchestrator", "serve",
            "--host", "127.0.0.1",
            "--port", "9000",
            "--reload",
        ]):
            main()

        args = mock_cmd.call_args[0][0]
        assert args.host == "127.0.0.1"
        assert args.port == 9000
        assert args.reload is True


class TestCLIRunsCommand:
    @patch("orchestrator.cli.cmd_runs")
    def test_runs_defaults(self, mock_cmd, mock_env):
        with patch("sys.argv", ["orchestrator", "runs"]):
            main()

        args = mock_cmd.call_args[0][0]
        assert args.limit == 20
        assert args.status is None

    @patch("orchestrator.cli.cmd_runs")
    def test_runs_with_filters(self, mock_cmd, mock_env):
        with patch("sys.argv", [
            "orchestrator", "runs",
            "--limit", "5",
            "--status", "failed",
        ]):
            main()

        args = mock_cmd.call_args[0][0]
        assert args.limit == 5
        assert args.status == "failed"

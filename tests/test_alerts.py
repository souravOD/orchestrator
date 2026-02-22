"""
Tests for the alerting system.
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from orchestrator.alerts import (
    AlertDispatcher,
    EmailAlertSender,
    GitHubIssueAlertSender,
    get_dispatcher,
    send_alert,
)


# ══════════════════════════════════════════════════════
# EmailAlertSender
# ══════════════════════════════════════════════════════

class TestEmailAlertSender:
    def test_not_configured_when_missing_host(self):
        sender = EmailAlertSender(host="", from_addr="test@x.com", to_addrs="a@x.com")
        assert not sender.is_configured

    def test_not_configured_when_missing_to(self):
        sender = EmailAlertSender(host="smtp.test.com", from_addr="test@x.com", to_addrs="")
        assert not sender.is_configured

    def test_configured_when_all_set(self):
        sender = EmailAlertSender(
            host="smtp.test.com", from_addr="test@x.com", to_addrs="a@x.com, b@x.com"
        )
        assert sender.is_configured
        assert len(sender.to_addrs) == 2

    def test_send_skips_when_not_configured(self):
        sender = EmailAlertSender(host="", from_addr="", to_addrs="")
        result = sender.send("Test", "<p>Test</p>")
        assert result is False

    @patch("smtplib.SMTP")
    def test_send_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        sender = EmailAlertSender(
            host="smtp.test.com",
            port=587,
            user="user",
            password="pass",
            from_addr="test@x.com",
            to_addrs="a@x.com",
        )
        result = sender.send("Pipeline Failed", "<p>Error</p>", severity="critical")
        assert result is True

    @patch("smtplib.SMTP", side_effect=Exception("SMTP error"))
    def test_send_failure(self, mock_smtp_cls):
        sender = EmailAlertSender(
            host="smtp.test.com",
            from_addr="test@x.com",
            to_addrs="a@x.com",
        )
        result = sender.send("Pipeline Failed", "<p>Error</p>")
        assert result is False


# ══════════════════════════════════════════════════════
# GitHubIssueAlertSender
# ══════════════════════════════════════════════════════

class TestGitHubIssueAlertSender:
    def test_not_configured_when_missing_token(self):
        sender = GitHubIssueAlertSender(token="", repo="owner/repo")
        assert not sender.is_configured

    def test_not_configured_when_missing_repo(self):
        sender = GitHubIssueAlertSender(token="ghp_test", repo="")
        assert not sender.is_configured

    def test_configured_when_all_set(self):
        sender = GitHubIssueAlertSender(token="ghp_test", repo="owner/repo")
        assert sender.is_configured

    def test_create_issue_skips_when_not_configured(self):
        sender = GitHubIssueAlertSender(token="", repo="")
        result = sender.create_issue("Test", "Body")
        assert result is None

    @patch("httpx.post")
    def test_create_issue_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"html_url": "https://github.com/owner/repo/issues/1"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        sender = GitHubIssueAlertSender(token="ghp_test", repo="owner/repo")
        url = sender.create_issue("Failure", "Pipeline crashed", labels=["bug"])
        assert url == "https://github.com/owner/repo/issues/1"
        mock_post.assert_called_once()

    @patch("httpx.post", side_effect=Exception("API error"))
    def test_create_issue_failure(self, mock_post):
        sender = GitHubIssueAlertSender(token="ghp_test", repo="owner/repo")
        url = sender.create_issue("Failure", "Pipeline crashed")
        assert url is None


# ══════════════════════════════════════════════════════
# AlertDispatcher
# ══════════════════════════════════════════════════════

class TestAlertDispatcher:
    def test_deduplication(self):
        dispatcher = AlertDispatcher()
        # First call should not be suppressed
        with patch.object(dispatcher, "_build_html_body", return_value="<p>Test</p>"), \
             patch.object(dispatcher, "_build_markdown_body", return_value="Test"), \
             patch("orchestrator.alerts.db", create=True) as mock_db:
            mock_db.create_alert_log = MagicMock()
            result1 = dispatcher.dispatch("Test Alert", "msg", dedup_window_seconds=300)
            assert "suppressed" not in result1

            # Second call within window should be suppressed
            result2 = dispatcher.dispatch("Test Alert", "msg", dedup_window_seconds=300)
            assert result2.get("suppressed") is True

    def test_dispatch_with_email(self):
        dispatcher = AlertDispatcher()
        dispatcher.email_sender = MagicMock()
        dispatcher.email_sender.is_configured = True
        dispatcher.email_sender.send.return_value = True
        dispatcher.github_sender = MagicMock()
        dispatcher.github_sender.is_configured = False

        with patch("orchestrator.alerts.db", create=True) as mock_db:
            mock_db.create_alert_log = MagicMock()
            result = dispatcher.dispatch(
                "Pipeline Failed",
                "Error details",
                severity="critical",
                pipeline_name="gold_to_neo4j",
                channels=["email"],
            )
        assert result.get("email") is True

    def test_build_html_body(self):
        html = AlertDispatcher._build_html_body(
            "Test", "Message", "critical", "gold_to_neo4j", "run-123", None,
        )
        assert "CRITICAL" in html
        assert "gold_to_neo4j" in html
        assert "run-123" in html

    def test_build_markdown_body(self):
        md = AlertDispatcher._build_markdown_body(
            "Message", "warning", "gold_to_neo4j", "run-123",
            {"error": "test error"},
        )
        assert "gold_to_neo4j" in md
        assert "run-123" in md
        assert "test error" in md


# ══════════════════════════════════════════════════════
# Module-Level Functions
# ══════════════════════════════════════════════════════

class TestModuleFunctions:
    def test_get_dispatcher_singleton(self):
        d1 = get_dispatcher()
        d2 = get_dispatcher()
        assert d1 is d2

    def test_send_alert_convenience(self):
        with patch("orchestrator.alerts._dispatcher") as mock_d:
            mock_d.dispatch.return_value = {"email": True}
            # Reset singleton so it creates a new one
            import orchestrator.alerts as alerts_mod
            alerts_mod._dispatcher = None
            dispatcher = get_dispatcher()
            with patch.object(dispatcher, "dispatch", return_value={"email": True}) as mock_dispatch:
                result = send_alert("Test", "Message", severity="info")
            mock_dispatch.assert_called_once()

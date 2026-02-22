"""
Alerting System
================

Dispatches alerts via Email (SMTP) and GitHub Issues when pipelines fail,
data quality thresholds are breached, or reconciliation drift is detected.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from .config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# Alert Types & Severity
# ══════════════════════════════════════════════════════

ALERT_TYPES = ("email", "github_issue")
ALERT_SEVERITIES = ("info", "warning", "critical")


# ══════════════════════════════════════════════════════
# Email Alert Sender
# ══════════════════════════════════════════════════════

class EmailAlertSender:
    """Send HTML-formatted alert emails via SMTP."""

    def __init__(
        self,
        host: str = "",
        port: int = 587,
        user: str = "",
        password: str = "",
        use_tls: bool = True,
        from_addr: str = "",
        to_addrs: str = "",
    ) -> None:
        self.host = host or settings.smtp_host
        self.port = port or settings.smtp_port
        self.user = user or settings.smtp_user
        self.password = password or settings.smtp_password
        self.use_tls = use_tls
        self.from_addr = from_addr or settings.alert_email_from
        self.to_addrs = [
            a.strip()
            for a in (to_addrs or settings.alert_email_to).split(",")
            if a.strip()
        ]

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.from_addr and self.to_addrs)

    def send(
        self,
        subject: str,
        body_html: str,
        severity: str = "warning",
    ) -> bool:
        """
        Send an alert email.

        Returns True on success, False on failure.
        """
        if not self.is_configured:
            logger.warning("Email alerting not configured — skipping")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[ASM Orchestrator] [{severity.upper()}] {subject}"
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.attach(MIMEText(body_html, "html"))

        try:
            if self.use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(self.host, self.port) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    if self.user and self.password:
                        server.login(self.user, self.password)
                    server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            else:
                with smtplib.SMTP(self.host, self.port) as server:
                    if self.user and self.password:
                        server.login(self.user, self.password)
                    server.sendmail(self.from_addr, self.to_addrs, msg.as_string())

            logger.info("Alert email sent: %s", subject)
            return True

        except Exception as exc:
            logger.error("Failed to send alert email: %s", exc)
            return False


# ══════════════════════════════════════════════════════
# GitHub Issue Alert Sender
# ══════════════════════════════════════════════════════

class GitHubIssueAlertSender:
    """Create GitHub Issues for critical pipeline failures."""

    GITHUB_API = "https://api.github.com"

    def __init__(
        self,
        token: str = "",
        repo: str = "",
    ) -> None:
        self.token = token or settings.github_token
        self.repo = repo or settings.github_repo  # "owner/repo"

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.repo)

    def create_issue(
        self,
        title: str,
        body: str,
        labels: Optional[List[str]] = None,
        severity: str = "warning",
    ) -> Optional[str]:
        """
        Create a GitHub Issue.

        Returns the issue URL on success, None on failure.
        """
        if not self.is_configured:
            logger.warning("GitHub alerting not configured — skipping")
            return None

        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed — cannot create GitHub issue")
            return None

        url = f"{self.GITHUB_API}/repos/{self.repo}/issues"
        issue_labels = list(labels or [])
        issue_labels.append("pipeline-alert")
        issue_labels.append(f"severity:{severity}")

        payload = {
            "title": f"[{severity.upper()}] {title}",
            "body": body,
            "labels": issue_labels,
        }

        try:
            response = httpx.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"token {self.token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=30,
            )
            response.raise_for_status()
            issue_url = response.json().get("html_url", "")
            logger.info("GitHub issue created: %s", issue_url)
            return issue_url

        except Exception as exc:
            logger.error("Failed to create GitHub issue: %s", exc)
            return None


# ══════════════════════════════════════════════════════
# Alert Dispatcher
# ══════════════════════════════════════════════════════

class AlertDispatcher:
    """
    Unified alert dispatch.

    Sends alerts through all configured channels and logs them
    to the orchestration ``alert_log`` table.
    """

    def __init__(self) -> None:
        self.email_sender = EmailAlertSender()
        self.github_sender = GitHubIssueAlertSender()
        self._recent_alerts: Dict[str, datetime] = {}

    def dispatch(
        self,
        title: str,
        message: str,
        severity: str = "warning",
        pipeline_name: Optional[str] = None,
        run_id: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
        channels: Optional[List[str]] = None,
        dedup_key: Optional[str] = None,
        dedup_window_seconds: int = 300,
    ) -> Dict[str, Any]:
        """
        Dispatch an alert to all configured channels.

        Parameters
        ----------
        title : str
            Subject line / issue title.
        message : str
            Detailed alert body (plain text).
        severity : str
            One of: info, warning, critical.
        pipeline_name : str, optional
            Which pipeline triggered the alert.
        run_id : str, optional
            Orchestration/pipeline run ID.
        error_details : dict, optional
            Additional error context.
        channels : list[str], optional
            Override channels ("email", "github_issue"). Default: all configured.
        dedup_key : str, optional
            Key for deduplication. Default: title.
        dedup_window_seconds : int
            Suppress duplicate alerts within this window.

        Returns
        -------
        dict
            Status of each channel dispatch.
        """
        # Deduplication
        key = dedup_key or title
        now = datetime.now(timezone.utc)
        if key in self._recent_alerts:
            elapsed = (now - self._recent_alerts[key]).total_seconds()
            if elapsed < dedup_window_seconds:
                logger.info("Alert suppressed (dedup): %s", title)
                return {"suppressed": True, "elapsed_seconds": elapsed}
        self._recent_alerts[key] = now

        results: Dict[str, Any] = {"title": title, "severity": severity}
        use_channels = channels or list(ALERT_TYPES)

        # Build HTML body for email
        html_body = self._build_html_body(
            title, message, severity, pipeline_name, run_id, error_details,
        )

        # Build markdown body for GitHub
        md_body = self._build_markdown_body(
            message, severity, pipeline_name, run_id, error_details,
        )

        # Email
        if "email" in use_channels and self.email_sender.is_configured:
            results["email"] = self.email_sender.send(title, html_body, severity)

        # GitHub Issue
        if "github_issue" in use_channels and self.github_sender.is_configured:
            labels = []
            if pipeline_name:
                labels.append(f"pipeline:{pipeline_name}")
            issue_url = self.github_sender.create_issue(
                title, md_body, labels=labels, severity=severity,
            )
            results["github_issue"] = issue_url

        # Log to DB
        try:
            from . import db
            db.create_alert_log(
                alert_type=", ".join(use_channels),
                severity=severity,
                title=title,
                message=message[:2000],
                pipeline_name=pipeline_name,
                run_id=run_id,
                dispatch_result=results,
            )
        except Exception as exc:
            logger.warning("Failed to log alert to DB: %s", exc)

        return results

    # ── HTML builder ──────────────────────────────────

    @staticmethod
    def _build_html_body(
        title: str,
        message: str,
        severity: str,
        pipeline_name: Optional[str],
        run_id: Optional[str],
        error_details: Optional[Dict[str, Any]],
    ) -> str:
        color = {"info": "#2196F3", "warning": "#FF9800", "critical": "#F44336"}.get(
            severity, "#999"
        )
        parts = [
            f'<div style="font-family:sans-serif;max-width:600px;margin:auto">',
            f'<div style="background:{color};color:white;padding:16px 24px;border-radius:8px 8px 0 0">',
            f"<h2 style='margin:0'>{severity.upper()}: {title}</h2>",
            f"</div>",
            f'<div style="padding:24px;border:1px solid #ddd;border-top:0;border-radius:0 0 8px 8px">',
            f"<p>{message}</p>",
        ]
        if pipeline_name:
            parts.append(f"<p><b>Pipeline:</b> {pipeline_name}</p>")
        if run_id:
            parts.append(f"<p><b>Run ID:</b> <code>{run_id}</code></p>")
        if error_details:
            parts.append(f"<pre>{json.dumps(error_details, indent=2, default=str)}</pre>")
        parts.append(
            f'<p style="color:#999;font-size:12px">Sent at '
            f"{datetime.now(timezone.utc).isoformat()} by ASM Orchestrator</p>"
        )
        parts.append("</div></div>")
        return "\n".join(parts)

    # ── Markdown builder ──────────────────────────────

    @staticmethod
    def _build_markdown_body(
        message: str,
        severity: str,
        pipeline_name: Optional[str],
        run_id: Optional[str],
        error_details: Optional[Dict[str, Any]],
    ) -> str:
        parts = [message, ""]
        if pipeline_name:
            parts.append(f"**Pipeline:** `{pipeline_name}`")
        if run_id:
            parts.append(f"**Run ID:** `{run_id}`")
        if error_details:
            parts.append("\n```json")
            parts.append(json.dumps(error_details, indent=2, default=str))
            parts.append("```")
        parts.append(
            f"\n_Dispatched at {datetime.now(timezone.utc).isoformat()} "
            f"by ASM Orchestrator_"
        )
        return "\n".join(parts)


# ── Module-level convenience ─────────────────────────

import json

_dispatcher: Optional[AlertDispatcher] = None

def get_dispatcher() -> AlertDispatcher:
    """Return a singleton AlertDispatcher."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher()
    return _dispatcher


def send_alert(
    title: str,
    message: str,
    severity: str = "warning",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience function to dispatch an alert."""
    return get_dispatcher().dispatch(title, message, severity=severity, **kwargs)

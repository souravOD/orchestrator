"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, DashboardStats, OrchestrationRun, Alert } from "@/lib/api";
import KPICard from "@/components/KPICard";
import StatusBadge from "@/components/StatusBadge";

function formatDuration(seconds: number | null): string {
  if (!seconds) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function OverviewPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [runs, setRuns] = useState<OrchestrationRun[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [s, r, a] = await Promise.all([
          api.getStats(),
          api.listRuns(10),
          api.listAlerts(5),
        ]);
        setStats(s);
        setRuns(r.runs);
        setAlerts(a.alerts);
      } catch (err) {
        console.error("Failed to load overview:", err);
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div>
        <div className="page-header">
          <h2>Overview</h2>
          <p>Pipeline health at a glance</p>
        </div>
        <div className="kpi-grid">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="shimmer shimmer-card" />
          ))}
        </div>
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="shimmer shimmer-row" />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div className="flex items-center justify-between">
          <div>
            <h2>Overview</h2>
            <p>Pipeline health at a glance</p>
          </div>
          {!error && (
            <div className="live-indicator">
              <span className="live-dot" />
              Live
            </div>
          )}
        </div>
      </div>

      {/* Connection Error Banner */}
      {error && (
        <div
          style={{
            padding: "16px 20px",
            marginBottom: 24,
            borderRadius: "var(--radius-md)",
            border: "1px solid rgba(245, 158, 11, 0.3)",
            background: "rgba(245, 158, 11, 0.08)",
            color: "var(--accent-amber)",
            fontSize: 13,
          }}
        >
          <strong>⚠ Backend Unavailable</strong>
          <div style={{ marginTop: 4, color: "var(--text-secondary)", fontSize: 12 }}>
            {error}
          </div>
        </div>
      )}

      {/* KPI Cards */}
      <div className="kpi-grid">
        <KPICard
          label="Total Runs"
          value={stats?.total_runs ?? 0}
          sub="Last 100 runs"
          color="blue"
        />
        <KPICard
          label="Success Rate"
          value={`${stats?.success_rate ?? 0}%`}
          sub={`${stats?.completed ?? 0} completed`}
          color="green"
        />
        <KPICard
          label="Failed"
          value={stats?.failed ?? 0}
          sub={`${stats?.running ?? 0} running`}
          color="red"
        />
        <KPICard
          label="Avg Duration"
          value={formatDuration(stats?.avg_duration_seconds ?? 0)}
          sub={`${stats?.total_records_written?.toLocaleString() ?? 0} records`}
          color="purple"
        />
        <KPICard
          label="Active Alerts"
          value={stats?.active_alerts ?? 0}
          sub="Warning + Critical"
          color="amber"
        />
        <KPICard
          label="Records Written"
          value={(stats?.total_records_written ?? 0).toLocaleString()}
          sub="Across all runs"
          color="cyan"
        />
      </div>

      {/* Recent Runs + Alerts */}
      <div className="grid-2">
        <div className="section">
          <div className="section-title">
            🔄 Recent Runs
            <Link href="/runs" style={{ fontSize: 12, marginLeft: "auto" }}>
              View all →
            </Link>
          </div>
          <div className="card">
            {runs.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">🔄</div>
                <p>No runs yet</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Flow</th>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <Link href={`/runs/${run.id}`} className="fw-600">
                          {run.flow_name}
                        </Link>
                      </td>
                      <td className="text-muted text-sm">
                        {run.source_name || '—'}
                      </td>
                      <td>
                        <StatusBadge status={run.status} />
                      </td>
                      <td className="mono text-muted">
                        {formatDuration(run.duration_seconds)}
                      </td>
                      <td className="text-muted text-sm">
                        {formatTime(run.started_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="section">
          <div className="section-title">
            🔔 Recent Alerts
            <Link href="/alerts" style={{ fontSize: 12, marginLeft: "auto" }}>
              View all →
            </Link>
          </div>
          <div className="card">
            {alerts.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">✅</div>
                <p>No recent alerts</p>
              </div>
            ) : (
              alerts.map((alert) => (
                <div key={alert.id} className="alert-row">
                  <div
                    className={`alert-severity-bar ${alert.severity}`}
                  />
                  <div className="alert-content">
                    <div className="alert-title">{alert.title}</div>
                    <div className="alert-meta">
                      <span className={`badge ${alert.severity}`}>
                        {alert.severity}
                      </span>
                      {alert.pipeline_name && (
                        <span>{alert.pipeline_name}</span>
                      )}
                      <span>{formatTime(alert.created_at)}</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

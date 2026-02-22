"use client";

import { useEffect, useState } from "react";
import { api, Alert } from "@/lib/api";

function formatTime(ts: string | null): string {
    if (!ts) return "—";
    return new Date(ts).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

const SEVERITY_FILTERS = [
    { label: "All", value: "" },
    { label: "Critical", value: "critical" },
    { label: "Warning", value: "warning" },
    { label: "Info", value: "info" },
];

export default function AlertsPage() {
    const [alerts, setAlerts] = useState<Alert[]>([]);
    const [severityFilter, setSeverityFilter] = useState("");
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                setLoading(true);
                const data = await api.listAlerts(
                    100,
                    severityFilter || undefined,
                    undefined
                );
                setAlerts(data.alerts);
            } catch (err) {
                console.error("Failed to load alerts:", err);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, [severityFilter]);

    const criticalCount = alerts.filter((a) => a.severity === "critical").length;
    const warningCount = alerts.filter((a) => a.severity === "warning").length;

    return (
        <div>
            <div className="page-header">
                <div className="flex items-center justify-between">
                    <div>
                        <h2>Alerts</h2>
                        <p>Pipeline alert history and notifications</p>
                    </div>
                    {(criticalCount > 0 || warningCount > 0) && (
                        <div className="flex gap-8">
                            {criticalCount > 0 && (
                                <span className="badge critical">{criticalCount} critical</span>
                            )}
                            {warningCount > 0 && (
                                <span className="badge warning">{warningCount} warning</span>
                            )}
                        </div>
                    )}
                </div>
            </div>

            <div className="filter-bar">
                {SEVERITY_FILTERS.map((f) => (
                    <button
                        key={f.value}
                        className={`filter-btn ${severityFilter === f.value ? "active" : ""}`}
                        onClick={() => setSeverityFilter(f.value)}
                    >
                        {f.label}
                    </button>
                ))}
            </div>

            <div className="card">
                {loading ? (
                    <>
                        {[1, 2, 3, 4, 5].map((i) => (
                            <div key={i} className="shimmer shimmer-row" />
                        ))}
                    </>
                ) : alerts.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">✅</div>
                        <p>
                            No alerts{severityFilter ? ` with severity "${severityFilter}"` : ""}
                        </p>
                    </div>
                ) : (
                    alerts.map((alert) => (
                        <div key={alert.id} className="alert-row">
                            <div className={`alert-severity-bar ${alert.severity}`} />
                            <div className="alert-content">
                                <div className="alert-title">{alert.title}</div>
                                {alert.message && (
                                    <div
                                        className="text-sm text-muted"
                                        style={{ marginTop: 4, marginBottom: 6 }}
                                    >
                                        {alert.message}
                                    </div>
                                )}
                                <div className="alert-meta">
                                    <span className={`badge ${alert.severity}`}>
                                        {alert.severity}
                                    </span>
                                    <span className="badge" style={{ background: "rgba(255,255,255,0.05)", color: "var(--text-secondary)" }}>
                                        {alert.alert_type}
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
    );
}

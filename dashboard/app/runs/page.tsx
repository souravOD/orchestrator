"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, OrchestrationRun } from "@/lib/api";
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
        second: "2-digit",
    });
}

const STATUS_FILTERS = [
    { label: "All", value: "" },
    { label: "Completed", value: "completed" },
    { label: "Failed", value: "failed" },
    { label: "Running", value: "running" },
    { label: "Pending", value: "pending" },
];

export default function RunsPage() {
    const [runs, setRuns] = useState<OrchestrationRun[]>([]);
    const [statusFilter, setStatusFilter] = useState("");
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                setLoading(true);
                const data = await api.listRuns(50, statusFilter || undefined);
                setRuns(data.runs);
            } catch (err) {
                console.error("Failed to load runs:", err);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, [statusFilter]);

    return (
        <div>
            <div className="page-header">
                <h2>Pipeline Runs</h2>
                <p>All orchestration runs across flows</p>
            </div>

            <div className="filter-bar">
                {STATUS_FILTERS.map((f) => (
                    <button
                        key={f.value}
                        className={`filter-btn ${statusFilter === f.value ? "active" : ""}`}
                        onClick={() => setStatusFilter(f.value)}
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
                ) : runs.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">🔍</div>
                        <p>No runs found{statusFilter ? ` with status "${statusFilter}"` : ""}</p>
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Flow</th>
                                <th>Status</th>
                                <th>Trigger</th>
                                <th>Records</th>
                                <th>DQ Issues</th>
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
                                        <div className="text-xs text-muted">{run.id.slice(0, 8)}</div>
                                    </td>
                                    <td>
                                        <StatusBadge status={run.status} />
                                    </td>
                                    <td className="text-sm text-muted">{run.trigger_type}</td>
                                    <td className="mono">
                                        {run.total_records_written.toLocaleString()}
                                    </td>
                                    <td className="mono">
                                        {run.total_dq_issues > 0 ? (
                                            <span style={{ color: "var(--accent-amber)" }}>
                                                {run.total_dq_issues}
                                            </span>
                                        ) : (
                                            <span className="text-muted">0</span>
                                        )}
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
    );
}

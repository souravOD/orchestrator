"use client";

import { useEffect, useState } from "react";
import { api, OrchestrationRun } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import KPICard from "@/components/KPICard";

function formatTime(ts: string | null): string {
    if (!ts) return "—";
    return new Date(ts).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function formatDuration(seconds: number | null): string {
    if (!seconds) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

const NEO4J_LAYERS = [
    { name: "recipes", icon: "🍳", desc: "Recipe nodes and relationships" },
    { name: "ingredients", icon: "🥕", desc: "Ingredient nodes and nutrition edges" },
    { name: "products", icon: "📦", desc: "Product catalog nodes" },
    { name: "customers", icon: "👥", desc: "Customer preference graph" },
];

export default function Neo4jPage() {
    const [syncRuns, setSyncRuns] = useState<OrchestrationRun[]>([]);
    const [reconRuns, setReconRuns] = useState<OrchestrationRun[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                const [sync, recon] = await Promise.all([
                    api.getNeo4jSyncStatus(),
                    api.getNeo4jReconciliation(),
                ]);
                setSyncRuns(sync.latest_runs);
                setReconRuns(recon.latest_runs);
            } catch (err) {
                console.error("Failed to load Neo4j status:", err);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    const lastSync = syncRuns[0];
    const lastRecon = reconRuns[0];

    return (
        <div>
            <div className="page-header">
                <h2>Neo4j Sync</h2>
                <p>Gold → Neo4j graph synchronization status</p>
            </div>

            {/* Status Cards */}
            <div className="kpi-grid">
                <KPICard
                    label="Last Sync"
                    value={lastSync ? formatTime(lastSync.started_at) : "—"}
                    sub={lastSync?.status || "No syncs"}
                    color="cyan"
                />
                <KPICard
                    label="Sync Status"
                    value={lastSync?.status || "—"}
                    sub={lastSync ? formatDuration(lastSync.duration_seconds) : ""}
                    color={lastSync?.status === "completed" ? "green" : lastSync?.status === "failed" ? "red" : "blue"}
                />
                <KPICard
                    label="Last Reconciliation"
                    value={lastRecon ? formatTime(lastRecon.started_at) : "—"}
                    sub={lastRecon?.status || "No reconciliations"}
                    color="purple"
                />
            </div>

            {/* Layer Cards */}
            <div className="section">
                <div className="section-title">🕸️ Graph Layers</div>
                <div className="grid-2">
                    {NEO4J_LAYERS.map((layer) => (
                        <div key={layer.name} className="card">
                            <div className="flex items-center gap-8">
                                <span style={{ fontSize: 24 }}>{layer.icon}</span>
                                <div>
                                    <div className="fw-600">{layer.name}</div>
                                    <div className="text-sm text-muted">{layer.desc}</div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {/* Sync History */}
            <div className="section">
                <div className="section-title">📊 Sync History</div>
                <div className="card">
                    {syncRuns.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">🕸️</div>
                            <p>No sync runs recorded</p>
                        </div>
                    ) : (
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Type</th>
                                    <th>Status</th>
                                    <th>Records</th>
                                    <th>Duration</th>
                                    <th>Started</th>
                                </tr>
                            </thead>
                            <tbody>
                                {[...syncRuns, ...reconRuns].map((run) => (
                                    <tr key={run.id}>
                                        <td className="fw-600">{run.flow_name}</td>
                                        <td><StatusBadge status={run.status} /></td>
                                        <td className="mono">{run.total_records_written.toLocaleString()}</td>
                                        <td className="mono text-muted">{formatDuration(run.duration_seconds)}</td>
                                        <td className="text-muted text-sm">{formatTime(run.started_at)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>
        </div>
    );
}

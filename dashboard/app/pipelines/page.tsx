"use client";

import { useEffect, useState } from "react";
import { api, PipelineDefinition, PipelineHealth } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

function formatTime(ts: string | null): string {
    if (!ts) return "Never";
    return new Date(ts).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

const LAYER_COLORS: Record<string, string> = {
    prebronze: "#8b5cf6",
    bronze: "#f59e0b",
    silver: "#94a3b8",
    gold: "#eab308",
    neo4j: "#06b6d4",
};

export default function PipelinesPage() {
    const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
    const [health, setHealth] = useState<Record<string, PipelineHealth>>({});
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function load() {
            try {
                const data = await api.listPipelines();
                setPipelines(data.pipelines);

                // Fetch health for each pipeline
                const healthMap: Record<string, PipelineHealth> = {};
                await Promise.all(
                    data.pipelines.map(async (p) => {
                        try {
                            healthMap[p.pipeline_name] = await api.getPipelineHealth(
                                p.pipeline_name
                            );
                        } catch {
                            /* ignore health fetch errors */
                        }
                    })
                );
                setHealth(healthMap);
            } catch (err) {
                console.error("Failed to load pipelines:", err);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    return (
        <div>
            <div className="page-header">
                <h2>Pipelines</h2>
                <p>Registered pipeline definitions and health status</p>
            </div>

            {loading ? (
                <div className="grid-3">
                    {[1, 2, 3].map((i) => (
                        <div key={i} className="shimmer" style={{ height: 180 }} />
                    ))}
                </div>
            ) : pipelines.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon">🔗</div>
                    <p>No pipelines registered</p>
                </div>
            ) : (
                <div className="grid-3">
                    {pipelines.map((p) => {
                        const h = health[p.pipeline_name];
                        return (
                            <div key={p.id} className="card" style={{ position: "relative" }}>
                                {/* Layer flow indicator */}
                                <div className="flex items-center gap-8" style={{ marginBottom: 14 }}>
                                    <span
                                        style={{
                                            padding: "3px 10px",
                                            borderRadius: 100,
                                            fontSize: 11,
                                            fontWeight: 600,
                                            background:
                                                LAYER_COLORS[p.layer_from] + "22",
                                            color: LAYER_COLORS[p.layer_from],
                                        }}
                                    >
                                        {p.layer_from}
                                    </span>
                                    <span className="text-muted">→</span>
                                    <span
                                        style={{
                                            padding: "3px 10px",
                                            borderRadius: 100,
                                            fontSize: 11,
                                            fontWeight: 600,
                                            background:
                                                LAYER_COLORS[p.layer_to] + "22",
                                            color: LAYER_COLORS[p.layer_to],
                                        }}
                                    >
                                        {p.layer_to}
                                    </span>

                                    <span style={{ marginLeft: "auto" }}>
                                        {p.is_active ? (
                                            <span className="badge completed">Active</span>
                                        ) : (
                                            <span className="badge skipped">Inactive</span>
                                        )}
                                    </span>
                                </div>

                                <div className="fw-600" style={{ fontSize: 15, marginBottom: 6 }}>
                                    {p.pipeline_name}
                                </div>

                                {p.description && (
                                    <div
                                        className="text-sm text-muted"
                                        style={{
                                            marginBottom: 14,
                                            lineHeight: 1.5,
                                            display: "-webkit-box",
                                            WebkitLineClamp: 3,
                                            WebkitBoxOrient: "vertical",
                                            overflow: "hidden",
                                        }}
                                    >
                                        {p.description}
                                    </div>
                                )}

                                {/* Health Status */}
                                <div
                                    style={{
                                        borderTop: "1px solid var(--border-primary)",
                                        paddingTop: 12,
                                        marginTop: "auto",
                                    }}
                                >
                                    <div className="flex items-center justify-between text-sm">
                                        <span className="text-muted">Last run</span>
                                        {h?.last_run_status ? (
                                            <StatusBadge status={h.last_run_status} />
                                        ) : (
                                            <span className="text-muted">—</span>
                                        )}
                                    </div>
                                    <div className="text-xs text-muted" style={{ marginTop: 4 }}>
                                        {h ? formatTime(h.last_run_at) : "No runs recorded"}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

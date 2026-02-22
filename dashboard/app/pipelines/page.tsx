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

    // Track unsaved edits per pipeline
    const [editState, setEditState] = useState<
        Record<string, { timeout: string; saving: boolean }>
    >({});

    async function loadPipelines() {
        try {
            const data = await api.listPipelines();
            setPipelines(data.pipelines);

            // Initialize edit state
            const edits: Record<string, { timeout: string; saving: boolean }> = {};
            data.pipelines.forEach((p) => {
                edits[p.pipeline_name] = {
                    timeout: p.timeout_seconds != null ? String(p.timeout_seconds) : "",
                    saving: false,
                };
            });
            setEditState(edits);

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

    useEffect(() => {
        loadPipelines();
    }, []);

    async function handleToggleActive(p: PipelineDefinition) {
        const name = p.pipeline_name;
        setEditState((prev) => ({
            ...prev,
            [name]: { ...prev[name], saving: true },
        }));
        try {
            await api.updatePipelineSettings(name, { is_active: !p.is_active });
            setPipelines((prev) =>
                prev.map((pp) =>
                    pp.pipeline_name === name
                        ? { ...pp, is_active: !pp.is_active }
                        : pp
                )
            );
        } catch (err) {
            console.error("Toggle failed:", err);
        } finally {
            setEditState((prev) => ({
                ...prev,
                [name]: { ...prev[name], saving: false },
            }));
        }
    }

    async function handleSaveTimeout(name: string) {
        const edit = editState[name];
        if (!edit) return;

        setEditState((prev) => ({
            ...prev,
            [name]: { ...prev[name], saving: true },
        }));

        try {
            const val = edit.timeout.trim() === "" ? null : parseInt(edit.timeout, 10);
            await api.updatePipelineSettings(name, { timeout_seconds: val });
            setPipelines((prev) =>
                prev.map((pp) =>
                    pp.pipeline_name === name
                        ? { ...pp, timeout_seconds: val }
                        : pp
                )
            );
        } catch (err) {
            console.error("Save timeout failed:", err);
        } finally {
            setEditState((prev) => ({
                ...prev,
                [name]: { ...prev[name], saving: false },
            }));
        }
    }

    function hasUnsavedTimeout(p: PipelineDefinition): boolean {
        const edit = editState[p.pipeline_name];
        if (!edit) return false;
        const currentVal = p.timeout_seconds != null ? String(p.timeout_seconds) : "";
        return edit.timeout !== currentVal;
    }

    return (
        <div>
            <div className="page-header">
                <h2>Pipelines</h2>
                <p>Registered pipeline definitions, health status, and settings</p>
            </div>

            {loading ? (
                <div className="grid-3">
                    {[1, 2, 3].map((i) => (
                        <div key={i} className="shimmer" style={{ height: 220 }} />
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
                        const edit = editState[p.pipeline_name];
                        return (
                            <div key={p.id} className="card" style={{ position: "relative", display: "flex", flexDirection: "column" }}>
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

                                    <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
                                        <button
                                            className={`toggle ${p.is_active ? "active" : ""}`}
                                            onClick={() => handleToggleActive(p)}
                                            disabled={edit?.saving}
                                            title={p.is_active ? "Active — click to deactivate" : "Inactive — click to activate"}
                                        />
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

                                {/* Timeout Setting */}
                                <div
                                    style={{
                                        borderTop: "1px solid var(--border-primary)",
                                        paddingTop: 12,
                                        marginBottom: 12,
                                    }}
                                >
                                    <div className="flex items-center justify-between text-sm" style={{ marginBottom: 4 }}>
                                        <span className="text-muted">Timeout</span>
                                        <div className="flex items-center gap-8">
                                            <input
                                                type="number"
                                                className="inline-input"
                                                placeholder="None"
                                                value={edit?.timeout ?? ""}
                                                onChange={(e) =>
                                                    setEditState((prev) => ({
                                                        ...prev,
                                                        [p.pipeline_name]: {
                                                            ...prev[p.pipeline_name],
                                                            timeout: e.target.value,
                                                        },
                                                    }))
                                                }
                                                min={0}
                                            />
                                            <span className="text-xs text-muted">sec</span>
                                            {hasUnsavedTimeout(p) && (
                                                <button
                                                    className="btn btn-green btn-sm"
                                                    disabled={edit?.saving}
                                                    onClick={() =>
                                                        handleSaveTimeout(p.pipeline_name)
                                                    }
                                                >
                                                    {edit?.saving ? "…" : "Save"}
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                </div>

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

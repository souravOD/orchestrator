"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, OrchestrationRun, PipelineRun } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

function formatDuration(seconds: number | null): string {
    if (!seconds) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatDurationMs(ms: number | null): string {
    if (!ms) return "—";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

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

export default function RunDetailPage() {
    const { id } = useParams<{ id: string }>();
    const router = useRouter();
    const [run, setRun] = useState<OrchestrationRun | null>(null);
    const [pipelineRuns, setPipelineRuns] = useState<PipelineRun[]>([]);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [confirmAction, setConfirmAction] = useState<"cancel" | "retry" | null>(null);

    async function load() {
        try {
            const data = await api.getRunSteps(id);
            setRun(data.orchestration_run);
            setPipelineRuns(data.pipeline_runs);
        } catch (err) {
            console.error("Failed to load run:", err);
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        load();
    }, [id]);

    async function handleCancel() {
        setConfirmAction(null);
        setActionLoading("cancel");
        try {
            await api.cancelRun(id);
            await load();
        } catch (err) {
            console.error("Cancel failed:", err);
        } finally {
            setActionLoading(null);
        }
    }

    async function handleRetry() {
        setConfirmAction(null);
        setActionLoading("retry");
        try {
            await api.retryRun(id);
            router.push("/runs");
        } catch (err) {
            console.error("Retry failed:", err);
        } finally {
            setActionLoading(null);
        }
    }

    if (loading) {
        return (
            <div>
                <div className="page-header">
                    <h2>Run Detail</h2>
                </div>
                {[1, 2, 3].map((i) => (
                    <div key={i} className="shimmer shimmer-card" style={{ marginBottom: 16 }} />
                ))}
            </div>
        );
    }

    if (!run) {
        return (
            <div className="empty-state">
                <div className="empty-state-icon">❌</div>
                <p>Run not found</p>
                <Link href="/runs" style={{ marginTop: 12, display: "inline-block" }}>
                    ← Back to runs
                </Link>
            </div>
        );
    }

    const completedLayers = pipelineRuns.filter((p) => p.status === "completed").length;
    const totalLayers = run.layers?.length || pipelineRuns.length;
    const progress = totalLayers > 0 ? (completedLayers / totalLayers) * 100 : 0;
    const progressColor =
        run.status === "failed" ? "red" :
            run.status === "completed" ? "green" :
                run.status === "cancelled" ? "amber" :
                    "blue";

    const canCancel = ["running", "pending", "queued"].includes(run.status);
    const canRetry = run.status === "failed";

    return (
        <div>
            {/* Confirm Dialog */}
            {confirmAction && (
                <div className="confirm-overlay" onClick={() => setConfirmAction(null)}>
                    <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
                        <h3>
                            {confirmAction === "cancel" ? "Cancel Run?" : "Retry Run?"}
                        </h3>
                        <p>
                            {confirmAction === "cancel"
                                ? "This will send a cancellation signal. The run will stop at the next checkpoint."
                                : "This will create a new run that resumes from the last completed layer."}
                        </p>
                        <div className="confirm-actions">
                            <button
                                className="btn btn-ghost"
                                onClick={() => setConfirmAction(null)}
                            >
                                Cancel
                            </button>
                            <button
                                className={`btn ${confirmAction === "cancel" ? "btn-red" : "btn-blue"}`}
                                onClick={confirmAction === "cancel" ? handleCancel : handleRetry}
                            >
                                {confirmAction === "cancel" ? "Confirm Cancel" : "Confirm Retry"}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <div className="page-header">
                <div className="text-sm text-muted mb-2">
                    <Link href="/runs">← Runs</Link> / {id.slice(0, 8)}
                </div>
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-8">
                        <h2>{run.flow_name}{run.source_name ? ` — ${run.source_name}` : ''}</h2>
                        <StatusBadge status={run.status} />
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-8">
                        {canCancel && (
                            <button
                                className="btn btn-red"
                                disabled={actionLoading === "cancel"}
                                onClick={() => setConfirmAction("cancel")}
                            >
                                {actionLoading === "cancel" ? "Cancelling…" : "⏹ Cancel"}
                            </button>
                        )}
                        {canRetry && (
                            <button
                                className="btn btn-blue"
                                disabled={actionLoading === "retry"}
                                onClick={() => setConfirmAction("retry")}
                            >
                                {actionLoading === "retry" ? "Retrying…" : "🔄 Retry"}
                            </button>
                        )}
                    </div>
                </div>
                <p>
                    Triggered by {run.trigger_type}
                    {run.triggered_by ? ` (${run.triggered_by})` : ""} •{" "}
                    {formatTime(run.started_at)}
                </p>
            </div>

            {/* Summary Cards */}
            <div className="kpi-grid" style={{ marginBottom: 24 }}>
                <div className="kpi-card blue">
                    <div className="kpi-label">Duration</div>
                    <div className="kpi-value blue">{formatDuration(run.duration_seconds)}</div>
                </div>
                <div className="kpi-card green">
                    <div className="kpi-label">Records Written</div>
                    <div className="kpi-value green">{run.total_records_written.toLocaleString()}</div>
                </div>
                <div className="kpi-card amber">
                    <div className="kpi-label">DQ Issues</div>
                    <div className="kpi-value amber">{run.total_dq_issues}</div>
                </div>
                <div className="kpi-card red">
                    <div className="kpi-label">Errors</div>
                    <div className="kpi-value red">{run.total_errors}</div>
                </div>
            </div>

            {/* Progress */}
            <div className="section">
                <div className="section-title">
                    Pipeline Progress — {completedLayers}/{totalLayers} layers
                </div>
                <div className="progress-bar" style={{ marginBottom: 24 }}>
                    <div
                        className={`progress-fill ${progressColor}`}
                        style={{ width: `${progress}%` }}
                    />
                </div>
            </div>

            {/* Pipeline Runs */}
            {pipelineRuns.map((pr) => (
                <div key={pr.id} className="card" style={{ marginBottom: 16 }}>
                    <div className="flex items-center justify-between mb-2">
                        <div>
                            <span className="fw-600" style={{ fontSize: 14 }}>
                                {pr.pipeline_name}
                            </span>
                            <span className="text-xs text-muted" style={{ marginLeft: 8 }}>
                                {pr.id.slice(0, 8)}
                            </span>
                        </div>
                        <StatusBadge status={pr.status} />
                    </div>

                    <div className="text-sm text-muted mb-2">
                        {pr.records_written} records written •{" "}
                        {formatDuration(pr.duration_seconds)}
                        {pr.error_message && (
                            <span style={{ color: "var(--accent-red)", marginLeft: 8 }}>
                                ⚠ {pr.error_message}
                            </span>
                        )}
                    </div>

                    {/* Step Logs */}
                    {pr.steps && pr.steps.length > 0 && (
                        <div className="step-timeline" style={{ marginTop: 12 }}>
                            {pr.steps.map((step) => (
                                <div key={step.id} className="step-item">
                                    <div className={`step-dot ${step.status}`}>
                                        {step.status === "completed"
                                            ? "✓"
                                            : step.status === "failed"
                                                ? "✗"
                                                : step.step_order}
                                    </div>
                                    <div className="step-info">
                                        <div className="step-name">{step.step_name}</div>
                                        <div className="step-meta">
                                            <span>{step.records_in} in → {step.records_out} out</span>
                                            {step.records_error > 0 && (
                                                <span style={{ color: "var(--accent-red)" }}>
                                                    {step.records_error} errors
                                                </span>
                                            )}
                                            <span>{formatDurationMs(step.duration_ms)}</span>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            ))}

            {pipelineRuns.length === 0 && (
                <div className="empty-state">
                    <div className="empty-state-icon">📋</div>
                    <p>No pipeline runs recorded for this orchestration</p>
                </div>
            )}
        </div>
    );
}

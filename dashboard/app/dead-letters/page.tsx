"use client";

import { useEffect, useState } from "react";
import { api, DeadLetter } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

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

function payloadPreview(payload: Record<string, unknown>): string {
    const str = JSON.stringify(payload);
    return str.length > 80 ? str.slice(0, 80) + "…" : str;
}

const STATUS_FILTERS = [
    { label: "All", value: "" },
    { label: "Pending", value: "pending" },
    { label: "Retrying", value: "retrying" },
    { label: "Exhausted", value: "exhausted" },
    { label: "Resolved", value: "resolved" },
    { label: "Discarded", value: "discarded" },
];

export default function DeadLettersPage() {
    const [items, setItems] = useState<DeadLetter[]>([]);
    const [statusFilter, setStatusFilter] = useState("");
    const [loading, setLoading] = useState(true);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [actionLoading, setActionLoading] = useState<string | null>(null);

    async function load() {
        try {
            setLoading(true);
            const data = await api.listDeadLetters(
                statusFilter || undefined,
                100
            );
            setItems(data.dead_letters);
        } catch (err) {
            console.error("Failed to load dead letters:", err);
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        load();
    }, [statusFilter]);

    async function handleRetry(id: string) {
        setActionLoading(id);
        try {
            await api.retryDeadLetter(id);
            await load();
        } catch (err) {
            console.error("Retry failed:", err);
        } finally {
            setActionLoading(null);
        }
    }

    async function handleDiscard(id: string) {
        setActionLoading(id);
        try {
            await api.discardDeadLetter(id);
            await load();
        } catch (err) {
            console.error("Discard failed:", err);
        } finally {
            setActionLoading(null);
        }
    }

    const pendingCount = items.filter(
        (i) => i.status === "pending" || i.status === "retrying"
    ).length;
    const exhaustedCount = items.filter((i) => i.status === "exhausted").length;

    return (
        <div>
            <div className="page-header">
                <h2>Dead-Letter Queue</h2>
                <p>
                    Failed webhook events captured for retry •{" "}
                    {pendingCount > 0 && (
                        <span style={{ color: "var(--accent-amber)" }}>
                            {pendingCount} pending
                        </span>
                    )}
                    {exhaustedCount > 0 && (
                        <span style={{ color: "var(--accent-red)", marginLeft: 8 }}>
                            {exhaustedCount} exhausted
                        </span>
                    )}
                    {pendingCount === 0 && exhaustedCount === 0 && (
                        <span>No issues</span>
                    )}
                </p>
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
                        {[1, 2, 3, 4].map((i) => (
                            <div key={i} className="shimmer shimmer-row" />
                        ))}
                    </>
                ) : items.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">📬</div>
                        <p>
                            No dead letters
                            {statusFilter ? ` with status "${statusFilter}"` : ""}
                        </p>
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Payload</th>
                                <th>Error</th>
                                <th>Status</th>
                                <th>Retries</th>
                                <th>Next Retry</th>
                                <th>Created</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((item) => (
                                <>
                                    <tr
                                        key={item.id}
                                        onClick={() =>
                                            setExpandedId(
                                                expandedId === item.id ? null : item.id
                                            )
                                        }
                                    >
                                        <td>
                                            <span
                                                className="mono text-muted"
                                                title={JSON.stringify(item.payload, null, 2)}
                                            >
                                                {payloadPreview(item.payload)}
                                            </span>
                                        </td>
                                        <td>
                                            <span
                                                className="text-sm"
                                                style={{
                                                    color: "var(--accent-red)",
                                                    maxWidth: 200,
                                                    display: "inline-block",
                                                    overflow: "hidden",
                                                    textOverflow: "ellipsis",
                                                    whiteSpace: "nowrap",
                                                }}
                                            >
                                                {item.error_message || "—"}
                                            </span>
                                        </td>
                                        <td>
                                            <StatusBadge status={item.status} />
                                        </td>
                                        <td className="mono">
                                            {item.retry_count}/{item.max_retries}
                                        </td>
                                        <td className="text-sm text-muted">
                                            {formatTime(item.next_retry_at)}
                                        </td>
                                        <td className="text-sm text-muted">
                                            {formatTime(item.created_at)}
                                        </td>
                                        <td>
                                            <div
                                                className="flex gap-8"
                                                onClick={(e) => e.stopPropagation()}
                                            >
                                                {(item.status === "pending" ||
                                                    item.status === "exhausted") && (
                                                        <button
                                                            className="btn btn-blue btn-sm"
                                                            disabled={actionLoading === item.id}
                                                            onClick={() => handleRetry(item.id)}
                                                        >
                                                            {actionLoading === item.id
                                                                ? "…"
                                                                : "Retry"}
                                                        </button>
                                                    )}
                                                {item.status !== "discarded" &&
                                                    item.status !== "resolved" && (
                                                        <button
                                                            className="btn btn-ghost btn-sm"
                                                            disabled={
                                                                actionLoading === item.id
                                                            }
                                                            onClick={() =>
                                                                handleDiscard(item.id)
                                                            }
                                                        >
                                                            Discard
                                                        </button>
                                                    )}
                                            </div>
                                        </td>
                                    </tr>

                                    {/* Expanded payload panel */}
                                    {expandedId === item.id && (
                                        <tr key={`${item.id}-detail`}>
                                            <td colSpan={7} style={{ padding: "0 14px 14px" }}>
                                                <div className="payload-panel">
                                                    <div
                                                        className="text-xs fw-600"
                                                        style={{
                                                            marginBottom: 8,
                                                            color: "var(--text-muted)",
                                                        }}
                                                    >
                                                        PAYLOAD
                                                    </div>
                                                    <pre>
                                                        {JSON.stringify(
                                                            item.payload,
                                                            null,
                                                            2
                                                        )}
                                                    </pre>
                                                    {item.error_details && (
                                                        <>
                                                            <div
                                                                className="text-xs fw-600"
                                                                style={{
                                                                    marginTop: 12,
                                                                    marginBottom: 8,
                                                                    color: "var(--accent-red)",
                                                                }}
                                                            >
                                                                ERROR DETAILS
                                                            </div>
                                                            <pre>
                                                                {JSON.stringify(
                                                                    item.error_details,
                                                                    null,
                                                                    2
                                                                )}
                                                            </pre>
                                                        </>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                    )}
                                </>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}

"use client";

import { useEffect, useState } from "react";
import { api, DataSource } from "@/lib/api";
import KPICard from "@/components/KPICard";

const CATEGORIES = ["all", "recipes", "products", "nutrition", "reviews"];
const STATUSES = ["all", "available", "ingesting", "completed", "archived"];

function formatNumber(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function formatBytes(bytes: number | null): string {
    if (!bytes) return "—";
    if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
    if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(0)} KB`;
    return `${bytes} B`;
}

function formatTime(ts: string | null): string {
    if (!ts) return "Never";
    return new Date(ts).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export default function DataSourcesPage() {
    const [sources, setSources] = useState<DataSource[]>([]);
    const [loading, setLoading] = useState(true);
    const [categoryFilter, setCategoryFilter] = useState("all");
    const [statusFilter, setStatusFilter] = useState("all");
    const [error, setError] = useState<string | null>(null);

    // Ingest modal state
    const [modalSource, setModalSource] = useState<DataSource | null>(null);
    const [modalBatchSize, setModalBatchSize] = useState(100);
    const [modalRecordCount, setModalRecordCount] = useState(500);
    const [modalDryRun, setModalDryRun] = useState(false);
    const [modalSubmitting, setModalSubmitting] = useState(false);

    useEffect(() => {
        async function load() {
            try {
                setLoading(true);
                const cat = categoryFilter === "all" ? undefined : categoryFilter;
                const stat = statusFilter === "all" ? undefined : statusFilter;
                const data = await api.listDataSources(cat, stat);
                setSources(data.data_sources);
            } catch (err) {
                console.error("Failed to load data sources:", err);
                setError(err instanceof Error ? err.message : String(err));
            } finally {
                setLoading(false);
            }
        }
        load();
    }, [categoryFilter, statusFilter]);

    // Compute KPIs
    const totalSources = sources.length;
    const totalRecords = sources.reduce((sum, s) => sum + (s.total_records || 0), 0);
    const totalIngested = sources.reduce((sum, s) => sum + (s.total_ingested || 0), 0);
    const remaining = totalRecords - totalIngested;

    function getProgress(src: DataSource): number {
        if (!src.total_records) return 0;
        return Math.min(100, Math.round((src.total_ingested / src.total_records) * 100));
    }

    function getSourceStatus(src: DataSource): string {
        if (src.status === "completed") return "completed";
        if (src.status === "ingesting") return "ingesting";
        return "available";
    }

    function getRemainingRecords(src: DataSource): number {
        const ingested = src.total_ingested || 0;
        const cursor = src.latest_cursor;
        const effectiveIngested = cursor ? cursor.cursor_end : ingested;
        return Math.max(0, (src.total_records || 0) - effectiveIngested);
    }

    function openModal(src: DataSource) {
        setModalSource(src);
        const rem = getRemainingRecords(src);
        setModalRecordCount(Math.min(500, rem));
        setModalBatchSize(100);
        setModalDryRun(false);
    }

    async function handleIngest() {
        if (!modalSource) return;
        setModalSubmitting(true);
        try {
            const result = await api.triggerDataSourceIngest(modalSource.id, {
                batch_size: modalBatchSize,
                record_count: modalRecordCount,
                dry_run: modalDryRun,
            });
            if (result.run_id) {
                alert(`✅ Ingestion started! Run ID: ${result.run_id.slice(0, 8)}...\n\nGo to Console page to monitor.`);
                setModalSource(null);
            }
        } catch (err) {
            alert(`❌ Failed: ${err instanceof Error ? err.message : err}`);
        } finally {
            setModalSubmitting(false);
        }
    }

    return (
        <div>
            <div className="page-header">
                <h2>🗄️ Data Source Registry</h2>
                <p>Manage and trigger ingestion from Supabase Storage sources</p>
            </div>

            {/* KPI Row */}
            <div className="kpi-grid" style={{ marginBottom: 20 }}>
                <KPICard label="Total Sources" value={totalSources} color="blue" />
                <KPICard
                    label="Total Records"
                    value={formatNumber(totalRecords)}
                    color="cyan"
                />
                <KPICard
                    label="Ingested"
                    value={formatNumber(totalIngested)}
                    color="green"
                    sub={`${totalRecords > 0 ? Math.round((totalIngested / totalRecords) * 100) : 0}%`}
                />
                <KPICard
                    label="Remaining"
                    value={formatNumber(remaining)}
                    color="amber"
                />
            </div>

            {/* Filters */}
            <div className="filter-bar" style={{ marginBottom: 16 }}>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {CATEGORIES.map((c) => (
                        <button
                            key={c}
                            className={`filter-btn ${categoryFilter === c ? "active" : ""}`}
                            onClick={() => setCategoryFilter(c)}
                        >
                            {c.charAt(0).toUpperCase() + c.slice(1)}
                        </button>
                    ))}
                </div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {STATUSES.map((s) => (
                        <button
                            key={s}
                            className={`filter-btn ${statusFilter === s ? "active" : ""}`}
                            onClick={() => setStatusFilter(s)}
                        >
                            {s.charAt(0).toUpperCase() + s.slice(1)}
                        </button>
                    ))}
                </div>
            </div>

            {/* Error */}
            {error && (
                <div
                    className="card"
                    style={{
                        borderColor: "rgba(239, 68, 68, 0.3)",
                        background: "var(--accent-red-glow)",
                        marginBottom: 16,
                        padding: 12,
                        fontSize: 13,
                        color: "var(--accent-red)",
                    }}
                >
                    ❌ {error}
                </div>
            )}

            {/* Source List */}
            <div className="card">
                {loading ? (
                    <>
                        {[1, 2, 3, 4, 5].map((i) => (
                            <div key={i} className="shimmer shimmer-row" />
                        ))}
                    </>
                ) : sources.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">🔍</div>
                        <p>No data sources found</p>
                    </div>
                ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        {sources.map((src) => {
                            const pct = getProgress(src);
                            const status = getSourceStatus(src);
                            const rem = getRemainingRecords(src);

                            return (
                                <div key={src.id} className={`source-card ${status}`}>
                                    <div className="source-info">
                                        <div className="source-name">{src.source_name}</div>
                                        <div className="source-meta">
                                            <span>{src.file_format.toUpperCase()}</span>
                                            <span>{formatNumber(src.total_records)} records</span>
                                            <span>{formatBytes(src.file_size_bytes)}</span>
                                            <span>→ {src.target_table}</span>
                                        </div>
                                    </div>

                                    <div className="source-progress-section">
                                        <div className="source-progress-label">
                                            <span>{pct}%</span>
                                            <span>
                                                {formatNumber(src.total_ingested)} / {formatNumber(src.total_records)}
                                            </span>
                                        </div>
                                        <div className="progress-bar">
                                            <div
                                                className={`progress-fill ${pct === 100 ? "green" : "blue"}`}
                                                style={{ width: `${pct}%` }}
                                            />
                                        </div>
                                    </div>

                                    <div className="source-actions">
                                        {status === "completed" ? (
                                            <span className="badge completed">✅ Done</span>
                                        ) : (
                                            <button
                                                className="btn btn-blue btn-sm"
                                                onClick={() => openModal(src)}
                                                disabled={rem <= 0}
                                            >
                                                ▶ Ingest
                                            </button>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* Ingest Modal */}
            {modalSource && (
                <div className="confirm-overlay" onClick={() => setModalSource(null)}>
                    <div
                        className="confirm-dialog modal-wide"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <h3>Ingest: {modalSource.source_name}</h3>
                        <p>
                            Start ingesting records from{" "}
                            <code style={{ color: "var(--accent-blue)" }}>
                                {modalSource.storage_path}
                            </code>
                        </p>

                        <div className="modal-body">
                            <div className="modal-info-row">
                                <span className="modal-info-label">Storage</span>
                                <span className="modal-info-value">
                                    {modalSource.storage_bucket}/{modalSource.storage_path}
                                </span>
                            </div>
                            <div className="modal-info-row">
                                <span className="modal-info-label">Cursor Position</span>
                                <span className="modal-info-value">
                                    {modalSource.latest_cursor
                                        ? modalSource.latest_cursor.cursor_end.toLocaleString()
                                        : "0"}
                                </span>
                            </div>
                            <div className="modal-info-row">
                                <span className="modal-info-label">Remaining</span>
                                <span className="modal-info-value">
                                    {getRemainingRecords(modalSource).toLocaleString()} records
                                </span>
                            </div>

                            <hr style={{ border: "none", borderTop: "1px solid var(--border-subtle)" }} />

                            <div className="form-group">
                                <label className="form-label">Batch Size</label>
                                <input
                                    type="number"
                                    className="form-input"
                                    min={1}
                                    max={10000}
                                    value={modalBatchSize}
                                    onChange={(e) => setModalBatchSize(Number(e.target.value))}
                                />
                            </div>

                            <div className="form-group">
                                <label className="form-label">
                                    Records to Ingest: {modalRecordCount.toLocaleString()}
                                </label>
                                <input
                                    type="range"
                                    className="form-range"
                                    min={1}
                                    max={getRemainingRecords(modalSource)}
                                    value={modalRecordCount}
                                    onChange={(e) => setModalRecordCount(Number(e.target.value))}
                                />
                                <div className="form-range-labels">
                                    <span>1</span>
                                    <span>{getRemainingRecords(modalSource).toLocaleString()}</span>
                                </div>
                            </div>

                            <div className="form-toggle-group">
                                <span className="form-toggle-label">Dry Run</span>
                                <button
                                    className={`toggle ${modalDryRun ? "active" : ""}`}
                                    onClick={() => setModalDryRun(!modalDryRun)}
                                />
                            </div>
                        </div>

                        <div className="modal-footer">
                            <button
                                className="btn btn-ghost"
                                onClick={() => setModalSource(null)}
                            >
                                Cancel
                            </button>
                            <button
                                className="btn btn-green"
                                onClick={handleIngest}
                                disabled={modalSubmitting}
                            >
                                {modalSubmitting ? "Starting..." : "▶ Start Ingestion"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

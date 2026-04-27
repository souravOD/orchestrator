"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { API_BASE, api, BucketLogFile } from "@/lib/api";

interface LogLine {
    type: "log" | "raw" | "complete" | "command";
    timestamp?: string;
    level?: "INFO" | "WARNING" | "ERROR";
    step?: string;
    pipeline?: string;
    status?: string;
    records_in?: number;
    records_out?: number;
    duration_ms?: number | null;
    error?: string | null;
    message?: string;
    duration_seconds?: number;
    total_records_written?: number;
    stream?: "stdout" | "stderr";
}

interface TerminalProps {
    runId: string | null;
    command: string;
    environment?: "production" | "testing";
    onComplete?: (status: string) => void;
    /** If true, skip SSE and load bucket logs directly (for historical runs) */
    historicalMode?: boolean;
}

type TabMode = "live" | "stored";

function formatTs(ts?: string): string {
    if (!ts) return "";
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

export default function Terminal({
    runId,
    command,
    environment,
    onComplete,
    historicalMode = false,
}: TerminalProps) {
    const [lines, setLines] = useState<LogLine[]>([]);
    const [streaming, setStreaming] = useState(false);
    const [finalStatus, setFinalStatus] = useState<string | null>(null);
    const bodyRef = useRef<HTMLDivElement>(null);
    const [autoscroll, setAutoscroll] = useState(true);
    const [cancelling, setCancelling] = useState(false);

    // Stored logs state
    const [activeTab, setActiveTab] = useState<TabMode>("live");
    const [bucketFiles, setBucketFiles] = useState<BucketLogFile[]>([]);
    const [loadingBucket, setLoadingBucket] = useState(false);
    const [bucketContent, setBucketContent] = useState<Record<string, unknown>>({});
    const [expandedFile, setExpandedFile] = useState<string | null>(null);

    // Auto-scroll to bottom
    useEffect(() => {
        if (autoscroll && bodyRef.current) {
            bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
        }
    }, [lines, autoscroll, bucketContent]);

    // SSE connection for live mode
    useEffect(() => {
        if (!runId) return;
        if (historicalMode) {
            // In historical mode, clear any stale live-stream state before
            // loading bucket logs so old terminal lines don't persist visually.
            setStreaming(false);
            setLines([]);
            setActiveTab("stored");
            loadBucketLogs(runId);
            return;
        }

        setStreaming(true);
        setFinalStatus(null);
        setActiveTab("live");
        setCancelling(false);

        // Add command echo as first line
        setLines([
            { type: "command", message: command, timestamp: new Date().toISOString() },
        ]);

        const envParam = environment && environment !== "production" ? `?environment=${environment}` : "";
        const eventSource = new EventSource(`${API_BASE}/api/runs/${runId}/logs${envParam}`);

        eventSource.onmessage = (event) => {
            try {
                const data: LogLine = JSON.parse(event.data);

                if (data.type === "complete") {
                    setFinalStatus(data.status || "completed");
                    setStreaming(false);
                    eventSource.close();
                    onComplete?.(data.status || "completed");
                    // Auto-load bucket logs when run completes
                    if (runId) loadBucketLogs(runId);
                } else {
                    setLines((prev) => [...prev, data]);
                }
            } catch {
                // Ignore parse errors
            }
        };

        eventSource.onerror = () => {
            setStreaming(false);
            eventSource.close();
        };

        return () => {
            eventSource.close();
            setStreaming(false);
        };
    }, [runId, historicalMode]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Bucket Log Functions ──────────────────────────

    const loadBucketLogs = useCallback(async (rid: string) => {
        setLoadingBucket(true);
        try {
            const res = await api.getBucketLogs(rid, environment);
            setBucketFiles(res.files || []);
        } catch (err) {
            console.error("Failed to load bucket logs:", err);
            setBucketFiles([]);
        } finally {
            setLoadingBucket(false);
        }
    }, [environment]);

    const loadBucketFile = useCallback(async (filename: string) => {
        if (!runId) return;
        if (expandedFile === filename) {
            setExpandedFile(null);
            return;
        }
        setExpandedFile(filename);
        if (bucketContent[filename]) return; // Already loaded
        try {
            const res = await api.getBucketLogFile(runId, filename, environment);
            setBucketContent((prev) => ({ ...prev, [filename]: res.content }));
        } catch (err) {
            console.error("Failed to load bucket file:", err);
            setBucketContent((prev) => ({ ...prev, [filename]: { error: "Failed to load" } }));
        }
    }, [runId, environment, expandedFile, bucketContent]);

    // ── Action Handlers ──────────────────────────────

    const handleStop = useCallback(async () => {
        if (!runId || cancelling) return;
        setCancelling(true);
        try {
            await api.cancelRun(runId, environment);
            setLines((prev) => [
                ...prev,
                {
                    type: "raw" as const,
                    stream: "stderr" as const,
                    message: "⛔ Run cancelled by user",
                    timestamp: new Date().toISOString(),
                },
            ]);
        } catch (err) {
            console.error("Failed to cancel run:", err);
        }
    }, [runId, environment, cancelling]);

    const handleCopy = useCallback(() => {
        const text = lines
            .map((l) => {
                if (l.type === "command") return `$ ${l.message}`;
                if (l.type === "raw") return l.message || "";
                return `[${formatTs(l.timestamp)}] ${l.level} ${l.step || l.pipeline || ""} ${l.status || ""} ${l.error || ""}`.trim();
            })
            .join("\n");
        navigator.clipboard.writeText(text);
    }, [lines]);

    const handleDownload = useCallback(() => {
        const text = lines
            .map((l) => JSON.stringify(l))
            .join("\n");
        const blob = new Blob([text], { type: "text/plain" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `run-${runId || "unknown"}.log`;
        a.click();
        URL.revokeObjectURL(url);
    }, [lines, runId]);

    const handleClear = useCallback(() => {
        setLines([]);
        setFinalStatus(null);
    }, []);

    // ── Render Helpers ──────────────────────────────

    const renderLogLine = (line: LogLine, i: number) => {
        if (line.type === "command") {
            return (
                <div key={i} className="terminal-line">
                    <span className="terminal-prompt">$</span>
                    <span className="terminal-message">{line.message}</span>
                </div>
            );
        }

        if (line.type === "raw") {
            const isErr = line.stream === "stderr";
            return (
                <div key={i} className={`terminal-line raw ${isErr ? "stderr" : "stdout"}`}>
                    <span className="terminal-message" style={{
                        color: isErr ? "var(--accent-amber, #f59e0b)" : "var(--terminal-raw, #a3e635)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                    }}>
                        {line.message}
                    </span>
                </div>
            );
        }

        const level = (line.level || "INFO").toLowerCase();
        const stepMsg = [
            line.pipeline && `[${line.pipeline}]`,
            line.step,
            line.status && `→ ${line.status}`,
            line.records_out != null && line.records_out > 0 &&
                `(${line.records_out.toLocaleString()} records)`,
            line.duration_ms != null && `${line.duration_ms}ms`,
            line.error && `❌ ${line.error}`,
        ]
            .filter(Boolean)
            .join(" ");

        return (
            <div key={i} className={`terminal-line ${level}`}>
                <span className="terminal-timestamp">{formatTs(line.timestamp)}</span>
                <span className="terminal-level">{(line.level || "INFO").padEnd(7)}</span>
                <span className="terminal-message">{stepMsg}</span>
            </div>
        );
    };

    const renderBucketContent = (content: unknown) => {
        if (!content) return <div style={{ color: "var(--text-muted)", padding: 8 }}>Loading...</div>;

        if (typeof content === "string") {
            return <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, padding: 8 }}>{content}</pre>;
        }

        // Render JSON with collapsible structure
        return (
            <pre style={{
                whiteSpace: "pre-wrap",
                fontSize: 11,
                padding: 8,
                color: "var(--terminal-raw, #a3e635)",
                background: "rgba(0, 0, 0, 0.2)",
                borderRadius: 4,
                margin: "4px 0",
                maxHeight: 400,
                overflow: "auto",
            }}>
                {JSON.stringify(content, null, 2)}
            </pre>
        );
    };

    const hasStoredLogs = bucketFiles.length > 0;
    const showTabs = hasStoredLogs || finalStatus || historicalMode;

    return (
        <div className="terminal">
            <div className="terminal-header">
                <div className="terminal-title">
                    <span>{streaming ? "🟢" : "⚪"}</span>
                    <span>Terminal</span>
                    {streaming && (
                        <span className="live-indicator">
                            <span className="live-dot" />
                            LIVE
                        </span>
                    )}
                </div>
                <div className="terminal-actions">
                    {showTabs && (
                        <>
                            <button
                                className={`btn btn-ghost btn-sm ${activeTab === "live" ? "active" : ""}`}
                                onClick={() => setActiveTab("live")}
                                title="Live Output"
                                style={{
                                    fontSize: 11,
                                    padding: "2px 8px",
                                    borderBottom: activeTab === "live" ? "2px solid var(--accent-green)" : "2px solid transparent",
                                }}
                            >
                                ▶ Live
                            </button>
                            <button
                                className={`btn btn-ghost btn-sm ${activeTab === "stored" ? "active" : ""}`}
                                onClick={() => {
                                    setActiveTab("stored");
                                    if (runId && bucketFiles.length === 0 && !loadingBucket) {
                                        loadBucketLogs(runId);
                                    }
                                }}
                                title="Stored Logs"
                                style={{
                                    fontSize: 11,
                                    padding: "2px 8px",
                                    borderBottom: activeTab === "stored" ? "2px solid var(--accent-blue, #3b82f6)" : "2px solid transparent",
                                }}
                            >
                                📂 Stored {hasStoredLogs && `(${bucketFiles.length})`}
                            </button>
                            <span style={{ width: 1, height: 16, background: "var(--border)", margin: "0 4px" }} />
                        </>
                    )}
                    {streaming && (
                        <button
                            id="stop-run-btn"
                            className="btn btn-sm"
                            onClick={handleStop}
                            disabled={cancelling}
                            title="Stop this run"
                            style={{
                                background: cancelling
                                    ? "rgba(239, 68, 68, 0.3)"
                                    : "rgba(239, 68, 68, 0.15)",
                                border: "1px solid rgba(239, 68, 68, 0.5)",
                                color: "var(--accent-red, #ef4444)",
                                fontSize: 11,
                                padding: "3px 10px",
                                fontWeight: 600,
                                cursor: cancelling ? "not-allowed" : "pointer",
                                transition: "all 0.2s ease",
                            }}
                        >
                            {cancelling ? "⏳ Stopping..." : "⏹ Stop"}
                        </button>
                    )}
                    <span style={{ width: 1, height: 16, background: "var(--border)", margin: "0 4px" }} />
                    <button className="btn btn-ghost btn-sm" onClick={handleCopy} title="Copy logs">📋</button>
                    <button className="btn btn-ghost btn-sm" onClick={handleDownload} title="Download logs" disabled={lines.length === 0}>💾</button>
                    <button className="btn btn-ghost btn-sm" onClick={handleClear} title="Clear">🗑️</button>
                    <button
                        className={`btn btn-ghost btn-sm ${autoscroll ? "" : "active"}`}
                        onClick={() => setAutoscroll(!autoscroll)}
                        title={autoscroll ? "Auto-scroll ON" : "Auto-scroll OFF"}
                    >
                        {autoscroll ? "📌" : "📍"}
                    </button>
                </div>
            </div>

            <div className="terminal-body" ref={bodyRef}>
                {activeTab === "live" ? (
                    // ── LIVE TAB ──
                    <>
                        {lines.length === 0 && !streaming ? (
                            <div className="terminal-empty">
                                <div className="terminal-empty-icon">⌨️</div>
                                <div>Select a flow and click Execute to start</div>
                            </div>
                        ) : (
                            <>
                                {lines.map((line, i) => renderLogLine(line, i))}
                                {streaming && <span className="terminal-cursor" />}
                            </>
                        )}
                    </>
                ) : (
                    // ── STORED LOGS TAB ──
                    <div style={{ padding: 8 }}>
                        {loadingBucket ? (
                            <div style={{ color: "var(--text-muted)", textAlign: "center", padding: 24 }}>
                                ⏳ Loading stored logs...
                            </div>
                        ) : bucketFiles.length === 0 ? (
                            <div style={{ color: "var(--text-muted)", textAlign: "center", padding: 24 }}>
                                <div style={{ fontSize: 24, marginBottom: 8 }}>📂</div>
                                <div>No stored logs found for this run</div>
                                <div style={{ fontSize: 11, marginTop: 4 }}>
                                    Logs are uploaded to Supabase Storage after a run completes
                                </div>
                            </div>
                        ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                {bucketFiles.map((file) => (
                                    <div key={file.name} style={{
                                        border: "1px solid var(--border)",
                                        borderRadius: 6,
                                        overflow: "hidden",
                                    }}>
                                        <button
                                            onClick={() => loadBucketFile(file.name)}
                                            style={{
                                                width: "100%",
                                                display: "flex",
                                                alignItems: "center",
                                                justifyContent: "space-between",
                                                padding: "8px 12px",
                                                background: expandedFile === file.name
                                                    ? "rgba(59, 130, 246, 0.1)"
                                                    : "rgba(255, 255, 255, 0.02)",
                                                border: "none",
                                                cursor: "pointer",
                                                color: "var(--text-primary)",
                                                fontSize: 12,
                                                fontFamily: "var(--font-mono)",
                                                transition: "background 0.15s ease",
                                            }}
                                        >
                                            <span>
                                                {file.name === "summary.json" && "📊 "}
                                                {file.name === "step_logs.json" && "📝 "}
                                                {file.name === "pipeline_runs.json" && "🔄 "}
                                                {file.name}
                                            </span>
                                            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
                                                {expandedFile === file.name ? "▼" : "▶"}
                                            </span>
                                        </button>
                                        {expandedFile === file.name && (
                                            <div style={{
                                                borderTop: "1px solid var(--border)",
                                                maxHeight: 400,
                                                overflow: "auto",
                                            }}>
                                                {renderBucketContent(bucketContent[file.name])}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}
            </div>

            {finalStatus && (
                <div className={`terminal-status ${finalStatus}`}>
                    <span>{finalStatus === "completed" ? "✅" : "❌"}</span>
                    <span>
                        {finalStatus === "completed" ? "Completed" : `Failed (${finalStatus})`}
                    </span>
                    {lines.find((l) => l.type === "complete") && (
                        <span className="text-muted text-sm" style={{ marginLeft: "auto" }}>
                            {(() => {
                                const c = lines.find((l) => l.type === "complete");
                                if (!c) return "";
                                const parts: string[] = [];
                                if (c.duration_seconds) parts.push(`${c.duration_seconds.toFixed(1)}s`);
                                if (c.total_records_written) parts.push(`${c.total_records_written.toLocaleString()} records`);
                                return parts.join(" · ");
                            })()}
                        </span>
                    )}
                    {hasStoredLogs && activeTab !== "stored" && (
                        <button
                            onClick={() => setActiveTab("stored")}
                            style={{
                                marginLeft: 8,
                                padding: "2px 8px",
                                fontSize: 11,
                                border: "1px solid var(--border)",
                                borderRadius: 4,
                                background: "transparent",
                                color: "var(--accent-blue, #3b82f6)",
                                cursor: "pointer",
                            }}
                        >
                            📂 View Stored Logs
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

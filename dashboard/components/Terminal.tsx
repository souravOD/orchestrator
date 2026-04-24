"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { API_BASE } from "@/lib/api";

interface LogLine {
    type: "log" | "complete" | "command";
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
}

interface TerminalProps {
    runId: string | null;
    command: string;
    onComplete?: (status: string) => void;
}

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

export default function Terminal({ runId, command, onComplete }: TerminalProps) {
    const [lines, setLines] = useState<LogLine[]>([]);
    const [streaming, setStreaming] = useState(false);
    const [finalStatus, setFinalStatus] = useState<string | null>(null);
    const bodyRef = useRef<HTMLDivElement>(null);
    const [autoscroll, setAutoscroll] = useState(true);

    // Auto-scroll to bottom
    useEffect(() => {
        if (autoscroll && bodyRef.current) {
            bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
        }
    }, [lines, autoscroll]);

    // SSE connection
    useEffect(() => {
        if (!runId) return;

        setStreaming(true);
        setFinalStatus(null);

        // Add command echo as first line
        setLines([
            { type: "command", message: command, timestamp: new Date().toISOString() },
        ]);

        const eventSource = new EventSource(`${API_BASE}/api/runs/${runId}/logs`);

        eventSource.onmessage = (event) => {
            try {
                const data: LogLine = JSON.parse(event.data);

                if (data.type === "complete") {
                    setFinalStatus(data.status || "completed");
                    setStreaming(false);
                    eventSource.close();
                    onComplete?.(data.status || "completed");
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
    }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleCopy = useCallback(() => {
        const text = lines
            .map((l) => {
                if (l.type === "command") return `$ ${l.message}`;
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
                    <button
                        className="btn btn-ghost btn-sm"
                        onClick={handleCopy}
                        title="Copy logs"
                    >
                        📋
                    </button>
                    <button
                        className="btn btn-ghost btn-sm"
                        onClick={handleDownload}
                        title="Download logs"
                        disabled={lines.length === 0}
                    >
                        💾
                    </button>
                    <button
                        className="btn btn-ghost btn-sm"
                        onClick={handleClear}
                        title="Clear"
                    >
                        🗑️
                    </button>
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
                {lines.length === 0 && !streaming ? (
                    <div className="terminal-empty">
                        <div className="terminal-empty-icon">⌨️</div>
                        <div>Select a flow and click Execute to start</div>
                    </div>
                ) : (
                    <>
                        {lines.map((line, i) => {
                            if (line.type === "command") {
                                return (
                                    <div key={i} className="terminal-line">
                                        <span className="terminal-prompt">$</span>
                                        <span className="terminal-message">
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
                                line.duration_ms != null &&
                                    `${line.duration_ms}ms`,
                                line.error && `❌ ${line.error}`,
                            ]
                                .filter(Boolean)
                                .join(" ");

                            return (
                                <div key={i} className={`terminal-line ${level}`}>
                                    <span className="terminal-timestamp">
                                        {formatTs(line.timestamp)}
                                    </span>
                                    <span className="terminal-level">
                                        {(line.level || "INFO").padEnd(7)}
                                    </span>
                                    <span className="terminal-message">{stepMsg}</span>
                                </div>
                            );
                        })}
                        {streaming && <span className="terminal-cursor" />}
                    </>
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
                </div>
            )}
        </div>
    );
}

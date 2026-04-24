"use client";

import { useEffect, useState, useCallback } from "react";
import { api, FlowDefinition, SourceNameItem, TestStatus } from "@/lib/api";
import Terminal from "@/components/Terminal";

// Flows that support source_name parameter
const SOURCE_FLOWS = new Set([
    "full_ingestion",
    "single_layer",
    "multi_source_ingestion",
]);

// Flows that support parallel workers
const PARALLEL_FLOWS = new Set([
    "full_ingestion",
    "multi_source_ingestion",
]);

// Available layers for single_layer flow
const LAYERS = [
    { value: "prebronze_to_bronze", label: "P2B (Pre-Bronze → Bronze)" },
    { value: "bronze_to_silver", label: "B2S (Bronze → Silver)" },
    { value: "silver_to_gold", label: "S2G (Silver → Gold)" },
    { value: "gold_to_neo4j", label: "G2N (Gold → Neo4j)" },
    { value: "usda_nutrition_fetch", label: "USDA Nutrition Fetch" },
];

interface HistoryItem {
    command: string;
    flow: string;
    timestamp: string;
}

function buildCommand(
    flow: string,
    opts: {
        sourceName: string;
        batchSize: number;
        incremental: boolean;
        dryRun: boolean;
        workers: number;
        layer: string;
    }
): string {
    const parts = ["orchestrator run", `--flow ${flow}`];
    if (SOURCE_FLOWS.has(flow) && opts.sourceName) {
        parts.push(`--source ${opts.sourceName}`);
    }
    if (flow === "single_layer" && opts.layer) {
        parts.push(`--layer ${opts.layer}`);
    }
    parts.push(`--batch-size ${opts.batchSize}`);
    if (PARALLEL_FLOWS.has(flow)) {
        parts.push(`--workers ${opts.workers}`);
    }
    if (!opts.incremental) parts.push("--full");
    if (opts.dryRun) parts.push("--dry-run");
    return parts.join(" ");
}

export default function ConsolePage() {
    const [flows, setFlows] = useState<FlowDefinition[]>([]);
    const [loading, setLoading] = useState(true);

    // Form state
    const [selectedFlow, setSelectedFlow] = useState("full_ingestion");
    const [sourceName, setSourceName] = useState("");
    const [batchSize, setBatchSize] = useState(100);
    const [incremental, setIncremental] = useState(true);
    const [dryRun, setDryRun] = useState(false);
    const [workers, setWorkers] = useState(5);
    const [layer, setLayer] = useState("prebronze_to_bronze");

    // Source names from API
    const [sourceNames, setSourceNames] = useState<SourceNameItem[]>([]);

    // Environment toggle
    const [environment, setEnvironment] = useState<"production" | "testing">("production");
    const [testConfigured, setTestConfigured] = useState(false);

    // Execution state
    const [executing, setExecuting] = useState(false);
    const [activeRunId, setActiveRunId] = useState<string | null>(null);
    const [activeCommand, setActiveCommand] = useState("$ waiting...");
    const [error, setError] = useState<string | null>(null);
    const [history, setHistory] = useState<HistoryItem[]>([]);

    // Load flows and test status (once on mount)
    useEffect(() => {
        async function load() {
            try {
                const [flowData, testStatus] = await Promise.all([
                    api.listFlows(),
                    api.getTestStatus(),
                ]);
                setFlows(flowData.flows);
                setTestConfigured(testStatus.configured);
            } catch (err) {
                console.error("Failed to load console data:", err);
            } finally {
                setLoading(false);
            }
        }
        load();

        // Load history from localStorage
        try {
            const stored = localStorage.getItem("console-history");
            if (stored) setHistory(JSON.parse(stored));
        } catch { /* ignore */ }
    }, []);

    // Re-fetch source names when environment toggles
    useEffect(() => {
        let isCurrent = true;
        async function loadSources() {
            try {
                const sourceData = await api.listSourceNames(environment);
                if (!isCurrent) return;
                setSourceNames(sourceData.sources);
                // Reset selection when source list changes
                setSourceName("");
            } catch (err) {
                console.error("Failed to load source names:", err);
            }
        }
        loadSources();
        return () => {
            isCurrent = false;
        };
    }, [environment]);

    const currentFlowDesc = flows.find((f) => f.name === selectedFlow)?.description || "";

    const handleExecute = useCallback(async () => {
        setError(null);
        setExecuting(true);

        const cmd = buildCommand(selectedFlow, {
            sourceName,
            batchSize,
            incremental,
            dryRun,
            workers,
            layer,
        });

        setActiveCommand(cmd);

        try {
            const payload: Record<string, unknown> = {
                flow_name: selectedFlow,
                trigger_type: "manual",
                triggered_by: "dashboard:console",
                batch_size: batchSize,
                incremental,
                dry_run: dryRun,
                environment,
            };

            if (SOURCE_FLOWS.has(selectedFlow) && sourceName) {
                payload.source_name = sourceName;
            }
            if (selectedFlow === "single_layer") {
                payload.layers = [layer];
            }
            if (PARALLEL_FLOWS.has(selectedFlow)) {
                payload.max_concurrency = workers;
            }

            const result = await api.triggerFlow(payload);

            if (result.run_id) {
                setActiveRunId(result.run_id);

                // Save to history
                const item: HistoryItem = {
                    command: cmd,
                    flow: selectedFlow,
                    timestamp: new Date().toISOString(),
                };
                const newHistory = [item, ...history].slice(0, 10);
                setHistory(newHistory);
                try {
                    localStorage.setItem("console-history", JSON.stringify(newHistory));
                } catch { /* ignore */ }
            } else {
                setError("No run_id returned from API");
                setExecuting(false);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            setExecuting(false);
        }
    }, [selectedFlow, sourceName, batchSize, incremental, dryRun, workers, layer, history, environment]);

    const handleComplete = useCallback((status: string) => {
        setExecuting(false);
        console.log("Run completed:", status);
    }, []);

    const handleHistoryClick = useCallback((item: HistoryItem) => {
        setSelectedFlow(item.flow);
    }, []);

    return (
        <div>
            <div className="page-header">
                <h2>⌨️ Pipeline Console</h2>
                <p>Execute and monitor pipeline flows in real-time</p>
            </div>

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

            <div className="console-layout">
                {/* Config Panel */}
                <div className="config-panel" style={environment === "testing" ? { borderColor: "rgba(245, 158, 11, 0.4)", boxShadow: "0 0 12px rgba(245, 158, 11, 0.1)" } : undefined}>
                    {/* Environment Toggle */}
                    {testConfigured && (
                        <div className="config-section">
                            <div className="config-section-title">Environment</div>
                            <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                                <button
                                    id="env-production"
                                    style={{
                                        flex: 1,
                                        padding: "8px 12px",
                                        border: "1px solid",
                                        borderColor: environment === "production" ? "var(--accent-green)" : "var(--border)",
                                        borderRadius: 8,
                                        background: environment === "production" ? "rgba(34, 197, 94, 0.12)" : "transparent",
                                        color: environment === "production" ? "var(--accent-green)" : "var(--text-muted)",
                                        cursor: executing ? "not-allowed" : "pointer",
                                        fontSize: 13,
                                        fontWeight: 600,
                                        transition: "all 0.2s ease",
                                    }}
                                    onClick={() => !executing && setEnvironment("production")}
                                    disabled={executing}
                                >
                                    🟢 Production
                                </button>
                                <button
                                    id="env-testing"
                                    style={{
                                        flex: 1,
                                        padding: "8px 12px",
                                        border: "1px solid",
                                        borderColor: environment === "testing" ? "rgb(245, 158, 11)" : "var(--border)",
                                        borderRadius: 8,
                                        background: environment === "testing" ? "rgba(245, 158, 11, 0.12)" : "transparent",
                                        color: environment === "testing" ? "rgb(245, 158, 11)" : "var(--text-muted)",
                                        cursor: executing ? "not-allowed" : "pointer",
                                        fontSize: 13,
                                        fontWeight: 600,
                                        transition: "all 0.2s ease",
                                    }}
                                    onClick={() => !executing && setEnvironment("testing")}
                                    disabled={executing}
                                >
                                    🧪 Testing
                                </button>
                            </div>
                            {environment === "testing" && (
                                <div
                                    style={{
                                        marginTop: 8,
                                        padding: "6px 10px",
                                        borderRadius: 6,
                                        background: "rgba(245, 158, 11, 0.08)",
                                        border: "1px solid rgba(245, 158, 11, 0.2)",
                                        fontSize: 11,
                                        color: "rgb(245, 158, 11)",
                                    }}
                                >
                                    ⚠️ Test mode — writes to test DB, reads from testing/ subfolder
                                </div>
                            )}
                        </div>
                    )}

                    <div className="config-section">
                        <div className="config-section-title">Flow Configuration</div>

                        <div className="form-group">
                            <label className="form-label">Flow</label>
                            <select
                                id="flow-select"
                                className="form-select"
                                value={selectedFlow}
                                onChange={(e) => setSelectedFlow(e.target.value)}
                                disabled={executing}
                            >
                                {loading ? (
                                    <option>Loading...</option>
                                ) : (
                                    flows.map((f) => (
                                        <option key={f.name} value={f.name}>
                                            {f.name}
                                        </option>
                                    ))
                                )}
                            </select>
                            {currentFlowDesc && (
                                <div className="text-xs text-muted" style={{ marginTop: 4 }}>
                                    {currentFlowDesc}
                                </div>
                            )}
                        </div>

                        {SOURCE_FLOWS.has(selectedFlow) && (
                            <div className="form-group" style={{ marginTop: 12 }}>
                                <label className="form-label">Source Name</label>
                                <select
                                    id="source-name"
                                    className="form-select"
                                    value={sourceName}
                                    onChange={(e) => setSourceName(e.target.value)}
                                    disabled={executing}
                                >
                                    <option value="">— select source —</option>
                                    {sourceNames
                                        .filter((s) => s.status !== "archived")
                                        .map((s) => (
                                            <option key={s.source_name} value={s.source_name}>
                                                {s.source_name} ({s.category})
                                            </option>
                                        ))}
                                </select>
                            </div>
                        )}

                        {selectedFlow === "single_layer" && (
                            <div className="form-group" style={{ marginTop: 12 }}>
                                <label className="form-label">Layer</label>
                                <select
                                    id="layer-select"
                                    className="form-select"
                                    value={layer}
                                    onChange={(e) => setLayer(e.target.value)}
                                    disabled={executing}
                                >
                                    {LAYERS.map((l) => (
                                        <option key={l.value} value={l.value}>
                                            {l.label}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}
                    </div>

                    <div className="config-section">
                        <div className="config-section-title">Parameters</div>

                        <div className="form-group">
                            <label className="form-label">Batch Size</label>
                            <input
                                id="batch-size"
                                type="number"
                                className="form-input"
                                min={1}
                                max={10000}
                                value={batchSize}
                                onChange={(e) => setBatchSize(Number(e.target.value))}
                                disabled={executing}
                            />
                        </div>

                        {PARALLEL_FLOWS.has(selectedFlow) && (
                            <div className="form-group" style={{ marginTop: 12 }}>
                                <label className="form-label">
                                    Parallel Workers: {workers}
                                </label>
                                <input
                                    id="workers-slider"
                                    type="range"
                                    className="form-range"
                                    min={1}
                                    max={5}
                                    value={workers}
                                    onChange={(e) => setWorkers(Number(e.target.value))}
                                    disabled={executing}
                                />
                                <div className="form-range-labels">
                                    <span>1</span>
                                    <span>2</span>
                                    <span>3</span>
                                    <span>4</span>
                                    <span>5</span>
                                </div>
                            </div>
                        )}

                        <div className="form-toggle-group" style={{ marginTop: 8 }}>
                            <span className="form-toggle-label">Incremental</span>
                            <button
                                className={`toggle ${incremental ? "active" : ""}`}
                                onClick={() => setIncremental(!incremental)}
                                disabled={executing}
                            />
                        </div>

                        <div className="form-toggle-group">
                            <span className="form-toggle-label">Dry Run</span>
                            <button
                                className={`toggle ${dryRun ? "active" : ""}`}
                                onClick={() => setDryRun(!dryRun)}
                                disabled={executing}
                            />
                        </div>
                    </div>

                    <button
                        id="execute-btn"
                        className={`btn-execute ${executing ? "running" : ""}`}
                        onClick={handleExecute}
                        disabled={executing || loading}
                        style={environment === "testing" && !executing ? {
                            background: "linear-gradient(135deg, rgb(245, 158, 11), rgb(217, 119, 6))",
                        } : undefined}
                    >
                        {executing ? (
                            <>⏳ Running...</>
                        ) : environment === "testing" ? (
                            <>🧪 Execute (Test)</>
                        ) : (
                            <>▶ Execute</>
                        )}
                    </button>

                    {history.length > 0 && (
                        <div className="config-section">
                            <div className="config-section-title">Recent Commands</div>
                            <div className="command-history">
                                {history.map((item, i) => (
                                    <div
                                        key={i}
                                        className="command-history-item"
                                        onClick={() => handleHistoryClick(item)}
                                    >
                                        <span style={{ opacity: 0.5 }}>$</span>
                                        <span>{item.command}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Terminal */}
                <Terminal
                    runId={activeRunId}
                    command={activeCommand}
                    onComplete={handleComplete}
                />
            </div>
        </div>
    );
}

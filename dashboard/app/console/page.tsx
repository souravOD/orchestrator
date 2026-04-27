"use client";

import { useEffect, useState, useCallback } from "react";
import { api, FlowDefinition, SourceNameItem, TestStatus, OrchestrationRun } from "@/lib/api";
import Terminal from "@/components/Terminal";

// Flows that support source_name parameter
const SOURCE_FLOWS = new Set([
    "full_ingestion",
    "single_layer",
    "multi_source_ingestion",
]);

// Flows that support parallel workers (intra-tool LLM parallelism)
const PARALLEL_FLOWS = new Set([
    "full_ingestion",
    "multi_source_ingestion",
    "single_layer",
]);

// Available layers for single_layer flow
const LAYERS = [
    { value: "prebronze_to_bronze", label: "P2B (Pre-Bronze → Bronze)" },
    { value: "bronze_to_silver", label: "B2S (Bronze → Silver)" },
    { value: "silver_to_gold", label: "S2G (Silver → Gold)" },
    { value: "gold_to_neo4j", label: "G2N (Gold → Neo4j)" },
    { value: "usda_nutrition_fetch", label: "USDA Nutrition Fetch" },
];

// Neo4j sub-layers
const NEO4J_LAYERS = [
    { value: "all", label: "All Layers" },
    { value: "recipes", label: "Recipes" },
    { value: "ingredients", label: "Ingredients" },
    { value: "products", label: "Products" },
    { value: "customers", label: "Customers" },
];

// Which pipelines use batch_size, incremental, dry_run
const BATCH_SIZE_LAYERS = new Set(["bronze_to_silver", "silver_to_gold"]);
const INCREMENTAL_LAYERS = new Set(["bronze_to_silver", "silver_to_gold"]);
const DRY_RUN_LAYERS = new Set(["bronze_to_silver", "silver_to_gold"]);

interface HistoryItem {
    command: string;
    flow: string;
    timestamp: string;
    run_id?: string;
    environment?: string;
    status?: string;
    duration_seconds?: number;
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
        skipTranslation: boolean;
        usdaLimit: number | null;
        usdaMaxWorkers: number;
        enableSchemaDiff: boolean;
        enableDqGeneration: boolean;
        tables: string;
        reprocessAll: boolean;
        neo4jLayer: string;
    }
): string {
    const parts = ["orchestrator run", `--flow ${flow}`];
    if (SOURCE_FLOWS.has(flow) && opts.sourceName) {
        parts.push(`--source ${opts.sourceName}`);
    }
    if (flow === "single_layer" && opts.layer) {
        parts.push(`--layer ${opts.layer}`);
    }

    // Only show generic opts for layers that support them
    const isGenericLayer = flow !== "single_layer" || BATCH_SIZE_LAYERS.has(opts.layer);
    if (isGenericLayer) {
        parts.push(`--batch-size ${opts.batchSize}`);
    }
    if (PARALLEL_FLOWS.has(flow)) {
        parts.push(`--workers ${opts.workers}`);
    }
    if (isGenericLayer && !opts.incremental) parts.push("--full");
    if (isGenericLayer && opts.dryRun) parts.push("--dry-run");

    // Pipeline-specific
    if (opts.skipTranslation) parts.push("--skip-translation");
    if (opts.usdaLimit) parts.push(`--usda-limit ${opts.usdaLimit}`);
    if (opts.tables) parts.push(`--tables ${opts.tables}`);
    if (opts.reprocessAll) parts.push("--reprocess-all");

    return parts.join(" ");
}

export default function ConsolePage() {
    const [flows, setFlows] = useState<FlowDefinition[]>([]);
    const [loading, setLoading] = useState(true);

    // Form state — generic
    const [selectedFlow, setSelectedFlow] = useState("full_ingestion");
    const [sourceName, setSourceName] = useState("");
    const [batchSize, setBatchSize] = useState(100);
    const [incremental, setIncremental] = useState(true);
    const [dryRun, setDryRun] = useState(false);
    const [workers, setWorkers] = useState(5);
    const [layer, setLayer] = useState("prebronze_to_bronze");

    // Form state — pipeline-specific
    const [skipTranslation, setSkipTranslation] = useState(false);
    const [usdaLimit, setUsdaLimit] = useState<number | null>(null);
    const [usdaMaxWorkers, setUsdaMaxWorkers] = useState(5);
    const [enableSchemaDiff, setEnableSchemaDiff] = useState(false);
    const [enableDqGeneration, setEnableDqGeneration] = useState(false);
    const [tables, setTables] = useState("");
    const [reprocessAll, setReprocessAll] = useState(false);
    const [neo4jLayer, setNeo4jLayer] = useState("all");
    const [showAdvanced, setShowAdvanced] = useState(false);

    // Source names from API
    const [sourceNames, setSourceNames] = useState<SourceNameItem[]>([]);

    // ── Multi-Source Batch State ──
    interface SourceConfig {
        source_name: string;
        batch_size: number;
        incremental: boolean;
        dry_run: boolean;
        skip_translation: boolean;
        llm_parallel_workers: number;
        usda_limit: number | null;
        usda_max_workers: number;
        enable_schema_diff: boolean;
        enable_dq_generation: boolean;
        tables: string;
        reprocess_all: boolean;
        _expanded: boolean;
    }
    const [multiSources, setMultiSources] = useState<SourceConfig[]>([]);
    const [concurrency, setConcurrency] = useState(3);
    const isMultiSource = selectedFlow === "multi_source_ingestion";

    // Batch execution state (multi-source produces multiple run IDs)
    const [batchRuns, setBatchRuns] = useState<{ source_name: string; run_id: string }[]>([]);
    const [activeBatchTab, setActiveBatchTab] = useState<string>("overview");

    // Environment toggle
    const [environment, setEnvironment] = useState<"production" | "testing">("production");
    const [testConfigured, setTestConfigured] = useState(false);

    // Execution state
    const [executing, setExecuting] = useState(false);
    const [activeRunId, setActiveRunId] = useState<string | null>(null);
    const [activeCommand, setActiveCommand] = useState("$ waiting...");
    const [error, setError] = useState<string | null>(null);
    const [history, setHistory] = useState<HistoryItem[]>([]);
    const [historicalMode, setHistoricalMode] = useState(false);

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

    // Enrich history with DB data on mount
    useEffect(() => {
        async function enrichHistory() {
            if (history.length === 0) return;
            try {
                const res = await api.getRecentRuns(10);
                if (res.runs && res.runs.length > 0) {
                    setHistory((prev) =>
                        prev.map((item) => {
                            if (!item.run_id) return item;
                            const match = res.runs.find((r: OrchestrationRun) => r.id === item.run_id);
                            if (match) {
                                return {
                                    ...item,
                                    status: match.status,
                                    duration_seconds: match.duration_seconds ?? undefined,
                                };
                            }
                            return item;
                        })
                    );
                }
            } catch { /* best-effort */ }
        }
        enrichHistory();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Re-fetch source names when environment toggles
    useEffect(() => {
        let isCurrent = true;
        // Clear immediately to prevent stale-env execution during fetch
        setSourceName("");
        setSourceNames([]);
        async function loadSources() {
            try {
                const sourceData = await api.listSourceNames(environment);
                if (!isCurrent) return;
                setSourceNames(sourceData.sources);
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

    // ── Multi-Source Helpers ──────────────────────────
    const addSource = useCallback((name: string) => {
        setMultiSources((prev) => [
            ...prev,
            {
                source_name: name,
                batch_size: 100,
                incremental: true,
                dry_run: false,
                skip_translation: false,
                llm_parallel_workers: 3,
                usda_limit: null,
                usda_max_workers: 5,
                enable_schema_diff: false,
                enable_dq_generation: false,
                tables: "",
                reprocess_all: false,
                _expanded: true,
            },
        ]);
    }, []);

    const removeSource = useCallback((idx: number) => {
        setMultiSources((prev) => prev.filter((_, i) => i !== idx));
    }, []);

    const updateSourceConfig = useCallback(
        (idx: number, patch: Partial<SourceConfig>) => {
            setMultiSources((prev) =>
                prev.map((s, i) => (i === idx ? { ...s, ...patch } : s))
            );
        },
        []
    );

    const handleExecute = useCallback(async () => {
        setError(null);
        setExecuting(true);
        setHistoricalMode(false);

        // ── Multi-Source Batch Trigger ──
        if (isMultiSource && multiSources.length > 0) {
            const cmd = `orchestrator batch --flow multi_source_ingestion --sources ${multiSources.map((s) => s.source_name).join(",")} --concurrency ${concurrency}`;
            setActiveCommand(cmd);

            try {
                const payload = {
                    flow_name: "full_ingestion",
                    sources: multiSources.map((s) => ({
                        source_name: s.source_name,
                        batch_size: s.batch_size,
                        incremental: s.incremental,
                        dry_run: s.dry_run,
                        skip_translation: s.skip_translation || undefined,
                        llm_parallel_workers: s.llm_parallel_workers,
                        usda_limit: s.usda_limit || undefined,
                        usda_max_workers: s.usda_max_workers !== 5 ? s.usda_max_workers : undefined,
                        enable_schema_diff: s.enable_schema_diff || undefined,
                        enable_dq_generation: s.enable_dq_generation || undefined,
                        tables: s.tables || undefined,
                        reprocess_all: s.reprocess_all || undefined,
                    })),
                    batch_size: 100,
                    incremental: true,
                    dry_run: false,
                    max_concurrency: concurrency,
                    environment,
                };

                const result = await api.triggerBatch(payload);

                if (result.sources && result.sources.length > 0) {
                    setBatchRuns(result.sources.map((s) => ({
                        source_name: s.source_name,
                        run_id: s.run_id,
                    })));
                    // Set first source as active terminal
                    setActiveRunId(result.sources[0].run_id);
                    setActiveBatchTab(result.sources[0].source_name);

                    const item: HistoryItem = {
                        command: cmd,
                        flow: "multi_source_ingestion",
                        timestamp: new Date().toISOString(),
                        run_id: result.batch_id,
                        environment,
                    };
                    const newHistory = [item, ...history].slice(0, 10);
                    setHistory(newHistory);
                    try {
                        localStorage.setItem("console-history", JSON.stringify(newHistory));
                    } catch { /* ignore */ }
                } else {
                    setError("No sources returned from batch API");
                    setExecuting(false);
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
                setExecuting(false);
            }
            return;
        }

        // ── Single-Source Trigger (existing logic) ──
        const cmd = buildCommand(selectedFlow, {
            sourceName, batchSize, incremental, dryRun, workers, layer,
            skipTranslation, usdaLimit, usdaMaxWorkers, enableSchemaDiff,
            enableDqGeneration, tables, reprocessAll, neo4jLayer,
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
            // Intra-tool LLM parallelism — always send for flows using LLM tools
            payload.llm_parallel_workers = workers;

            // Pipeline-specific opts
            if (skipTranslation) payload.skip_translation = true;
            if (usdaLimit) payload.usda_limit = usdaLimit;
            if (usdaMaxWorkers !== 5) payload.usda_max_workers = usdaMaxWorkers;
            if (enableSchemaDiff) payload.enable_schema_diff = true;
            if (enableDqGeneration) payload.enable_dq_generation = true;
            if (tables) payload.tables = tables;
            if (reprocessAll) payload.reprocess_all = true;

            const result = await api.triggerFlow(payload);

            if (result.run_id) {
                setActiveRunId(result.run_id);
                setBatchRuns([]);

                // Save to history with run_id
                const item: HistoryItem = {
                    command: cmd,
                    flow: selectedFlow,
                    timestamp: new Date().toISOString(),
                    run_id: result.run_id,
                    environment,
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
    }, [selectedFlow, sourceName, batchSize, incremental, dryRun, workers, layer,
        history, environment, skipTranslation, usdaLimit, usdaMaxWorkers,
        enableSchemaDiff, enableDqGeneration, tables, reprocessAll, neo4jLayer,
        isMultiSource, multiSources, concurrency]);

    const handleComplete = useCallback((status: string) => {
        setExecuting(false);
        // Update history with completed status
        setHistory((prev) => {
            const updated = prev.map((item, i) =>
                i === 0 ? { ...item, status } : item
            );
            try {
                localStorage.setItem("console-history", JSON.stringify(updated));
            } catch { /* ignore */ }
            return updated;
        });
    }, []);

    const handleHistoryClick = useCallback((item: HistoryItem) => {
        if (item.run_id) {
            // Load logs for this historical run
            setActiveRunId(item.run_id);
            setActiveCommand(item.command);
            setHistoricalMode(true);
            if (item.environment) {
                setEnvironment(item.environment as "production" | "testing");
            }
        } else {
            // Fallback: just set the flow
            setSelectedFlow(item.flow);
        }
    }, []);

    // ── Render helpers for toggle/input ──────────────────

    // Determine which pipeline-specific options to show (single-source mode)
    const activeLayer = selectedFlow === "single_layer" ? layer : null;
    const showP2BOptions = selectedFlow === "full_ingestion" || activeLayer === "prebronze_to_bronze";
    const showUSDAOptions = selectedFlow === "full_ingestion" || activeLayer === "usda_nutrition_fetch";
    const showB2SOptions = selectedFlow === "full_ingestion" || selectedFlow === "bronze_to_gold" || activeLayer === "bronze_to_silver";
    const showS2GOptions = selectedFlow === "full_ingestion" || selectedFlow === "bronze_to_gold" || activeLayer === "silver_to_gold";
    const showG2NOptions = activeLayer === "gold_to_neo4j";
    const showGenericParams = selectedFlow !== "single_layer" || BATCH_SIZE_LAYERS.has(layer);
    const hasAdvancedOptions = showP2BOptions || showUSDAOptions || showB2SOptions || showS2GOptions || showG2NOptions;

    const renderToggle = (label: string, value: boolean, onChange: (v: boolean) => void, id: string) => (
        <div className="form-toggle-group">
            <span className="form-toggle-label">{label}</span>
            <button
                id={id}
                className={`toggle ${value ? "active" : ""}`}
                onClick={() => onChange(!value)}
                disabled={executing}
            />
        </div>
    );

    const statusBadge = (status?: string) => {
        if (!status) return null;
        const colors: Record<string, string> = {
            completed: "var(--accent-green)",
            failed: "var(--accent-red)",
            running: "var(--accent-blue, #3b82f6)",
            cancelled: "var(--text-muted)",
        };
        return (
            <span style={{
                display: "inline-block",
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: colors[status] || "var(--text-muted)",
                marginRight: 6,
            }} />
        );
    };

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

                        {/* Single-source selector (non-multi flows) */}
                        {SOURCE_FLOWS.has(selectedFlow) && !isMultiSource && (
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

                        {/* ── Multi-Source Panel ── */}
                        {isMultiSource && (
                            <div style={{ marginTop: 12 }}>
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                                    <label className="form-label" style={{ margin: 0 }}>
                                        Sources ({multiSources.length})
                                    </label>
                                    <select
                                        id="add-source-select"
                                        className="form-select"
                                        style={{ width: "auto", fontSize: 11, padding: "4px 8px" }}
                                        value=""
                                        onChange={(e) => {
                                            if (e.target.value) addSource(e.target.value);
                                        }}
                                        disabled={executing}
                                    >
                                        <option value="">+ Add Source</option>
                                        {sourceNames
                                            .filter((s) => s.status !== "archived" && !multiSources.some((ms) => ms.source_name === s.source_name))
                                            .map((s) => (
                                                <option key={s.source_name} value={s.source_name}>
                                                    {s.source_name} ({s.category})
                                                </option>
                                            ))}
                                    </select>
                                </div>

                                {multiSources.length === 0 && (
                                    <div style={{
                                        padding: "16px 12px",
                                        textAlign: "center",
                                        color: "var(--text-muted)",
                                        fontSize: 12,
                                        border: "1px dashed var(--border)",
                                        borderRadius: 8,
                                    }}>
                                        Add sources above to configure the batch
                                    </div>
                                )}

                                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                    {multiSources.map((src, idx) => (
                                        <div key={src.source_name} style={{
                                            border: "1px solid var(--border)",
                                            borderRadius: 8,
                                            background: "rgba(255,255,255,0.02)",
                                            overflow: "hidden",
                                        }}>
                                            {/* Source Card Header */}
                                            <div style={{
                                                display: "flex",
                                                alignItems: "center",
                                                justifyContent: "space-between",
                                                padding: "8px 10px",
                                                cursor: "pointer",
                                                background: src._expanded ? "rgba(59,130,246,0.06)" : "transparent",
                                            }}
                                                onClick={() => updateSourceConfig(idx, { _expanded: !src._expanded })}
                                            >
                                                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{idx + 1}.</span>
                                                    <span style={{ fontWeight: 600, fontSize: 12 }}>{src.source_name}</span>
                                                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                                                        B:{src.batch_size} W:{src.llm_parallel_workers}
                                                        {src.incremental ? " Inc" : " Full"}
                                                        {src.dry_run ? " 🧪" : ""}
                                                    </span>
                                                </div>
                                                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                                                    <span style={{ fontSize: 10 }}>{src._expanded ? "▾" : "▸"}</span>
                                                    <button
                                                        onClick={(e) => { e.stopPropagation(); removeSource(idx); }}
                                                        disabled={executing}
                                                        style={{
                                                            background: "none", border: "none", cursor: "pointer",
                                                            color: "var(--accent-red, #ef4444)", fontSize: 12, padding: "0 4px",
                                                        }}
                                                        title="Remove source"
                                                    >✕</button>
                                                </div>
                                            </div>

                                            {/* Source Card Body (expanded) */}
                                            {src._expanded && (
                                                <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 8 }}>
                                                    {/* Core params */}
                                                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                                                        <div className="form-group" style={{ margin: 0 }}>
                                                            <label className="form-label" style={{ fontSize: 10 }}>Batch Size</label>
                                                            <input type="number" className="form-input" style={{ fontSize: 11 }} min={1} max={10000}
                                                                value={src.batch_size} onChange={(e) => updateSourceConfig(idx, { batch_size: Number(e.target.value) })} disabled={executing} />
                                                        </div>
                                                        <div className="form-group" style={{ margin: 0 }}>
                                                            <label className="form-label" style={{ fontSize: 10 }}>Workers: {src.llm_parallel_workers}</label>
                                                            <input type="range" className="form-range" min={1} max={5}
                                                                value={src.llm_parallel_workers} onChange={(e) => updateSourceConfig(idx, { llm_parallel_workers: Number(e.target.value) })} disabled={executing} />
                                                        </div>
                                                    </div>
                                                    {/* Toggles */}
                                                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
                                                        {renderToggle("Incremental", src.incremental, (v) => updateSourceConfig(idx, { incremental: v }), `ms-inc-${idx}`)}
                                                        {renderToggle("Dry Run", src.dry_run, (v) => updateSourceConfig(idx, { dry_run: v }), `ms-dry-${idx}`)}
                                                    </div>
                                                    {/* Pipeline-specific */}
                                                    <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase" as const, letterSpacing: "0.05em" }}>
                                                        Pipeline Options
                                                    </div>
                                                    {renderToggle("Skip Translation", src.skip_translation, (v) => updateSourceConfig(idx, { skip_translation: v }), `ms-skip-t-${idx}`)}
                                                    {renderToggle("Schema Diff", src.enable_schema_diff, (v) => updateSourceConfig(idx, { enable_schema_diff: v }), `ms-sd-${idx}`)}
                                                    {renderToggle("DQ Generation", src.enable_dq_generation, (v) => updateSourceConfig(idx, { enable_dq_generation: v }), `ms-dq-${idx}`)}
                                                    {renderToggle("Reprocess All", src.reprocess_all, (v) => updateSourceConfig(idx, { reprocess_all: v }), `ms-rp-${idx}`)}
                                                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                                                        <div className="form-group" style={{ margin: 0 }}>
                                                            <label className="form-label" style={{ fontSize: 10 }}>USDA Limit</label>
                                                            <input type="number" className="form-input" style={{ fontSize: 11 }} min={0}
                                                                value={src.usda_limit || 0} onChange={(e) => updateSourceConfig(idx, { usda_limit: Number(e.target.value) || null })} disabled={executing} />
                                                        </div>
                                                        <div className="form-group" style={{ margin: 0 }}>
                                                            <label className="form-label" style={{ fontSize: 10 }}>USDA Workers</label>
                                                            <input type="number" className="form-input" style={{ fontSize: 11 }} min={1} max={10}
                                                                value={src.usda_max_workers} onChange={(e) => updateSourceConfig(idx, { usda_max_workers: Number(e.target.value) })} disabled={executing} />
                                                        </div>
                                                    </div>
                                                    <div className="form-group" style={{ margin: 0 }}>
                                                        <label className="form-label" style={{ fontSize: 10 }}>Tables Filter</label>
                                                        <input type="text" className="form-input" style={{ fontSize: 11 }} placeholder="e.g. recipes,ingredients"
                                                            value={src.tables} onChange={(e) => updateSourceConfig(idx, { tables: e.target.value })} disabled={executing} />
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>

                                {/* Concurrency slider */}
                                {multiSources.length > 1 && (
                                    <div className="form-group" style={{ marginTop: 10 }}>
                                        <label className="form-label" style={{ fontSize: 11 }}>
                                            Max Concurrency: {concurrency}
                                            <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>(simultaneous sources)</span>
                                        </label>
                                        <input type="range" className="form-range" min={1} max={5}
                                            value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} disabled={executing} />
                                    </div>
                                )}
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

                    {/* ── Generic Parameters (hidden in multi-source mode — each card has its own) ── */}
                    {!isMultiSource && (
                    <div className="config-section">
                        <div className="config-section-title">Parameters</div>

                        {showGenericParams && (
                            <div className="form-group">
                                <label className="form-label">
                                    Batch Size
                                    <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>
                                        (records per table per run)
                                    </span>
                                </label>
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
                        )}

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

                        {(showGenericParams && INCREMENTAL_LAYERS.has(activeLayer || "")) || selectedFlow !== "single_layer" ? (
                            <>
                                {renderToggle("Incremental", incremental, setIncremental, "toggle-incremental")}
                                {renderToggle("Dry Run", dryRun, setDryRun, "toggle-dry-run")}
                            </>
                        ) : null}
                    </div>
                    )}

                    {/* ── Pipeline-Specific Options (hidden in multi-source mode) ── */}
                    {!isMultiSource && hasAdvancedOptions && (
                        <div className="config-section">
                            <button
                                onClick={() => setShowAdvanced(!showAdvanced)}
                                style={{
                                    width: "100%",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "space-between",
                                    background: "none",
                                    border: "none",
                                    cursor: "pointer",
                                    color: "var(--text-primary)",
                                    padding: 0,
                                    fontSize: 12,
                                    fontWeight: 600,
                                    letterSpacing: "0.04em",
                                    textTransform: "uppercase" as const,
                                }}
                            >
                                <span>{showAdvanced ? "▾" : "▸"} Pipeline Options</span>
                                <span style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 400, textTransform: "none" as const }}>
                                    {[showP2BOptions && "P2B", showUSDAOptions && "USDA",
                                      showB2SOptions && "B2S", showS2GOptions && "S2G",
                                      showG2NOptions && "G2N"].filter(Boolean).join(" · ")}
                                </span>
                            </button>

                            {showAdvanced && (
                                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 12 }}>
                                    {/* P2B Options */}
                                    {showP2BOptions && (
                                        <div style={{ padding: "8px 10px", background: "rgba(255,255,255,0.02)", borderRadius: 6, border: "1px solid var(--border)" }}>
                                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase" as const, letterSpacing: "0.05em" }}>P2B — PreBronze → Bronze</div>
                                            {renderToggle("Skip Translation", skipTranslation, setSkipTranslation, "toggle-skip-translation")}
                                        </div>
                                    )}

                                    {/* USDA Options */}
                                    {showUSDAOptions && (
                                        <div style={{ padding: "8px 10px", background: "rgba(255,255,255,0.02)", borderRadius: 6, border: "1px solid var(--border)" }}>
                                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase" as const, letterSpacing: "0.05em" }}>USDA — Nutrition Fetch</div>
                                            <div className="form-group" style={{ marginBottom: 6 }}>
                                                <label className="form-label" style={{ fontSize: 11 }}>Record Limit (0 = unlimited)</label>
                                                <input
                                                    type="number"
                                                    className="form-input"
                                                    min={0}
                                                    value={usdaLimit || 0}
                                                    onChange={(e) => setUsdaLimit(Number(e.target.value) || null)}
                                                    disabled={executing}
                                                    style={{ fontSize: 12 }}
                                                />
                                            </div>
                                            <div className="form-group">
                                                <label className="form-label" style={{ fontSize: 11 }}>Max Workers: {usdaMaxWorkers}</label>
                                                <input
                                                    type="range"
                                                    className="form-range"
                                                    min={1}
                                                    max={10}
                                                    value={usdaMaxWorkers}
                                                    onChange={(e) => setUsdaMaxWorkers(Number(e.target.value))}
                                                    disabled={executing}
                                                />
                                            </div>
                                        </div>
                                    )}

                                    {/* B2S Options */}
                                    {showB2SOptions && (
                                        <div style={{ padding: "8px 10px", background: "rgba(255,255,255,0.02)", borderRadius: 6, border: "1px solid var(--border)" }}>
                                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase" as const, letterSpacing: "0.05em" }}>B2S — Bronze → Silver</div>
                                            {renderToggle("Enable Schema Diff", enableSchemaDiff, setEnableSchemaDiff, "toggle-schema-diff")}
                                            {renderToggle("Enable DQ Generation", enableDqGeneration, setEnableDqGeneration, "toggle-dq-gen")}
                                        </div>
                                    )}

                                    {/* S2G Options */}
                                    {showS2GOptions && (
                                        <div style={{ padding: "8px 10px", background: "rgba(255,255,255,0.02)", borderRadius: 6, border: "1px solid var(--border)" }}>
                                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase" as const, letterSpacing: "0.05em" }}>S2G — Silver → Gold</div>
                                            {renderToggle("Reprocess All", reprocessAll, setReprocessAll, "toggle-reprocess-all")}
                                        </div>
                                    )}

                                    {/* Tables filter — shared across B2S/S2G */}
                                    {(showB2SOptions || showS2GOptions) && (
                                        <div className="form-group">
                                            <label className="form-label" style={{ fontSize: 11 }}>
                                                Tables Filter
                                                <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>(comma-separated)</span>
                                            </label>
                                            <input
                                                type="text"
                                                className="form-input"
                                                placeholder="e.g. recipes,ingredients"
                                                value={tables}
                                                onChange={(e) => setTables(e.target.value)}
                                                disabled={executing}
                                                style={{ fontSize: 12 }}
                                            />
                                        </div>
                                    )}

                                    {/* G2N Options */}
                                    {showG2NOptions && (
                                        <div style={{ padding: "8px 10px", background: "rgba(255,255,255,0.02)", borderRadius: 6, border: "1px solid var(--border)" }}>
                                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase" as const, letterSpacing: "0.05em" }}>G2N — Gold → Neo4j</div>
                                            <div className="form-group">
                                                <label className="form-label" style={{ fontSize: 11 }}>Sync Layer</label>
                                                <select
                                                    className="form-select"
                                                    value={neo4jLayer}
                                                    onChange={(e) => setNeo4jLayer(e.target.value)}
                                                    disabled={executing}
                                                    style={{ fontSize: 12 }}
                                                >
                                                    {NEO4J_LAYERS.map((l) => (
                                                        <option key={l.value} value={l.value}>{l.label}</option>
                                                    ))}
                                                </select>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    <button
                        id="execute-btn"
                        className={`btn-execute ${executing ? "running" : ""}`}
                        onClick={handleExecute}
                        disabled={executing || loading || (isMultiSource && multiSources.length === 0)}
                        style={environment === "testing" && !executing ? {
                            background: "linear-gradient(135deg, rgb(245, 158, 11), rgb(217, 119, 6))",
                        } : undefined}
                    >
                        {executing ? (
                            <>⏳ Running...</>
                        ) : isMultiSource ? (
                            environment === "testing" ? (
                                <>🧪 Execute Batch ({multiSources.length} sources)</>
                            ) : (
                                <>▶ Execute Batch ({multiSources.length} sources)</>
                            )
                        ) : environment === "testing" ? (
                            <>🧪 Execute (Test)</>
                        ) : (
                            <>▶ Execute</>
                        )}
                    </button>

                    {/* ── Recent Commands History ── */}
                    {history.length > 0 && (
                        <div className="config-section">
                            <div className="config-section-title">Recent Commands</div>
                            <div className="command-history">
                                {history.map((item, i) => (
                                    <div
                                        key={i}
                                        className="command-history-item"
                                        onClick={() => handleHistoryClick(item)}
                                        style={{ cursor: "pointer" }}
                                    >
                                        <div style={{ display: "flex", alignItems: "center", gap: 6, overflow: "hidden" }}>
                                            {statusBadge(item.status)}
                                            <span style={{ opacity: 0.5 }}>$</span>
                                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                                {item.command}
                                            </span>
                                        </div>
                                        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                                            <span>{new Date(item.timestamp).toLocaleTimeString()}</span>
                                            {item.duration_seconds != null && (
                                                <span>· {item.duration_seconds.toFixed(1)}s</span>
                                            )}
                                            {item.environment === "testing" && (
                                                <span style={{ color: "rgb(245, 158, 11)" }}>🧪</span>
                                            )}
                                            {item.run_id && (
                                                <span style={{ color: "var(--accent-blue, #3b82f6)" }}>📂</span>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Terminal — with batch source tabs for multi-source mode */}
                <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, minHeight: 0, overflow: "hidden" }}>
                    {batchRuns.length > 1 && (
                        <div style={{
                            display: "flex",
                            gap: 2,
                            padding: "4px 8px",
                            background: "rgba(0, 0, 0, 0.3)",
                            borderRadius: "8px 8px 0 0",
                            borderBottom: "1px solid var(--border)",
                            overflowX: "auto",
                        }}>
                            {batchRuns.map((br) => (
                                <button
                                    key={br.source_name}
                                    onClick={() => {
                                        setActiveBatchTab(br.source_name);
                                        setActiveRunId(br.run_id);
                                    }}
                                    style={{
                                        padding: "4px 10px",
                                        fontSize: 11,
                                        fontWeight: 600,
                                        border: "none",
                                        borderRadius: 6,
                                        cursor: "pointer",
                                        background: activeBatchTab === br.source_name
                                            ? "rgba(59, 130, 246, 0.15)"
                                            : "transparent",
                                        color: activeBatchTab === br.source_name
                                            ? "var(--accent-blue, #3b82f6)"
                                            : "var(--text-muted)",
                                        borderBottom: activeBatchTab === br.source_name
                                            ? "2px solid var(--accent-blue, #3b82f6)"
                                            : "2px solid transparent",
                                        transition: "all 0.15s ease",
                                        whiteSpace: "nowrap",
                                    }}
                                >
                                    {br.source_name}
                                </button>
                            ))}
                        </div>
                    )}
                    <Terminal
                        runId={activeRunId}
                        command={activeCommand}
                        environment={environment}
                        onComplete={handleComplete}
                        historicalMode={historicalMode}
                    />
                </div>
            </div>
        </div>
    );
}

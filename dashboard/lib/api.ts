/**
 * API Client
 * ===========
 * Typed fetch wrappers for the FastAPI orchestrator backend.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
    try {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: { "Content-Type": "application/json", ...options?.headers },
        });
        if (!res.ok) {
            throw new Error(`API error ${res.status}: ${await res.text()}`);
        }
        return res.json();
    } catch (err) {
        if (err instanceof TypeError && (err as TypeError).message === "Failed to fetch") {
            throw new Error(
                `Cannot connect to API at ${API_BASE}. Start the backend with: python -m orchestrator.cli serve`
            );
        }
        throw err;
    }
}

// ── Types ──────────────────────────────────────────

export interface DashboardStats {
    total_runs: number;
    completed: number;
    failed: number;
    running: number;
    success_rate: number;
    avg_duration_seconds: number;
    total_records_written: number;
    active_alerts: number;
}

export interface OrchestrationRun {
    id: string;
    flow_name: string;
    flow_type: string;
    status: string;
    trigger_type: string;
    triggered_by: string | null;
    source_name: string | null;
    vendor_id: string | null;
    layers: string[];
    current_layer: string | null;
    total_records_processed: number;
    total_records_written: number;
    total_dq_issues: number;
    total_errors: number;
    started_at: string;
    completed_at: string | null;
    duration_seconds: number | null;
    config: Record<string, unknown>;
    metadata: Record<string, unknown>;
}

export interface PipelineRun {
    id: string;
    pipeline_name: string;
    orchestration_run_id: string;
    status: string;
    records_written: number;
    duration_seconds: number | null;
    error_message: string | null;
    started_at: string;
    completed_at: string | null;
    steps: StepLog[];
}

export interface StepLog {
    id: string;
    pipeline_run_id: string;
    step_name: string;
    step_order: number;
    status: string;
    records_in: number;
    records_out: number;
    records_error: number;
    duration_ms: number | null;
    error_message: string | null;
}

export interface PipelineDefinition {
    id: string;
    pipeline_name: string;
    layer_from: string;
    layer_to: string;
    description: string | null;
    is_active: boolean;
    timeout_seconds: number | null;
}

export interface DeadLetter {
    id: string;
    payload: Record<string, unknown>;
    error_message: string | null;
    error_details: Record<string, unknown> | null;
    retry_count: number;
    max_retries: number;
    status: string;
    next_retry_at: string | null;
    last_retry_at: string | null;
    resolved_at: string | null;
    created_at: string;
}

export interface PipelineHealth {
    pipeline: string;
    active: boolean;
    last_run_status: string | null;
    last_run_at: string | null;
}

export interface Alert {
    id: string;
    alert_type: string;
    severity: string;
    title: string;
    message: string | null;
    pipeline_name: string | null;
    run_id: string | null;
    created_at: string;
}

// ── API Functions ──────────────────────────────────

export const api = {
    getStats: () => fetchApi<DashboardStats>("/api/stats"),

    listRuns: (limit = 20, status?: string) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (status) params.set("status", status);
        return fetchApi<{ runs: OrchestrationRun[]; count: number }>(
            `/api/runs?${params}`
        );
    },

    getRun: (id: string) => fetchApi<OrchestrationRun>(`/api/runs/${id}`),

    getRunSteps: (id: string) =>
        fetchApi<{
            orchestration_run: OrchestrationRun;
            pipeline_runs: PipelineRun[];
        }>(`/api/runs/${id}/steps`),

    listPipelines: () =>
        fetchApi<{ pipelines: PipelineDefinition[]; count: number }>(
            "/api/pipelines"
        ),

    getPipelineHealth: (name: string) =>
        fetchApi<PipelineHealth>(`/api/pipelines/${name}/health`),

    getNeo4jSyncStatus: () =>
        fetchApi<{ latest_runs: OrchestrationRun[] }>("/api/neo4j/sync-status"),

    getNeo4jReconciliation: () =>
        fetchApi<{ latest_runs: OrchestrationRun[] }>("/api/neo4j/reconciliation"),

    listAlerts: (limit = 50, severity?: string, pipeline_name?: string) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (severity) params.set("severity", severity);
        if (pipeline_name) params.set("pipeline_name", pipeline_name);
        return fetchApi<{ alerts: Alert[]; count: number }>(
            `/api/alerts?${params}`
        );
    },

    // ── Cancel / Retry ─────────────────────────────────

    cancelRun: (id: string) =>
        fetchApi<{ cancelled: boolean; run_id: string }>(`/api/runs/${id}/cancel`, {
            method: "POST",
        }),

    retryRun: (id: string) =>
        fetchApi<{ status: string; result: unknown }>(`/api/runs/${id}/retry`, {
            method: "POST",
        }),

    // ── Pipeline Settings ──────────────────────────────

    updatePipelineSettings: (name: string, settings: Record<string, unknown>) =>
        fetchApi<{ pipeline: string; updated: Record<string, unknown> }>(
            `/api/pipelines/${name}/settings`,
            { method: "PUT", body: JSON.stringify(settings) }
        ),

    // ── Dead-Letter Queue ──────────────────────────────

    listDeadLetters: (status?: string, limit = 50) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (status) params.set("status", status);
        return fetchApi<{ dead_letters: DeadLetter[]; count: number }>(
            `/api/dead-letters?${params}`
        );
    },

    retryDeadLetter: (id: string) =>
        fetchApi<{ status: string }>(`/api/dead-letters/${id}/retry`, {
            method: "POST",
        }),

    discardDeadLetter: (id: string) =>
        fetchApi<{ status: string }>(`/api/dead-letters/${id}`, {
            method: "DELETE",
        }),
};

/**
 * API Client
 * ===========
 * Typed fetch wrappers for the FastAPI orchestrator backend.
 */

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8100";

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

export interface SourceNameItem {
    source_name: string;
    category: string;
    status: string;
}

export interface TestStatus {
    configured: boolean;
    url: string;
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

    // ── Flow Registry ──────────────────────────────────

    listFlows: () =>
        fetchApi<{ flows: FlowDefinition[]; count: number }>("/api/flows"),

    triggerFlow: (payload: Record<string, unknown>) =>
        fetchApi<{ status: string; run_id: string; flow_name?: string }>(
            "/api/trigger",
            { method: "POST", body: JSON.stringify(payload) }
        ),

    listSourceNames: (environment?: string) =>
        fetchApi<{ sources: SourceNameItem[] }>(
            `/api/source-names${environment ? `?environment=${encodeURIComponent(environment)}` : ""}`
        ),

    getTestStatus: () =>
        fetchApi<TestStatus>("/api/test/status"),

    // ── Data Sources ───────────────────────────────────

    listDataSources: (category?: string, status?: string) => {
        const params = new URLSearchParams();
        if (category) params.set("category", category);
        if (status) params.set("status", status);
        return fetchApi<{ data_sources: DataSource[]; count: number }>(
            `/api/data-sources?${params}`
        );
    },

    getDataSource: (id: string) =>
        fetchApi<{ source: DataSource; cursors: DataSourceCursor[] }>(
            `/api/data-sources/${id}`
        ),

    triggerDataSourceIngest: (id: string, config: Record<string, unknown>) =>
        fetchApi<{ status: string; run_id: string; source_name?: string }>(
            `/api/data-sources/${id}/ingest`,
            { method: "POST", body: JSON.stringify(config) }
        ),

    // ── Schedules ──────────────────────────────────────

    listSchedules: () =>
        fetchApi<{ schedules: ScheduleDefinition[]; count: number }>(
            "/api/schedules"
        ),

    triggerSchedule: (id: string) =>
        fetchApi<{ status: string; run_id: string; flow_name?: string }>(
            `/api/schedules/${id}/trigger`,
            { method: "POST" }
        ),

    updateSchedule: (id: string, fields: Record<string, unknown>) =>
        fetchApi<ScheduleDefinition>(
            `/api/schedules/${id}`,
            { method: "PATCH", body: JSON.stringify(fields) }
        ),

    // ── Event Triggers ─────────────────────────────────

    listEventTriggers: () =>
        fetchApi<{ triggers: EventTrigger[]; count: number }>(
            "/api/event-triggers"
        ),

    updateEventTrigger: (id: string, fields: Record<string, unknown>) =>
        fetchApi<EventTrigger>(
            `/api/event-triggers/${id}`,
            { method: "PATCH", body: JSON.stringify(fields) }
        ),
};

// ── New Types ──────────────────────────────────────────

export interface FlowDefinition {
    name: string;
    description: string;
}

export interface DataSource {
    id: string;
    source_name: string;
    category: string;
    storage_bucket: string;
    storage_path: string;
    file_format: string;
    file_size_bytes: number | null;
    total_records: number;
    total_ingested: number;
    target_table: string;
    description: string | null;
    status: string;
    is_active: boolean;
    tags: string[];
    parent_source_id: string | null;
    last_ingested_at: string | null;
    created_at: string;
    updated_at: string;
    latest_cursor?: DataSourceCursor | null;
}

export interface DataSourceCursor {
    id: string;
    data_source_id: string;
    cursor_type: string;
    cursor_start: number;
    cursor_end: number;
    batch_size: number;
    status: string;
    records_processed: number;
    records_written: number;
    records_skipped: number;
    records_failed: number;
    error_message: string | null;
    started_at: string | null;
    completed_at: string | null;
    created_at: string;
}

export interface ScheduleDefinition {
    id: string;
    schedule_name: string;
    cron_expression: string;
    flow_name: string;
    run_config: Record<string, unknown>;
    is_active: boolean;
    description: string | null;
    last_run_at: string | null;
    next_run_at: string | null;
    created_at: string;
    updated_at: string;
}

export interface EventTrigger {
    id: string;
    trigger_name: string;
    event_type: string;
    source_schema: string | null;
    source_table: string | null;
    flow_name: string;
    filter_config: Record<string, unknown>;
    debounce_seconds: number;
    is_active: boolean;
    description: string | null;
    created_at: string;
    updated_at: string;
}

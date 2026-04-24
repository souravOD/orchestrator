"use client";

import { useEffect, useState, useCallback } from "react";
import { api, ScheduleDefinition, EventTrigger } from "@/lib/api";

// Simple cron-to-human-readable converter for common patterns
function cronToHuman(cron: string): string {
    if (!cron) return cron;
    const parts = cron.split(" ");
    if (parts.length < 5) return cron;
    const [min, hour, dom, , dow] = parts;

    // Every N minutes
    if (min.startsWith("*/")) return `Every ${min.slice(2)} min`;

    // Specific time patterns
    const timeStr = `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`;

    if (dom === "*" && dow === "*") return `Daily ${timeStr}`;
    if (dom === "*" && dow === "0") return `Sundays ${timeStr}`;
    if (dom === "*" && dow === "1-5") return `Weekdays ${timeStr}`;
    if (dom === "1" && dow === "*") return `Monthly ${timeStr}`;

    return cron;
}

function formatTime(ts: string | null): string {
    if (!ts) return "—";
    return new Date(ts).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export default function TriggersPage() {
    const [schedules, setSchedules] = useState<ScheduleDefinition[]>([]);
    const [triggers, setTriggers] = useState<EventTrigger[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function load() {
            try {
                setLoading(true);
                const [schedData, trigData] = await Promise.all([
                    api.listSchedules(),
                    api.listEventTriggers(),
                ]);
                setSchedules(schedData.schedules);
                setTriggers(trigData.triggers);
            } catch (err) {
                console.error("Failed to load triggers:", err);
                setError(err instanceof Error ? err.message : String(err));
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    const toggleSchedule = useCallback(
        async (sched: ScheduleDefinition) => {
            try {
                const updated = await api.updateSchedule(sched.id, {
                    is_active: !sched.is_active,
                });
                setSchedules((prev) =>
                    prev.map((s) =>
                        s.id === sched.id ? { ...s, is_active: updated.is_active ?? !s.is_active } : s
                    )
                );
            } catch (err) {
                alert(`Failed to update: ${err instanceof Error ? err.message : err}`);
            }
        },
        []
    );

    const toggleTrigger = useCallback(
        async (trig: EventTrigger) => {
            try {
                const updated = await api.updateEventTrigger(trig.id, {
                    is_active: !trig.is_active,
                });
                setTriggers((prev) =>
                    prev.map((t) =>
                        t.id === trig.id ? { ...t, is_active: updated.is_active ?? !t.is_active } : t
                    )
                );
            } catch (err) {
                alert(`Failed to update: ${err instanceof Error ? err.message : err}`);
            }
        },
        []
    );

    const runScheduleNow = useCallback(
        async (sched: ScheduleDefinition) => {
            try {
                const result = await api.triggerSchedule(sched.id);
                alert(
                    `✅ ${sched.schedule_name} triggered!\nFlow: ${result.flow_name}\nRun ID: ${result.run_id?.slice(0, 8)}...\n\nGo to Console page to monitor.`
                );
            } catch (err) {
                alert(`❌ Failed: ${err instanceof Error ? err.message : err}`);
            }
        },
        []
    );

    return (
        <div>
            <div className="page-header">
                <h2>⏰ Triggers & Schedules</h2>
                <p>Manage cron schedules and event-driven triggers</p>
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

            {loading ? (
                <div className="grid-2">
                    <div className="card">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="shimmer shimmer-card" style={{ marginBottom: 8 }} />
                        ))}
                    </div>
                    <div className="card">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="shimmer shimmer-card" style={{ marginBottom: 8 }} />
                        ))}
                    </div>
                </div>
            ) : (
                <div className="triggers-layout">
                    {/* Cron Schedules */}
                    <div>
                        <div className="section-title">
                            📅 Cron Schedules
                            <span className="badge" style={{ marginLeft: 8 }}>
                                {schedules.length}
                            </span>
                        </div>

                        {schedules.length === 0 ? (
                            <div className="card">
                                <div className="empty-state">
                                    <div className="empty-state-icon">📅</div>
                                    <p>No schedules configured</p>
                                </div>
                            </div>
                        ) : (
                            schedules.map((sched) => (
                                <div key={sched.id} className="trigger-card">
                                    <div className="trigger-card-header">
                                        <div className="trigger-card-title">
                                            {sched.schedule_name}
                                        </div>
                                        <span className="cron-badge">
                                            {sched.cron_expression}
                                        </span>
                                    </div>

                                    <div className="trigger-card-meta">
                                        <span>
                                            ↳ {cronToHuman(sched.cron_expression)}
                                        </span>
                                        <span>
                                            Flow: <strong>{sched.flow_name}</strong>
                                        </span>
                                        {sched.run_config && Object.keys(sched.run_config).length > 0 && (
                                            <span>
                                                Config:{" "}
                                                {Object.entries(sched.run_config)
                                                    .map(([k, v]) => `${k}=${v}`)
                                                    .join(", ")}
                                            </span>
                                        )}
                                        <span>Last: {formatTime(sched.last_run_at)}</span>
                                        {sched.next_run_at && (
                                            <span>Next: {formatTime(sched.next_run_at)}</span>
                                        )}
                                    </div>

                                    <div className="trigger-card-actions">
                                        <div className="flex items-center gap-8">
                                            <span
                                                className={`trigger-status-dot ${sched.is_active ? "active" : "inactive"}`}
                                            />
                                            <button
                                                className={`toggle ${sched.is_active ? "active" : ""}`}
                                                onClick={() => toggleSchedule(sched)}
                                                title={sched.is_active ? "Deactivate" : "Activate"}
                                            />
                                        </div>
                                        <button
                                            className="btn btn-blue btn-sm"
                                            onClick={() => runScheduleNow(sched)}
                                        >
                                            ▶ Run Now
                                        </button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>

                    {/* Event Triggers */}
                    <div>
                        <div className="section-title">
                            ⚡ Event Triggers
                            <span className="badge" style={{ marginLeft: 8 }}>
                                {triggers.length}
                            </span>
                        </div>

                        {triggers.length === 0 ? (
                            <div className="card">
                                <div className="empty-state">
                                    <div className="empty-state-icon">⚡</div>
                                    <p>No event triggers configured</p>
                                </div>
                            </div>
                        ) : (
                            triggers.map((trig) => (
                                <div key={trig.id} className="trigger-card">
                                    <div className="trigger-card-header">
                                        <div className="trigger-card-title">
                                            {trig.trigger_name}
                                        </div>
                                        <span className="badge">{trig.event_type}</span>
                                    </div>

                                    <div className="trigger-card-meta">
                                        {trig.source_schema && trig.source_table && (
                                            <span>
                                                Source: {trig.source_schema}.{trig.source_table}
                                            </span>
                                        )}
                                        <span>
                                            Flow: <strong>{trig.flow_name}</strong>
                                        </span>
                                        <span>
                                            Debounce: {trig.debounce_seconds}s
                                        </span>
                                        {trig.description && (
                                            <span className="text-muted">{trig.description}</span>
                                        )}
                                    </div>

                                    <div className="trigger-card-actions">
                                        <div className="flex items-center gap-8">
                                            <span
                                                className={`trigger-status-dot ${trig.is_active ? "active" : "inactive"}`}
                                            />
                                            <button
                                                className={`toggle ${trig.is_active ? "active" : ""}`}
                                                onClick={() => toggleTrigger(trig)}
                                                title={trig.is_active ? "Deactivate" : "Activate"}
                                            />
                                        </div>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

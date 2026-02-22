/**
 * Supabase Realtime Client
 * =========================
 * Subscribes to orchestration table changes for live dashboard updates.
 */

import { createClient, RealtimeChannel } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

export type RealtimeCallback = (payload: Record<string, unknown>) => void;

/**
 * Subscribe to changes on an orchestration table.
 * Returns the channel for cleanup.
 */
export function subscribeToTable(
    table: string,
    callback: RealtimeCallback
): RealtimeChannel {
    const channel = supabase
        .channel(`orchestration-${table}`)
        .on(
            "postgres_changes",
            {
                event: "*",
                schema: "orchestration",
                table,
            },
            (payload) => callback(payload.new as Record<string, unknown>)
        )
        .subscribe();

    return channel;
}

/**
 * Unsubscribe from a Realtime channel.
 */
export function unsubscribe(channel: RealtimeChannel) {
    supabase.removeChannel(channel);
}

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_SECTIONS = [
    {
        label: "Dashboard",
        items: [
            { href: "/", label: "Overview", icon: "📊" },
            { href: "/runs", label: "Runs", icon: "🔄" },
            { href: "/pipelines", label: "Pipelines", icon: "🔗" },
        ],
    },
    {
        label: "Operations",
        items: [
            { href: "/console", label: "Console", icon: "⌨️" },
            { href: "/data-sources", label: "Data Sources", icon: "🗄️" },
            { href: "/triggers", label: "Triggers", icon: "⏰" },
        ],
    },
    {
        label: "Infrastructure",
        items: [
            { href: "/neo4j", label: "Neo4j Sync", icon: "🕸️" },
            { href: "/alerts", label: "Alerts", icon: "🔔" },
            { href: "/dead-letters", label: "Dead Letters", icon: "📬" },
        ],
    },
];

export default function Sidebar() {
    const pathname = usePathname();

    return (
        <aside className="sidebar">
            <div className="sidebar-logo">
                <div className="sidebar-logo-icon">⚡</div>
                <div>
                    <h1>Orchestrator</h1>
                    <span>Pipeline Monitor</span>
                </div>
            </div>

            <nav className="sidebar-nav">
                {NAV_SECTIONS.map((section) => (
                    <div key={section.label}>
                        <div className="nav-section-label">{section.label}</div>
                        {section.items.map((item) => {
                            const isActive =
                                item.href === "/"
                                    ? pathname === "/"
                                    : pathname.startsWith(item.href);
                            return (
                                <Link
                                    key={item.href}
                                    href={item.href}
                                    className={`nav-link ${isActive ? "active" : ""}`}
                                >
                                    <span className="nav-icon">{item.icon}</span>
                                    {item.label}
                                </Link>
                            );
                        })}
                    </div>
                ))}
            </nav>

            <div style={{ padding: "16px", borderTop: "1px solid var(--border-primary)" }}>
                <div className="live-indicator">
                    <span className="live-dot" />
                    Connected
                </div>
            </div>
        </aside>
    );
}

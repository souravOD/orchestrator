interface KPICardProps {
    label: string;
    value: string | number;
    sub?: string;
    color: "blue" | "green" | "red" | "amber" | "purple" | "cyan";
}

export default function KPICard({ label, value, sub, color }: KPICardProps) {
    return (
        <div className={`kpi-card ${color}`}>
            <div className="kpi-label">{label}</div>
            <div className={`kpi-value ${color}`}>{value}</div>
            {sub && <div className="kpi-sub">{sub}</div>}
        </div>
    );
}

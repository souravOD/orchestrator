export default function StatusBadge({ status }: { status: string }) {
    const label = status.replace(/_/g, " ");
    const showDot = status === "running";

    return (
        <span className={`badge ${status}`}>
            {showDot && <span className="badge-dot" />}
            {label}
        </span>
    );
}

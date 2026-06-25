import { cn } from "@/lib/utils";

interface BadgeProps {
  children: React.ReactNode;
  variant?: "default" | "success" | "warning" | "error" | "info" | "outline";
  size?: "sm" | "md";
  dot?: boolean;
  className?: string;
}

export function Badge({
  children,
  variant = "default",
  size = "sm",
  dot = false,
  className,
}: BadgeProps) {
  const variantStyle: React.CSSProperties = (() => {
    switch (variant) {
      case "success":
        return { background: "var(--success-light)", color: "var(--success-text)" };
      case "warning":
        return { background: "var(--warning-light)", color: "var(--warning-text)" };
      case "error":
        return { background: "var(--danger-light)", color: "var(--danger-text)" };
      case "info":
        return { background: "var(--info-light)", color: "var(--info-text)" };
      case "outline":
        return {
          background: "transparent",
          color: "var(--text-secondary)",
          border: "1px solid var(--border-primary)",
        };
      default:
        return { background: "var(--bg-subdued)", color: "var(--text-secondary)" };
    }
  })();

  const dotColor: string = (() => {
    switch (variant) {
      case "success": return "var(--success)";
      case "warning": return "var(--warning)";
      case "error": return "var(--danger)";
      case "info": return "var(--info)";
      default: return "var(--text-tertiary)";
    }
  })();

  const sizeClass = size === "sm"
    ? "px-2 py-[2px] text-[11px]"
    : "px-2.5 py-[3px] text-[12px]";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] font-medium leading-[16px] whitespace-nowrap",
        sizeClass,
        className
      )}
      style={variantStyle}
    >
      {dot && (
        <span
          className="h-[6px] w-[6px] rounded-full flex-shrink-0"
          style={{ background: dotColor }}
        />
      )}
      {children}
    </span>
  );
}

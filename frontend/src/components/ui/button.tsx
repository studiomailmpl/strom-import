import { cn } from "@/lib/utils";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  children: React.ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  children,
  className,
  disabled,
  style,
  ...props
}: ButtonProps) {
  const variantStyle: React.CSSProperties = (() => {
    switch (variant) {
      case "primary":
        return {
          background: "var(--accent)",
          color: "#fff",
          border: "none",
          boxShadow: "0 1px 0 rgba(0,0,0,0.08), inset 0 -1px 0 rgba(0,0,0,0.15)",
        };
      case "secondary":
        return {
          background: "var(--bg-primary)",
          color: "var(--text-primary)",
          border: "1px solid var(--border-primary)",
          boxShadow: "0 1px 0 rgba(0,0,0,0.04)",
        };
      case "ghost":
        return {
          background: "transparent",
          color: "var(--text-secondary)",
          border: "1px solid transparent",
        };
      case "danger":
        return {
          background: "var(--danger)",
          color: "#fff",
          border: "none",
          boxShadow: "0 1px 0 rgba(0,0,0,0.08), inset 0 -1px 0 rgba(0,0,0,0.15)",
        };
    }
  })();

  const sizeClass = {
    sm: "px-3 py-[5px] text-[12px]",
    md: "px-4 py-[7px] text-[13px]",
    lg: "px-5 py-[9px] text-[14px]",
  }[size];

  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-[var(--radius-md)] font-medium",
        "transition-all duration-150",
        "hover:brightness-95 active:scale-[0.98] active:brightness-90",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-1",
        "disabled:opacity-40 disabled:pointer-events-none disabled:shadow-none",
        sizeClass,
        className
      )}
      style={{ ...variantStyle, ...style }}
      disabled={disabled || loading}
      {...props}
    >
      {loading && (
        <svg
          className="animate-spin h-3.5 w-3.5"
          viewBox="0 0 24 24"
          fill="none"
        >
          <circle
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            className="opacity-20"
          />
          <path
            d="M12 2a10 10 0 0 1 10 10"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
          />
        </svg>
      )}
      {children}
    </button>
  );
}

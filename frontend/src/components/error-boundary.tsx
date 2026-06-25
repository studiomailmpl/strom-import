"use client";

import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallbackMessage?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * React error boundary that catches render errors in child components.
 * Shows a friendly error UI with retry button instead of crashing the entire app.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("[ErrorBoundary] Caught error:", error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "64px 24px",
            textAlign: "center",
          }}
        >
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: "var(--radius-lg)",
              background: "var(--warning-light)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: 16,
            }}
          >
            <AlertTriangle style={{ width: 24, height: 24, color: "var(--warning)" }} />
          </div>
          <h2
            style={{
              fontSize: 16,
              fontWeight: 600,
              color: "var(--text-primary)",
              marginBottom: 8,
            }}
          >
            Noget gik galt
          </h2>
          <p
            style={{
              fontSize: 13,
              color: "var(--text-secondary)",
              maxWidth: 400,
              marginBottom: 20,
            }}
          >
            {this.props.fallbackMessage ||
              "Der opstod en uventet fejl. Prøv at genindlæse siden."}
          </p>
          {this.state.error && (
            <pre
              style={{
                fontSize: 11,
                color: "var(--text-tertiary)",
                background: "var(--bg-secondary)",
                padding: "8px 12px",
                borderRadius: "var(--radius-md)",
                maxWidth: 500,
                overflow: "auto",
                marginBottom: 20,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {this.state.error.message}
            </pre>
          )}
          <button
            onClick={this.handleRetry}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 500,
              borderRadius: "var(--radius-md)",
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              transition: "background 0.15s ease",
            }}
          >
            <RefreshCw style={{ width: 14, height: 14 }} />
            Prøv igen
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

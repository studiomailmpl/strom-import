"use client";

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import {
  History,
  FileText,
  Upload,
  FlaskConical,
  ChevronRight,
  Package,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";
import { toast } from "sonner";
import type { ImportSummary } from "@/lib/types";

const statusConfig: Record<
  string,
  { label: string; variant: "default" | "success" | "warning" | "error" | "info" | "outline"; dot?: boolean }
> = {
  uploading: { label: "Uploader", variant: "outline" },
  uploaded: { label: "Uploadet", variant: "outline" },
  analysing: { label: "Analyserer", variant: "info", dot: true },
  review: { label: "Gennemgå", variant: "warning", dot: true },
  pushing: { label: "Sender", variant: "info", dot: true },
  completed: { label: "Færdig", variant: "success", dot: true },
  failed: { label: "Fejlet", variant: "error", dot: true },
};

export default function HistoryPage() {
  const { getToken } = useAuth();
  const [imports, setImports] = useState<ImportSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchImports = useCallback(async () => {
    try {
      const token = await getToken();
      const data = await apiFetch<ImportSummary[]>("/api/v1/imports/", {
        token: token || undefined,
      });
      setImports(data);
    } catch (error) {
      console.error("Failed to fetch imports:", error);
      toast.error("Kunne ikke hente importer");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchImports();
  }, [fetchImports]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", paddingTop: 80 }}>
        <div
          style={{
            width: 20,
            height: 20,
            borderRadius: "50%",
            border: "2px solid var(--border-primary)",
            borderTopColor: "transparent",
            animation: "spin 0.6s linear infinite",
          }}
        />
      </div>
    );
  }

  return (
    <div>
      {/* Page header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, color: "var(--text-primary)", letterSpacing: "-0.01em" }}>
            Historik
          </h1>
          <p style={{ marginTop: 4, fontSize: 13, color: "var(--text-secondary)" }}>
            {imports.length} import{imports.length !== 1 ? "er" : ""} i alt
          </p>
        </div>
        <Link href="/dashboard/import">
          <Button>
            <Upload style={{ width: 15, height: 15 }} />
            Ny import
          </Button>
        </Link>
      </div>

      {imports.length === 0 ? (
        <div
          className="card"
          style={{
            padding: "64px 24px",
            textAlign: "center",
          }}
        >
          <History style={{ width: 32, height: 32, margin: "0 auto", color: "var(--text-tertiary)" }} />
          <p style={{ marginTop: 12, fontSize: 14, fontWeight: 500, color: "var(--text-secondary)" }}>
            Ingen importer endnu
          </p>
          <p style={{ marginTop: 4, fontSize: 13, color: "var(--text-tertiary)" }}>
            Start med at uploade en PDF-faktura
          </p>
          <Link href="/dashboard/import" style={{ display: "inline-block", marginTop: 16 }}>
            <Button>
              <Upload style={{ width: 15, height: 15 }} />
              Upload faktura
            </Button>
          </Link>
        </div>
      ) : (
        <div className="card" style={{ overflow: "hidden" }}>
          {/* Table header */}
          <div
            className="history-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 120px 80px 80px 100px 28px",
              alignItems: "center",
              padding: "10px 20px",
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase" as const,
              letterSpacing: "0.04em",
              color: "var(--text-tertiary)",
              background: "var(--bg-secondary)",
              borderBottom: "1px solid var(--border-secondary)",
            }}
          >
            <span>Import</span>
            <span className="hide-mobile">Dato</span>
            <span className="hide-mobile" style={{ textAlign: "center" }}>Produkter</span>
            <span className="hide-mobile" style={{ textAlign: "center" }}>Sendt</span>
            <span>Status</span>
            <span />
          </div>

          {/* Rows */}
          {imports.map((imp, idx) => {
            const config = statusConfig[imp.status] || statusConfig.uploaded;

            return (
              <Link
                key={imp.id}
                href={`/dashboard/history/${imp.id}`}
                className="table-row history-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 120px 80px 80px 100px 28px",
                  alignItems: "center",
                  padding: "12px 20px",
                  fontSize: 13,
                  textDecoration: "none",
                  color: "inherit",
                  borderBottom: idx < imports.length - 1 ? "1px solid var(--border-secondary)" : "none",
                  transition: "background 0.1s ease",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
                  <div
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: "var(--radius-md)",
                      background: "var(--bg-subdued)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <FileText style={{ width: 15, height: 15, color: "var(--text-tertiary)" }} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          fontWeight: 500,
                          color: "var(--text-primary)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap" as const,
                        }}
                      >
                        {imp.name || imp.file_name}
                      </span>
                      {imp.is_test && (
                        <Badge variant="warning" size="sm">
                          <FlaskConical style={{ width: 10, height: 10 }} />
                          Test
                        </Badge>
                      )}
                    </div>
                    {imp.file_count > 1 && (
                      <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                        {imp.file_count} filer
                      </span>
                    )}
                  </div>
                </div>

                <span className="hide-mobile" style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
                  {formatDate(imp.created_at)}
                </span>

                <span className="hide-mobile" style={{ textAlign: "center", color: "var(--text-secondary)", fontWeight: 500 }}>
                  {imp.total_products}
                </span>

                <span className="hide-mobile" style={{ textAlign: "center" }}>
                  {imp.products_pushed > 0 ? (
                    <span style={{ color: "var(--success)", fontWeight: 500 }}>{imp.products_pushed}</span>
                  ) : (
                    <span style={{ color: "var(--text-disabled)" }}>—</span>
                  )}
                </span>

                <Badge variant={config.variant} dot={config.dot}>
                  {config.label}
                </Badge>

                <ChevronRight
                  style={{ width: 14, height: 14, marginLeft: "auto", color: "var(--text-disabled)" }}
                />
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

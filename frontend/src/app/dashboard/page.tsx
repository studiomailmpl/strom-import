"use client";

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import {
  FileUp,
  ArrowRight,
  ChevronRight,
  TrendingUp,
  AlertCircle,
  ExternalLink,
  Tag,
  Settings,
  Package,
  BarChart3,
  AlertTriangle,
  Calendar,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import type { ImportSummary } from "@/lib/types";

const statusMap: Record<
  string,
  { label: string; variant: "default" | "success" | "warning" | "error" | "info" }
> = {
  uploading: { label: "Uploader", variant: "default" },
  uploaded: { label: "Klar", variant: "default" },
  analysing: { label: "Analyserer", variant: "info" },
  review: { label: "Review", variant: "warning" },
  pushing: { label: "Pusher…", variant: "info" },
  completed: { label: "Færdig", variant: "success" },
  failed: { label: "Fejl", variant: "error" },
};

/* ─── Helpers ─── */

function timeAgo(dateStr: string): string {
  const now = new Date();
  const d = new Date(dateStr);
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "Lige nu";
  if (diffMin < 60) return `${diffMin} min siden`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH} time${diffH > 1 ? "r" : ""} siden`;
  const diffD = Math.floor(diffH / 24);
  if (diffD === 1) return "I går";
  if (diffD < 7) return `${diffD} dage siden`;
  return d.toLocaleDateString("da-DK", { day: "numeric", month: "short" });
}

/* ─── Component ─── */

export default function DashboardPage() {
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
    } catch (err) {
      toast.error("Kunne ikke hente imports — tjek din forbindelse");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchImports();
  }, [fetchImports]);

  /* ─── Compute KPIs ─── */

  const recentImports = imports.slice(0, 6);
  const totalProducts = imports.reduce((s, i) => s + (i.total_products || 0), 0);
  const totalPushed = imports.reduce((s, i) => s + (i.products_pushed || 0), 0);
  const completedCount = imports.filter((i) => i.status === "completed").length;
  const failedCount = imports.filter((i) => i.status === "failed").length;
  const needsAction = imports.filter(
    (i) => i.status === "review" || i.status === "failed"
  );
  const todayCount = imports.filter(
    (i) => new Date(i.created_at).toDateString() === new Date().toDateString()
  ).length;

  const kpis = [
    {
      label: "Imports i alt",
      value: completedCount.toString(),
      sub: `${imports.length} total`,
      icon: Package,
      color: "var(--accent)",
    },
    {
      label: "Produkter pushed",
      value: totalPushed.toString(),
      sub: `af ${totalProducts} fundet`,
      icon: BarChart3,
      color: "var(--success)",
    },
    {
      label: "Fejlrate",
      value: imports.length > 0 ? `${((failedCount / imports.length) * 100).toFixed(0)}%` : "0%",
      sub: `${failedCount} fejlede`,
      icon: AlertTriangle,
      color: failedCount > 0 ? "var(--danger)" : "var(--text-tertiary)",
    },
    {
      label: "I dag",
      value: todayCount.toString(),
      sub: todayCount === 1 ? "import" : "imports",
      icon: Calendar,
      color: "var(--info)",
    },
  ];

  /* ─── Render ─── */

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 600, color: "var(--text-primary)", letterSpacing: "-0.01em", marginBottom: 20 }}>
        Oversigt
      </h1>

      {/* ═══ KPI Row ═══ */}
      <div className="kpi-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        {kpis.map((kpi) => (
          <div
            key={kpi.label}
            className="card"
            style={{ padding: "16px 18px" }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <p style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-tertiary)" }}>
                {kpi.label}
              </p>
              <kpi.icon style={{ width: 15, height: 15, color: kpi.color, opacity: 0.7 }} />
            </div>
            <p style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", color: "var(--text-primary)", marginTop: 8 }}>
              {kpi.value}
            </p>
            <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
              {kpi.sub}
            </p>
          </div>
        ))}
      </div>

      {/* ═══ Split: Table + Actions ═══ */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 16 }}>
        {/* ─── Import table ─── */}
        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
            <h2 style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
              Seneste imports
            </h2>
            {imports.length > 6 && (
              <Link
                href="/dashboard/history"
                style={{ fontSize: 12, color: "var(--accent)", display: "flex", alignItems: "center", gap: 4, fontWeight: 500 }}
              >
                Se alle
                <ArrowRight style={{ width: 12, height: 12 }} />
              </Link>
            )}
          </div>

          <div className="card" style={{ overflow: "hidden" }}>
            {loading ? (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "64px 0" }}>
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
            ) : recentImports.length === 0 ? (
              <div style={{ padding: "64px 24px", textAlign: "center" }}>
                <FileUp style={{ width: 32, height: 32, margin: "0 auto", color: "var(--text-tertiary)" }} />
                <p style={{ marginTop: 12, fontSize: 14, fontWeight: 500, color: "var(--text-secondary)" }}>
                  Ingen imports endnu
                </p>
                <Link
                  href="/dashboard/import"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    marginTop: 12,
                    fontSize: 13,
                    fontWeight: 500,
                    padding: "7px 14px",
                    borderRadius: "var(--radius-md)",
                    background: "var(--accent)",
                    color: "#fff",
                  }}
                >
                  <FileUp style={{ width: 14, height: 14 }} />
                  Upload faktura
                </Link>
              </div>
            ) : (
              <>
                {/* Table header */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 60px 80px 28px",
                    alignItems: "center",
                    padding: "8px 16px",
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
                  <span style={{ textAlign: "center" }}>Antal</span>
                  <span>Status</span>
                  <span />
                </div>

                {/* Table rows */}
                {recentImports.map((imp, idx) => {
                  const cfg = statusMap[imp.status] || statusMap.uploaded;
                  return (
                    <Link
                      key={imp.id}
                      href={`/dashboard/history/${imp.id}`}
                      className="table-row"
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 60px 80px 28px",
                        alignItems: "center",
                        padding: "10px 16px",
                        fontSize: 13,
                        textDecoration: "none",
                        color: "inherit",
                        borderBottom: idx < recentImports.length - 1 ? "1px solid var(--border-secondary)" : "none",
                      }}
                    >
                      <div style={{ minWidth: 0 }}>
                        <p style={{ fontWeight: 500, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }}>
                          {imp.file_name?.replace(/\.pdf$/i, "") || `Import #${imp.id.slice(0, 4)}`}
                        </p>
                        <p style={{ fontSize: 11, marginTop: 1, color: "var(--text-tertiary)" }}>
                          {timeAgo(imp.created_at)}
                        </p>
                      </div>
                      <span style={{ textAlign: "center", color: "var(--text-secondary)", fontWeight: 500 }}>
                        {imp.total_products}
                      </span>
                      <Badge variant={cfg.variant} dot>{cfg.label}</Badge>
                      <ChevronRight style={{ width: 14, height: 14, marginLeft: "auto", color: "var(--text-disabled)" }} />
                    </Link>
                  );
                })}
              </>
            )}
          </div>
        </div>

        {/* ─── Action panel ─── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Needs action */}
          <div className="card" style={{ padding: 16 }}>
            <p style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-tertiary)", marginBottom: 12 }}>
              Kræver handling
            </p>
            {needsAction.length === 0 ? (
              <div style={{ padding: "12px 0", textAlign: "center" }}>
                <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                  Alt er opdateret
                </p>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                {needsAction.slice(0, 4).map((imp) => (
                  <Link
                    key={imp.id}
                    href={`/dashboard/history/${imp.id}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "8px 0",
                      fontSize: 12,
                      textDecoration: "none",
                      color: "var(--text-secondary)",
                      borderBottom: "1px solid var(--border-secondary)",
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                      <AlertCircle
                        style={{
                          width: 14,
                          height: 14,
                          flexShrink: 0,
                          color: imp.status === "failed" ? "var(--danger)" : "var(--warning)",
                        }}
                      />
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const }}>
                        {imp.file_name?.replace(/\.pdf$/i, "")}
                      </span>
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)", marginLeft: 8, flexShrink: 0 }}>
                      {imp.status === "review" ? "Review" : "Se fejl"}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </div>

          {/* Quick links */}
          <div className="card" style={{ padding: 16 }}>
            <p style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--text-tertiary)", marginBottom: 12 }}>
              Genveje
            </p>
            {[
              { label: "Ny import", href: "/dashboard/import", icon: FileUp },
              { label: "Shopify admin", href: "/dashboard/shopify", icon: ExternalLink },
              { label: "Brands", href: "/dashboard/settings/brands", icon: Tag },
              { label: "Indstillinger", href: "/dashboard/settings", icon: Settings },
            ].map((link, idx) => (
              <Link
                key={link.href}
                href={link.href}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "8px 0",
                  fontSize: 13,
                  textDecoration: "none",
                  color: "var(--text-secondary)",
                  borderBottom: idx < 3 ? "1px solid var(--border-secondary)" : "none",
                  transition: "color 0.1s",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
                onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-secondary)")}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <link.icon style={{ width: 14, height: 14, color: "var(--text-tertiary)" }} />
                  {link.label}
                </span>
                <ChevronRight style={{ width: 14, height: 14, color: "var(--text-disabled)" }} />
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

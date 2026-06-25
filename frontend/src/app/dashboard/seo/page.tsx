"use client";

import { useState, useEffect } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  BarChart3,
  RefreshCw,
  ExternalLink,
  TrendingUp,
  MousePointerClick,
  Eye,
  Target,
  CheckCircle2,
  Search,
  ArrowUpDown,
  Database,
  Layers,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

/* ─── Types ─── */

interface SEOStatus {
  configured: boolean;
  active: boolean;
  property_url: string | null;
  last_synced: string | null;
  total_keywords: number;
  product_types_covered: number;
  dataforseo_configured: boolean;
}

interface KeywordDetail {
  keyword: string;
  product_type: string;
  clicks: number;
  impressions: number;
  avg_position: number;
  ctr: number;
  landing_page: string;
  last_synced: string | null;
}

interface KeywordsResponse {
  summary: Record<string, string[]>;
  details: KeywordDetail[];
}

/* ─── Component ─── */

export default function SEOPage() {
  const { getToken } = useAuth();
  const [status, setStatus] = useState<SEOStatus | null>(null);
  const [keywords, setKeywords] = useState<KeywordDetail[]>([]);
  const [summary, setSummary] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"clicks" | "impressions" | "avg_position" | "ctr">("clicks");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Fetch status + keywords
  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const [statusData, kwData] = await Promise.all([
          apiFetch<SEOStatus>("/api/v1/seo/status", { token: token || undefined }),
          apiFetch<KeywordsResponse>("/api/v1/seo/keywords", { token: token || undefined }).catch(() => null),
        ]);
        setStatus(statusData);
        if (kwData) {
          setKeywords(kwData.details);
          setSummary(kwData.summary);
        }
      } catch {
        setStatus({
          configured: false,
          active: false,
          property_url: null,
          last_synced: null,
          total_keywords: 0,
          product_types_covered: 0,
          dataforseo_configured: false,
        });
      } finally {
        setLoading(false);
      }
    })();
  }, [getToken]);

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const token = await getToken();
      const data = await apiFetch<{ auth_url: string }>("/api/v1/seo/connect", {
        token: token || undefined,
      });
      window.open(data.auth_url, "_self");
    } catch {
      toast.error("Kunne ikke starte Google-forbindelse");
      setConnecting(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const token = await getToken();
      const result = await apiFetch<{ status: string; keywords_synced: number; product_types: number }>(
        "/api/v1/seo/sync",
        { method: "POST", token: token || undefined }
      );
      if (result.status === "ok") {
        toast.success(`${result.keywords_synced} keywords synkroniseret for ${result.product_types} produkttyper`);
      } else {
        toast.info(result.status);
      }
      // Refresh data
      const [statusData, kwData] = await Promise.all([
        apiFetch<SEOStatus>("/api/v1/seo/status", { token: token || undefined }),
        apiFetch<KeywordsResponse>("/api/v1/seo/keywords", { token: token || undefined }).catch(() => null),
      ]);
      setStatus(statusData);
      if (kwData) {
        setKeywords(kwData.details);
        setSummary(kwData.summary);
      }
    } catch {
      toast.error("Sync fejlede — tjek at Search Console har data for dit domæne");
    } finally {
      setSyncing(false);
    }
  };

  const toggleSort = (col: typeof sortBy) => {
    if (sortBy === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(col);
      setSortDir(col === "avg_position" ? "asc" : "desc");
    }
  };

  // Filter + sort keywords
  const productTypes = [...new Set(keywords.map((k) => k.product_type))].sort();
  const filtered = selectedType ? keywords.filter((k) => k.product_type === selectedType) : keywords;
  const sorted = [...filtered].sort((a, b) => {
    const mul = sortDir === "asc" ? 1 : -1;
    return (a[sortBy] - b[sortBy]) * mul;
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div
          className="h-5 w-5 animate-spin rounded-full border-2 border-t-transparent"
          style={{ borderColor: "var(--border-primary)", borderTopColor: "transparent" }}
        />
      </div>
    );
  }

  const isConnected = status?.configured && status?.active;

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <h1
          className="text-[20px] font-medium tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          SEO Keywords
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Søgeord fra Google Search Console — bruges automatisk til at optimere nye produkter
        </p>
      </div>

      {/* Status + actions bar */}
      <div
        className="card mb-6 flex flex-col gap-4 rounded-[var(--radius-lg)] p-5 sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="flex items-center gap-3">
          {isConnected ? (
            <>
              <div
                className="flex h-9 w-9 items-center justify-center rounded-full"
                style={{ background: "var(--success-light)" }}
              >
                <CheckCircle2 className="h-[18px] w-[18px]" style={{ color: "var(--success)" }} />
              </div>
              <div>
                <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                  {status?.property_url || "Search Console forbundet"}
                </p>
                <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  {status?.last_synced
                    ? `Sidst synkroniseret ${new Date(status.last_synced).toLocaleDateString("da-DK", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`
                    : "Ingen sync endnu"}
                </p>
              </div>
            </>
          ) : (
            <>
              <div
                className="flex h-9 w-9 items-center justify-center rounded-full"
                style={{ background: "var(--bg-subdued)" }}
              >
                <Search className="h-[18px] w-[18px]" style={{ color: "var(--text-tertiary)" }} />
              </div>
              <div>
                <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                  Search Console ikke forbundet
                </p>
                <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  Forbind for at hente rigtige søgedata fra Google
                </p>
              </div>
            </>
          )}
        </div>
        <div className="flex gap-2">
          {isConnected ? (
            <Button onClick={handleSync} loading={syncing} variant="secondary" size="sm">
              <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
              Sync nu
            </Button>
          ) : (
            <Button onClick={handleConnect} loading={connecting} size="sm">
              <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
              Forbind Search Console
            </Button>
          )}
        </div>
      </div>

      {/* DataForSEO status bar */}
      <div
        className="card mb-6 flex items-center gap-3 rounded-[var(--radius-lg)] px-5 py-3.5"
      >
        <div
          className="flex h-8 w-8 items-center justify-center rounded-full"
          style={{
            background: status?.dataforseo_configured
              ? "var(--success-light)"
              : "var(--bg-subdued)",
          }}
        >
          <Database
            className="h-4 w-4"
            style={{
              color: status?.dataforseo_configured
                ? "var(--success)"
                : "var(--text-tertiary)",
            }}
          />
        </div>
        <div className="flex-1">
          <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
            DataForSEO
            <span
              className="ml-2 inline-block rounded-full px-2 py-0.5 text-[10px] font-medium"
              style={{
                background: status?.dataforseo_configured
                  ? "var(--success-light)"
                  : "var(--bg-subdued)",
                color: status?.dataforseo_configured
                  ? "var(--success)"
                  : "var(--text-tertiary)",
              }}
            >
              {status?.dataforseo_configured ? "Aktiv" : "Ikke konfigureret"}
            </span>
          </p>
          <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
            {status?.dataforseo_configured
              ? "Keyword-volumen, sværhedsgrad og CPC beriges automatisk ved import"
              : "Forbind DataForSEO under Indstillinger for at berige keywords med søgevolumen og sværhedsgrad"}
          </p>
        </div>
      </div>

      {/* KPI cards */}
      {isConnected && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            {
              label: "Keywords tracket",
              value: status?.total_keywords || 0,
              icon: BarChart3,
            },
            {
              label: "Produkttyper",
              value: status?.product_types_covered || 0,
              icon: Target,
            },
            {
              label: "Totale klik",
              value: keywords.reduce((s, k) => s + k.clicks, 0),
              icon: MousePointerClick,
            },
            {
              label: "Totale visninger",
              value: keywords.reduce((s, k) => s + k.impressions, 0).toLocaleString("da-DK"),
              icon: Eye,
            },
          ].map((kpi) => (
            <div
              key={kpi.label}
              className="card rounded-[var(--radius-lg)] p-4"
            >
              <div className="flex items-center gap-2 mb-2">
                <kpi.icon className="h-3.5 w-3.5" style={{ color: "var(--text-tertiary)" }} />
                <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  {kpi.label}
                </span>
              </div>
              <p className="text-[20px] font-semibold" style={{ color: "var(--text-primary)" }}>
                {kpi.value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* AI prompt injection preview */}
      {isConnected && Object.keys(summary).length > 0 && (
        <div className="card mb-6 rounded-[var(--radius-lg)] p-5">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="h-4 w-4" style={{ color: "var(--accent)" }} />
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Keywords der bruges i AI-prompten
            </p>
          </div>
          <p className="text-[12px] mb-4" style={{ color: "var(--text-tertiary)" }}>
            Disse top-keywords per produkttype injiceres automatisk i AI-prompten når nye produkter analyseres. AI&apos;en bruger dem som inspiration til at generere SEO-keywords der matcher rigtig søgeadfærd.
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary).map(([type, kws]) => (
              <div
                key={type}
                className="rounded-[var(--radius-md)] px-3 py-2"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border-primary)" }}
              >
                <p className="text-[11px] font-medium mb-1" style={{ color: "var(--accent)" }}>
                  {type}
                </p>
                <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                  {kws.slice(0, 5).join(" · ")}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Keyword table */}
      {isConnected && keywords.length > 0 && (
        <div className="card rounded-[var(--radius-lg)] overflow-hidden">
          {/* Filter tabs */}
          <div
            className="flex items-center gap-1 overflow-x-auto px-4 pt-4 pb-2"
          >
            <button
              onClick={() => setSelectedType(null)}
              className="rounded-[var(--radius-md)] px-3 py-1.5 text-[12px] font-medium whitespace-nowrap transition-all"
              style={{
                background: !selectedType ? "var(--accent-light)" : "transparent",
                color: !selectedType ? "var(--accent)" : "var(--text-tertiary)",
              }}
            >
              Alle ({keywords.length})
            </button>
            {productTypes.map((type) => {
              const count = keywords.filter((k) => k.product_type === type).length;
              return (
                <button
                  key={type}
                  onClick={() => setSelectedType(type)}
                  className="rounded-[var(--radius-md)] px-3 py-1.5 text-[12px] font-medium whitespace-nowrap transition-all"
                  style={{
                    background: selectedType === type ? "var(--accent-light)" : "transparent",
                    color: selectedType === type ? "var(--accent)" : "var(--text-tertiary)",
                  }}
                >
                  {type} ({count})
                </button>
              );
            })}
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-primary)" }}>
                  <th
                    className="px-4 py-2.5 text-left text-[11px] font-medium"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    Søgeord
                  </th>
                  {!selectedType && (
                    <th
                      className="px-4 py-2.5 text-left text-[11px] font-medium"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      Produkttype
                    </th>
                  )}
                  {(["clicks", "impressions", "avg_position", "ctr"] as const).map((col) => {
                    const labels = {
                      clicks: "Klik",
                      impressions: "Visninger",
                      avg_position: "Gns. position",
                      ctr: "CTR",
                    };
                    return (
                      <th
                        key={col}
                        className="px-4 py-2.5 text-right text-[11px] font-medium cursor-pointer select-none"
                        style={{ color: sortBy === col ? "var(--accent)" : "var(--text-tertiary)" }}
                        onClick={() => toggleSort(col)}
                      >
                        <span className="inline-flex items-center gap-1">
                          {labels[col]}
                          {sortBy === col && (
                            <ArrowUpDown className="h-3 w-3" />
                          )}
                        </span>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {sorted.map((kw, i) => (
                  <tr
                    key={`${kw.product_type}-${kw.keyword}-${i}`}
                    style={{ borderBottom: "1px solid var(--border-secondary)" }}
                    className="transition-colors"
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <td className="px-4 py-2.5">
                      <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                        {kw.keyword}
                      </span>
                    </td>
                    {!selectedType && (
                      <td className="px-4 py-2.5">
                        <span
                          className="inline-block rounded-[var(--radius-sm)] px-2 py-0.5 text-[11px] font-medium"
                          style={{ background: "var(--bg-subdued)", color: "var(--text-secondary)" }}
                        >
                          {kw.product_type}
                        </span>
                      </td>
                    )}
                    <td className="px-4 py-2.5 text-right">
                      <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                        {kw.clicks}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
                        {kw.impressions.toLocaleString("da-DK")}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <span
                        className="text-[13px] font-medium"
                        style={{
                          color: kw.avg_position <= 10 ? "var(--success)" : kw.avg_position <= 20 ? "var(--warning)" : "var(--text-tertiary)",
                        }}
                      >
                        {kw.avg_position.toFixed(1)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
                        {kw.ctr.toFixed(1)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {sorted.length === 0 && (
            <div className="px-4 py-8 text-center">
              <p className="text-[13px]" style={{ color: "var(--text-tertiary)" }}>
                Ingen keywords fundet{selectedType ? ` for "${selectedType}"` : ""}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Empty state when connected but no data */}
      {isConnected && keywords.length === 0 && (
        <div className="card rounded-[var(--radius-lg)] p-8 text-center">
          <BarChart3 className="mx-auto mb-3 h-8 w-8" style={{ color: "var(--text-disabled)" }} />
          <p className="text-[14px] font-medium mb-1" style={{ color: "var(--text-primary)" }}>
            Ingen keywords synkroniseret endnu
          </p>
          <p className="text-[12px] mb-4" style={{ color: "var(--text-tertiary)" }}>
            Tryk &quot;Sync nu&quot; for at hente søgedata fra Google Search Console. Der skal være produkter live på dit domæne der har fået visninger i Google.
          </p>
          <Button onClick={handleSync} loading={syncing} size="sm">
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Sync keywords
          </Button>
        </div>
      )}

      {/* Not connected state */}
      {!isConnected && (
        <div className="card rounded-[var(--radius-lg)] p-8 text-center">
          <Search className="mx-auto mb-3 h-8 w-8" style={{ color: "var(--text-disabled)" }} />
          <p className="text-[14px] font-medium mb-1" style={{ color: "var(--text-primary)" }}>
            Forbind Google Search Console
          </p>
          <p className="text-[12px] mb-2 max-w-md mx-auto" style={{ color: "var(--text-tertiary)" }}>
            Search Console giver adgang til data om hvilke søgeord der bringer trafik til din butik. Systemet bruger disse data til automatisk at generere bedre SEO-keywords for nye produkter.
          </p>
          <p className="text-[12px] mb-5 max-w-md mx-auto" style={{ color: "var(--text-tertiary)" }}>
            Krav: Dit domæne skal være verificeret i Google Search Console, og du skal logge ind med den Google-konto der har adgang.
          </p>
          <Button onClick={handleConnect} loading={connecting} size="sm">
            <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
            Forbind Search Console
          </Button>
        </div>
      )}

      {/* How it works — 3-layer pipeline */}
      <div
        className="mt-6 rounded-[var(--radius-lg)] px-5 py-4"
        style={{ background: "var(--bg-primary)", border: "1px solid var(--border-secondary)" }}
      >
        <div className="flex items-center gap-2 mb-3">
          <Layers className="h-4 w-4" style={{ color: "var(--text-secondary)" }} />
          <p className="text-[12px] font-medium" style={{ color: "var(--text-secondary)" }}>
            3-lags keyword-pipeline
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-3 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
          <div>
            <div className="flex items-center gap-1.5 mb-0.5">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--success)" }}
              />
              <span className="font-medium" style={{ color: "var(--text-secondary)" }}>
                Lag 1 — Autocomplete
              </span>
            </div>
            <span className="text-[10px]" style={{ color: "var(--success)" }}>Altid aktiv</span>
            <p className="mt-1">AI-genererede keywords valideres mod Googles autocomplete. Keywords uden søgeaktivitet erstattes med bedre alternativer.</p>
          </div>
          <div>
            <div className="flex items-center gap-1.5 mb-0.5">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{
                  background: status?.dataforseo_configured
                    ? "var(--success)"
                    : "var(--text-disabled)",
                }}
              />
              <span className="font-medium" style={{ color: "var(--text-secondary)" }}>
                Lag 2 — DataForSEO
              </span>
            </div>
            <span
              className="text-[10px]"
              style={{
                color: status?.dataforseo_configured
                  ? "var(--success)"
                  : "var(--text-tertiary)",
              }}
            >
              {status?.dataforseo_configured ? "Aktiv" : "Kræver API-credentials"}
            </span>
            <p className="mt-1">Beriger keywords med søgevolumen, sværhedsgrad og CPC. Finder relaterede keywords med højere potentiale.</p>
          </div>
          <div>
            <div className="flex items-center gap-1.5 mb-0.5">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{
                  background: isConnected
                    ? "var(--success)"
                    : "var(--text-disabled)",
                }}
              />
              <span className="font-medium" style={{ color: "var(--text-secondary)" }}>
                Lag 3 — Search Console
              </span>
            </div>
            <span
              className="text-[10px]"
              style={{
                color: isConnected ? "var(--success)" : "var(--text-tertiary)",
              }}
            >
              {isConnected ? "Forbundet" : "Kræver forbindelse"}
            </span>
            <p className="mt-1">Top-keywords per produkttype injiceres i AI-prompten. Jo mere trafik din butik får, jo bedre bliver fremtidige keywords.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useState, useEffect, Suspense } from "react";
import { useAuth } from "@clerk/nextjs";
import { useSearchParams } from "next/navigation";
import { ChevronRight, ImageIcon, Search, CheckCircle2, ExternalLink, RefreshCw, BarChart3, Database, FolderOpen } from "lucide-react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

interface Settings {
  default_eur_rate: number;
  default_markup: number;
}

interface SEOStatus {
  configured: boolean;
  active: boolean;
  property_url: string | null;
  last_synced: string | null;
  total_keywords: number;
  product_types_covered: number;
  dataforseo_configured: boolean;
}

interface DriveStatus {
  connected: boolean;
  root_folder_id: string | null;
  connected_at: string | null;
}

export default function SettingsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20">
          <div
            className="h-5 w-5 animate-spin rounded-full border-2 border-t-transparent"
            style={{ borderColor: "var(--border-primary)", borderTopColor: "transparent" }}
          />
        </div>
      }
    >
      <SettingsPageInner />
    </Suspense>
  );
}

function SettingsPageInner() {
  const { getToken } = useAuth();
  const searchParams = useSearchParams();
  const [eurRate, setEurRate] = useState(7.46);
  const [markup, setMarkup] = useState(2.5);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // SEO / Search Console state
  const [seoStatus, setSeoStatus] = useState<SEOStatus | null>(null);
  const [seoLoading, setSeoLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // Google Drive state
  const [driveStatus, setDriveStatus] = useState<DriveStatus | null>(null);
  const [driveLoading, setDriveLoading] = useState(true);
  const [driveConnecting, setDriveConnecting] = useState(false);
  const [driveDisconnecting, setDriveDisconnecting] = useState(false);
  const [rootFolder, setRootFolder] = useState("");
  const [rootFolderSaving, setRootFolderSaving] = useState(false);

  // DataForSEO state
  const [dfsLogin, setDfsLogin] = useState("");
  const [dfsPassword, setDfsPassword] = useState("");
  const [dfsSaving, setDfsSaving] = useState(false);
  const [dfsLoginHint, setDfsLoginHint] = useState<string | null>(null);
  const [dfsDisconnecting, setDfsDisconnecting] = useState(false);

  // Show toast on OAuth redirect
  useEffect(() => {
    if (searchParams.get("seo_connected") === "true") {
      toast.success("Search Console forbundet!");
      window.history.replaceState({}, "", "/dashboard/settings");
    }
    const seoError = searchParams.get("seo_error");
    if (seoError) {
      toast.error(`Search Console fejl: ${seoError}`);
      window.history.replaceState({}, "", "/dashboard/settings");
    }
    if (searchParams.get("drive_connected") === "true") {
      toast.success("Google Drive forbundet!");
      window.history.replaceState({}, "", "/dashboard/settings");
    }
    const driveError = searchParams.get("drive_error");
    if (driveError) {
      toast.error(`Google Drive fejl: ${driveError}`);
      window.history.replaceState({}, "", "/dashboard/settings");
    }
  }, [searchParams]);

  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const data = await apiFetch<Settings>("/api/v1/settings", {
          token: token || undefined,
        });
        setEurRate(data.default_eur_rate);
        setMarkup(data.default_markup);
      } catch (err) {
        toast.error("Kunne ikke hente indstillinger — bruger standardværdier");
      } finally {
        setLoading(false);
      }
    })();
  }, [getToken]);

  // Fetch SEO status
  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const data = await apiFetch<SEOStatus>("/api/v1/seo/status", {
          token: token || undefined,
        });
        setSeoStatus(data);
      } catch {
        // Not configured yet — that's fine
        setSeoStatus({ configured: false, active: false, property_url: null, last_synced: null, total_keywords: 0, product_types_covered: 0, dataforseo_configured: false });
      } finally {
        setSeoLoading(false);
      }
    })();
  }, [getToken]);

  const handleConnectSearchConsole = async () => {
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

  const handleSyncKeywords = async () => {
    setSyncing(true);
    try {
      const token = await getToken();
      const data = await apiFetch<{ status: string; keywords_synced: number; product_types: number }>("/api/v1/seo/sync", {
        method: "POST",
        token: token || undefined,
      });
      if (data.status === "ok") {
        toast.success(`${data.keywords_synced} keywords synkroniseret for ${data.product_types} produkttyper`);
      } else {
        toast.info(data.status);
      }
      // Refresh status
      const status = await apiFetch<SEOStatus>("/api/v1/seo/status", { token: token || undefined });
      setSeoStatus(status);
    } catch {
      toast.error("Sync fejlede — tjek at Search Console har data");
    } finally {
      setSyncing(false);
    }
  };

  // Fetch Google Drive status
  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const data = await apiFetch<DriveStatus>("/api/v1/drive/status", {
          token: token || undefined,
        });
        setDriveStatus(data);
        setRootFolder(data.root_folder_id || "");
      } catch {
        // Not connected yet — that's fine
        setDriveStatus({ connected: false, root_folder_id: null, connected_at: null });
      } finally {
        setDriveLoading(false);
      }
    })();
  }, [getToken]);

  const handleConnectDrive = async () => {
    setDriveConnecting(true);
    try {
      const token = await getToken();
      const data = await apiFetch<{ auth_url: string }>("/api/v1/drive/connect", {
        token: token || undefined,
      });
      window.open(data.auth_url, "_self");
    } catch {
      toast.error("Kunne ikke starte Google Drive-forbindelse");
      setDriveConnecting(false);
    }
  };

  const handleDisconnectDrive = async () => {
    setDriveDisconnecting(true);
    try {
      const token = await getToken();
      await apiFetch("/api/v1/drive/disconnect", {
        method: "POST",
        token: token || undefined,
      });
      setDriveStatus({ connected: false, root_folder_id: null, connected_at: null });
      setRootFolder("");
      toast.success("Google Drive frakoblet");
    } catch {
      toast.error("Kunne ikke frakoble Google Drive");
    } finally {
      setDriveDisconnecting(false);
    }
  };

  const handleSaveRootFolder = async () => {
    setRootFolderSaving(true);
    try {
      const token = await getToken();
      const data = await apiFetch<DriveStatus>("/api/v1/drive/set-root-folder", {
        method: "POST",
        token: token || undefined,
        body: JSON.stringify({ root_folder_id: rootFolder.trim() }),
      });
      setDriveStatus(data);
      setRootFolder(data.root_folder_id || "");
      toast.success(
        data.root_folder_id ? "Rodmappe gemt" : "Rodmappe ryddet — hele drevet gennemsøges"
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Kunne ikke gemme rodmappen";
      toast.error(msg);
    } finally {
      setRootFolderSaving(false);
    }
  };

  // Fetch DataForSEO config on mount
  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const data = await apiFetch<{ configured: boolean; login_hint: string | null }>("/api/v1/seo/dataforseo", {
          token: token || undefined,
        });
        if (data.configured && data.login_hint) {
          setDfsLoginHint(data.login_hint);
        }
      } catch {
        // Not configured — that's fine
      }
    })();
  }, [getToken]);

  const handleSaveDataForSEO = async () => {
    if (!dfsLogin || !dfsPassword) {
      toast.error("Udfyld både login og password");
      return;
    }
    setDfsSaving(true);
    try {
      const token = await getToken();
      const data = await apiFetch<{ configured: boolean; login_hint: string | null }>("/api/v1/seo/dataforseo", {
        method: "PUT",
        token: token || undefined,
        body: JSON.stringify({ login: dfsLogin, password: dfsPassword }),
      });
      toast.success("DataForSEO forbundet!");
      setDfsLoginHint(data.login_hint);
      setDfsLogin("");
      setDfsPassword("");
      // Refresh SEO status
      const status = await apiFetch<SEOStatus>("/api/v1/seo/status", { token: token || undefined });
      setSeoStatus(status);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Kunne ikke gemme DataForSEO-credentials";
      toast.error(msg);
    } finally {
      setDfsSaving(false);
    }
  };

  const handleDisconnectDataForSEO = async () => {
    setDfsDisconnecting(true);
    try {
      const token = await getToken();
      await apiFetch("/api/v1/seo/dataforseo", {
        method: "DELETE",
        token: token || undefined,
      });
      toast.success("DataForSEO frakoblet");
      setDfsLoginHint(null);
      // Refresh SEO status
      const status = await apiFetch<SEOStatus>("/api/v1/seo/status", { token: token || undefined });
      setSeoStatus(status);
    } catch {
      toast.error("Kunne ikke frakoble DataForSEO");
    } finally {
      setDfsDisconnecting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const token = await getToken();
      await apiFetch("/api/v1/settings", {
        method: "PATCH",
        token: token || undefined,
        body: JSON.stringify({
          default_eur_rate: eurRate,
          default_markup: markup,
        }),
      });
      toast.success("Indstillinger gemt");
    } catch {
      toast.error("Kunne ikke gemme indstillinger");
    } finally {
      setSaving(false);
    }
  };

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

  return (
    <div>
      <div className="mb-6">
        <h1
          className="text-[20px] font-medium tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          Indstillinger
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Standardværdier for nye importer
        </p>
      </div>

      {/* Brand management link */}
      <Link
        href="/dashboard/settings/brands"
        className="card mb-6 flex items-center justify-between rounded-[var(--radius-lg)] px-6 py-4 transition-all"
      >
        <div className="flex items-center gap-4">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)]"
            style={{ background: "var(--accent-light)" }}
          >
            <ImageIcon className="h-5 w-5" style={{ color: "var(--accent)" }} />
          </div>
          <div>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Brands & Image Banks
            </p>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Administrer brands og deres image bank-forbindelser
            </p>
          </div>
        </div>
        <ChevronRight className="h-5 w-5" style={{ color: "var(--text-tertiary)" }} />
      </Link>

      {/* Search Console / SEO section */}
      <div className="card mb-6 rounded-[var(--radius-lg)] p-6">
        <div className="flex items-center gap-3 mb-4">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)]"
            style={{ background: "var(--accent-light)" }}
          >
            <BarChart3 className="h-5 w-5" style={{ color: "var(--accent)" }} />
          </div>
          <div>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Google Search Console
            </p>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Forbind Search Console for smartere SEO-keywords baseret på rigtig søgedata
            </p>
          </div>
        </div>

        {seoLoading ? (
          <div className="flex items-center gap-2 py-3">
            <div
              className="h-4 w-4 animate-spin rounded-full border-2 border-t-transparent"
              style={{ borderColor: "var(--border-primary)", borderTopColor: "transparent" }}
            />
            <span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>Henter status...</span>
          </div>
        ) : seoStatus?.configured && seoStatus?.active ? (
          <div>
            {/* Connected state */}
            <div
              className="flex items-center justify-between rounded-[var(--radius-md)] px-3 py-2.5 mb-4"
              style={{ background: "var(--success-light)" }}
            >
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4" style={{ color: "var(--success)" }} />
                <span className="text-[12px] font-medium" style={{ color: "var(--success)" }}>
                  Forbundet til {seoStatus.property_url || "Search Console"}
                </span>
              </div>
              <button
                onClick={handleConnectSearchConsole}
                className="text-[11px] font-medium px-2 py-1 rounded-[var(--radius-sm)] transition-colors"
                style={{
                  color: "var(--text-secondary)",
                  background: "transparent",
                  border: "1px solid var(--border-primary)",
                }}
              >
                Genforbind
              </button>
            </div>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <div
                className="rounded-[var(--radius-md)] px-3 py-2.5"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border-primary)" }}
              >
                <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>Keywords tracket</p>
                <p className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  {seoStatus.total_keywords}
                </p>
              </div>
              <div
                className="rounded-[var(--radius-md)] px-3 py-2.5"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border-primary)" }}
              >
                <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>Produkttyper dækket</p>
                <p className="text-[16px] font-semibold" style={{ color: "var(--text-primary)" }}>
                  {seoStatus.product_types_covered}
                </p>
              </div>
            </div>

            {seoStatus.last_synced && (
              <p className="text-[11px] mb-3" style={{ color: "var(--text-tertiary)" }}>
                Sidst synkroniseret: {new Date(seoStatus.last_synced).toLocaleDateString("da-DK", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}
              </p>
            )}

            <Button
              onClick={handleSyncKeywords}
              loading={syncing}
              variant="secondary"
              size="sm"
            >
              <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
              Sync keywords nu
            </Button>
          </div>
        ) : (
          <div>
            {/* Not connected state */}
            <p className="text-[12px] mb-4" style={{ color: "var(--text-secondary)" }}>
              Når Search Console er forbundet, henter systemet automatisk data om hvilke søgeord der giver trafik til din butik. Disse data bruges til at generere bedre SEO-keywords for nye produkter.
            </p>
            <Button
              onClick={handleConnectSearchConsole}
              loading={connecting}
              size="sm"
            >
              <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
              Forbind Google Search Console
            </Button>
          </div>
        )}
      </div>

      {/* Google Drive section */}
      <div className="card mb-6 rounded-[var(--radius-lg)] p-6">
        <div className="flex items-center gap-3 mb-4">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)]"
            style={{
              background: driveStatus?.connected ? "var(--success-light)" : "var(--bg-subdued)",
            }}
          >
            <FolderOpen
              className="h-5 w-5"
              style={{
                color: driveStatus?.connected ? "var(--success)" : "var(--text-tertiary)",
              }}
            />
          </div>
          <div>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Google Drive
            </p>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Forbind Drive så ordrebekræftelser kan bruges som datakilde ved import
            </p>
          </div>
        </div>

        {driveLoading ? (
          <div className="flex items-center gap-2 py-3">
            <div
              className="h-4 w-4 animate-spin rounded-full border-2 border-t-transparent"
              style={{ borderColor: "var(--border-primary)", borderTopColor: "transparent" }}
            />
            <span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>Henter status...</span>
          </div>
        ) : driveStatus?.connected ? (
          <div>
            <div
              className="flex items-center justify-between rounded-[var(--radius-md)] px-3 py-2.5 mb-4"
              style={{ background: "var(--success-light)" }}
            >
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4" style={{ color: "var(--success)" }} />
                <span className="text-[12px] font-medium" style={{ color: "var(--success)" }}>
                  Forbundet ✓
                </span>
              </div>
              <button
                onClick={handleDisconnectDrive}
                disabled={driveDisconnecting}
                className="text-[11px] px-2 py-1 rounded-[var(--radius-sm)] transition-colors"
                style={{ color: "var(--text-tertiary)", background: "transparent" }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--danger)";
                  e.currentTarget.style.background = "var(--bg-primary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--text-tertiary)";
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {driveDisconnecting ? "Frakobler..." : "Frakobl"}
              </button>
            </div>

            <div className="mb-3">
              <label className="mb-1 block text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
                Rodmappe-ID (valgfrit)
              </label>
              <input
                type="text"
                value={rootFolder}
                onChange={(e) => setRootFolder(e.target.value)}
                placeholder="fx 1QWf2LGX7ehmMMO0YdEwsmxfI9tUOR8VE"
                className="w-full rounded-[var(--radius-md)] px-3 py-2 text-[13px]"
                style={{
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-primary)",
                  outline: "none",
                }}
              />
              <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                Begrænser søgningen til én mappe. ID&apos;et er den sidste del af mappens
                URL i Drive. Lad feltet stå tomt for at gennemsøge hele drevet.
              </p>
            </div>

            <Button
              onClick={handleSaveRootFolder}
              loading={rootFolderSaving}
              variant="secondary"
              size="sm"
            >
              Gem rodmappe
            </Button>
          </div>
        ) : (
          <div>
            <p className="text-[12px] mb-4" style={{ color: "var(--text-secondary)" }}>
              Når Drive er forbundet, kan systemet finde ordrebekræftelser i dine
              mapper og bruge dem som datakilde ved import. Der gives kun læseadgang.
            </p>
            <Button onClick={handleConnectDrive} loading={driveConnecting} size="sm">
              <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
              Forbind Google Drive
            </Button>
          </div>
        )}
      </div>

      {/* DataForSEO section */}
      <div className="card mb-6 rounded-[var(--radius-lg)] p-6">
        <div className="flex items-center gap-3 mb-4">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)]"
            style={{
              background: seoStatus?.dataforseo_configured
                ? "var(--success-light)"
                : "var(--bg-subdued)",
            }}
          >
            <Database
              className="h-5 w-5"
              style={{
                color: seoStatus?.dataforseo_configured
                  ? "var(--success)"
                  : "var(--text-tertiary)",
              }}
            />
          </div>
          <div>
            <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              DataForSEO — Keyword Enrichment
            </p>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Beriger keywords med søgevolumen, sværhedsgrad og CPC fra DataForSEO API
            </p>
          </div>
        </div>

        {seoLoading ? (
          <div className="flex items-center gap-2 py-3">
            <div
              className="h-4 w-4 animate-spin rounded-full border-2 border-t-transparent"
              style={{ borderColor: "var(--border-primary)", borderTopColor: "transparent" }}
            />
            <span className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>Henter status...</span>
          </div>
        ) : seoStatus?.dataforseo_configured || dfsLoginHint ? (
          <div>
            <div
              className="flex items-center justify-between rounded-[var(--radius-md)] px-3 py-2.5 mb-3"
              style={{ background: "var(--success-light)" }}
            >
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4" style={{ color: "var(--success)" }} />
                <span className="text-[12px] font-medium" style={{ color: "var(--success)" }}>
                  Forbundet som {dfsLoginHint || "DataForSEO"}
                </span>
              </div>
              <button
                onClick={handleDisconnectDataForSEO}
                disabled={dfsDisconnecting}
                className="text-[11px] px-2 py-1 rounded-[var(--radius-sm)] transition-colors"
                style={{ color: "var(--text-tertiary)", background: "transparent" }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--danger)";
                  e.currentTarget.style.background = "var(--bg-primary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--text-tertiary)";
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {dfsDisconnecting ? "Frakobler..." : "Frakobl"}
              </button>
            </div>
            <p className="text-[12px]" style={{ color: "var(--text-tertiary)" }}>
              Alle nye imports beriges automatisk med søgevolumen, keyword difficulty og CPC for det danske marked.
            </p>
          </div>
        ) : (
          <div>
            <p className="text-[12px] mb-3" style={{ color: "var(--text-secondary)" }}>
              DataForSEO beriger AI-genererede keywords med faktisk søgedata — volumen, sværhedsgrad og CPC. Uden dette lag virker Lag 1 (Autocomplete) stadig, men du får ikke data om søgevolumen.
            </p>
            <div className="space-y-3 mb-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
                  DataForSEO Login (email)
                </label>
                <input
                  type="email"
                  value={dfsLogin}
                  onChange={(e) => setDfsLogin(e.target.value)}
                  placeholder="din-email@example.com"
                  className="w-full rounded-[var(--radius-md)] px-3 py-2 text-[13px]"
                  style={{
                    background: "var(--bg-primary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-primary)",
                    outline: "none",
                  }}
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
                  DataForSEO Password
                </label>
                <input
                  type="password"
                  value={dfsPassword}
                  onChange={(e) => setDfsPassword(e.target.value)}
                  placeholder="API password"
                  className="w-full rounded-[var(--radius-md)] px-3 py-2 text-[13px]"
                  style={{
                    background: "var(--bg-primary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-primary)",
                    outline: "none",
                  }}
                />
              </div>
              <Button onClick={handleSaveDataForSEO} loading={dfsSaving} size="sm">
                <Database className="h-3.5 w-3.5 mr-1.5" />
                Forbind DataForSEO
              </Button>
            </div>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Opret konto på{" "}
              <a href="https://app.dataforseo.com/register" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: "var(--accent)" }}>
                app.dataforseo.com
              </a>
              {" "}— pay-as-you-go, ~$0.10 per 1.000 keywords
            </p>
          </div>
        )}
      </div>

      <div className="card max-w-lg rounded-[var(--radius-lg)] p-6"
      >
        <div className="space-y-4">
          <div>
            <label
              className="mb-1.5 block text-[13px] font-medium"
              style={{ color: "var(--text-secondary)" }}
            >
              Standard EUR-kurs
            </label>
            <input
              type="number"
              value={eurRate}
              onChange={(e) => setEurRate(parseFloat(e.target.value) || 7.46)}
              step={0.01}
              min={0.01}
              max={20}
              className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
              style={{
                background: "var(--bg-primary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-primary)",
                outline: "none",
              }}
            />
          </div>
          <div>
            <label
              className="mb-1.5 block text-[13px] font-medium"
              style={{ color: "var(--text-secondary)" }}
            >
              Standard markup-faktor
            </label>
            <input
              type="number"
              value={markup}
              onChange={(e) => setMarkup(parseFloat(e.target.value) || 2.5)}
              step={0.1}
              min={1}
              max={10}
              className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
              style={{
                background: "var(--bg-primary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-primary)",
                outline: "none",
              }}
            />
          </div>
        </div>
        <div className="mt-6 flex justify-end">
          <Button onClick={handleSave} loading={saving}>
            Gem indstillinger
          </Button>
        </div>
      </div>
    </div>
  );
}

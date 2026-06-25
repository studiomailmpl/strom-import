"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  Loader2,
  Search,
  Plus,
  ImageIcon,
  Trash2,
  ChevronLeft,
  X,
  Globe,
  Database,
  Check,
} from "lucide-react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

/* ─── Types ─── */

interface Brand {
  id: string;
  name: string;
  slug: string;
  markup: number;
  image_bank_url: string | null;
  image_bank_type: string | null;
  image_bank_search_pattern: string | null;
  image_bank_notes: string | null;
  website_url: string | null;
  search_url_pattern: string | null;
  is_active: boolean;
  created_at: string | null;
}

interface BrandSuggestion {
  name: string;
  slug: string;
  website: string | null;
  search_url: string | null;
  already_added: boolean;
}

interface BrandStats {
  total_brands: number;
  with_image_bank: number;
  without_image_bank: number;
}

/* ─── Image bank types ─── */

const IMAGE_BANK_TYPES: { value: string; label: string; icon: string; searchHint: string }[] = [
  { value: "datadwell", label: "Datadwell", icon: "DW", searchHint: "/search?q={sku}" },
  { value: "canto", label: "Canto", icon: "CA", searchHint: "/search?keyword={sku}" },
  { value: "trendmark", label: "Trendmark", icon: "TM", searchHint: "/search?query={sku}" },
  { value: "brandos", label: "Brandos", icon: "BR", searchHint: "/search?q={sku}" },
  { value: "custom", label: "Anden", icon: "?", searchHint: "" },
];

/* ─── Component ─── */

export default function BrandsPage() {
  const { getToken } = useAuth();
  const [brands, setBrands] = useState<Brand[]>([]);
  const [stats, setStats] = useState<BrandStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const [showAddPanel, setShowAddPanel] = useState(false);
  const [addSearch, setAddSearch] = useState("");
  const [suggestions, setSuggestions] = useState<BrandSuggestion[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);

  const [editingBrand, setEditingBrand] = useState<Brand | null>(null);
  const [editForm, setEditForm] = useState({
    markup: 2.5,
    image_bank_url: "",
    image_bank_type: "",
    image_bank_search_pattern: "",
    image_bank_notes: "",
    website_url: "",
    search_url_pattern: "",
  });
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const addSearchRef = useRef<HTMLInputElement>(null);

  /* ─── Fetch brands ─── */

  const fetchBrands = useCallback(async () => {
    try {
      const token = await getToken();
      const [brandsData, statsData] = await Promise.all([
        apiFetch<Brand[]>("/api/v1/brands", { token: token || undefined }),
        apiFetch<BrandStats>("/api/v1/brands/stats", { token: token || undefined }),
      ]);
      setBrands(brandsData);
      setStats(statsData);
    } catch {
      toast.error("Kunne ikke hente brands");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchBrands();
  }, [fetchBrands]);

  /* ─── Search suggestions ─── */

  useEffect(() => {
    if (!showAddPanel) return;

    const timer = setTimeout(async () => {
      setLoadingSuggestions(true);
      try {
        const token = await getToken();
        const data = await apiFetch<BrandSuggestion[]>(
          `/api/v1/brands/suggestions?search=${encodeURIComponent(addSearch)}`,
          { token: token || undefined }
        );
        setSuggestions(data);
      } catch {
        // Ignore
      } finally {
        setLoadingSuggestions(false);
      }
    }, 200);

    return () => clearTimeout(timer);
  }, [addSearch, showAddPanel, getToken]);

  /* ─── Add brand ─── */

  const handleAddBrand = async (suggestion: BrandSuggestion) => {
    if (suggestion.already_added) return;

    setAdding(suggestion.slug);
    try {
      const token = await getToken();
      await apiFetch("/api/v1/brands", {
        method: "POST",
        token: token || undefined,
        body: JSON.stringify({
          name: suggestion.name,
          slug: suggestion.slug,
          website_url: suggestion.website,
          search_url_pattern: suggestion.search_url,
        }),
      });
      toast.success(`${suggestion.name} tilføjet`);
      await fetchBrands();
      setSuggestions((prev) =>
        prev.map((s) =>
          s.slug === suggestion.slug ? { ...s, already_added: true } : s
        )
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Kunne ikke tilføje brand");
    } finally {
      setAdding(null);
    }
  };

  const handleAddCustomBrand = async () => {
    if (!addSearch.trim()) return;

    setAdding("custom");
    try {
      const token = await getToken();
      await apiFetch("/api/v1/brands", {
        method: "POST",
        token: token || undefined,
        body: JSON.stringify({ name: addSearch.trim() }),
      });
      toast.success(`${addSearch.trim()} tilføjet`);
      setAddSearch("");
      await fetchBrands();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Kunne ikke tilføje brand");
    } finally {
      setAdding(null);
    }
  };

  /* ─── Edit brand ─── */

  const openEdit = (brand: Brand) => {
    setEditingBrand(brand);
    setEditForm({
      markup: brand.markup || 2.5,
      image_bank_url: brand.image_bank_url || "",
      image_bank_type: brand.image_bank_type || "",
      image_bank_search_pattern: brand.image_bank_search_pattern || "",
      image_bank_notes: brand.image_bank_notes || "",
      website_url: brand.website_url || "",
      search_url_pattern: brand.search_url_pattern || "",
    });
  };

  const handleSave = async () => {
    if (!editingBrand) return;

    setSaving(true);
    try {
      const token = await getToken();
      await apiFetch(`/api/v1/brands/${editingBrand.id}`, {
        method: "PATCH",
        token: token || undefined,
        body: JSON.stringify(editForm),
      });
      toast.success(`${editingBrand.name} opdateret`);
      setEditingBrand(null);
      await fetchBrands();
    } catch {
      toast.error("Kunne ikke opdatere brand");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!editingBrand) return;

    setDeleting(true);
    try {
      const token = await getToken();
      await apiFetch(`/api/v1/brands/${editingBrand.id}`, {
        method: "DELETE",
        token: token || undefined,
      });
      toast.success(`${editingBrand.name} fjernet`);
      setEditingBrand(null);
      await fetchBrands();
    } catch {
      toast.error("Kunne ikke fjerne brand");
    } finally {
      setDeleting(false);
    }
  };

  /* ─── Filter ─── */

  const filteredBrands = brands.filter((b) =>
    b.name.toLowerCase().includes(search.toLowerCase())
  );

  /* ─── Loading ─── */

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

  /* ─── Edit panel ─── */

  if (editingBrand) {
    return (
      <div>
        <button
          onClick={() => setEditingBrand(null)}
          className="mb-6 flex items-center gap-1.5 text-[13px] transition-colors"
          style={{ color: "var(--text-tertiary)" }}
        >
          <ChevronLeft className="h-4 w-4" />
          Tilbage til brands
        </button>

        <div className="mb-6">
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            {editingBrand.name}
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Konfigurer image bank og brand-detaljer
          </p>
        </div>

        <div className="max-w-lg space-y-6">
          {/* Markup */}
          <div className="card rounded-[var(--radius-lg)] p-6"
          >
            <h3 className="mb-1 text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Markup
            </h3>
            <p className="mb-4 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Bruges til at beregne udsalgspris fra kostpris (kostpris × kurs × markup)
            </p>

            <div className="flex items-center gap-3">
              <input
                type="number"
                step="0.1"
                min="1"
                max="10"
                value={editForm.markup}
                onChange={(e) =>
                  setEditForm((f) => ({
                    ...f,
                    markup: parseFloat(e.target.value) || 2.5,
                  }))
                }
                className="w-24 rounded-[var(--radius-md)] px-3 py-2.5 text-[13px] font-medium"
                style={{
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-primary)",
                  outline: "none",
                }}
              />
              <span className="text-[13px]" style={{ color: "var(--text-tertiary)" }}>×</span>
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                F.eks. kostpris €100 × 7,46 × {editForm.markup} = {Math.round((100 * 7.46 * editForm.markup) / 50) * 50} kr
              </span>
            </div>
          </div>

          {/* Image bank */}
          <div className="card rounded-[var(--radius-lg)] p-6">
            <h3
              className="mb-1 text-[13px] font-medium flex items-center gap-2"
              style={{ color: "var(--text-primary)" }}
            >
              <ImageIcon className="h-4 w-4" style={{ color: "var(--accent)" }} />
              Image Bank
            </h3>
            <p className="mb-4 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Forbind til brandets billedportal for automatisk at hente packshot-billeder under import
            </p>

            <div className="space-y-4">
              <div>
                <label
                  className="mb-1.5 block text-[13px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Portal-type
                </label>
                <div className="flex flex-wrap gap-2">
                  {IMAGE_BANK_TYPES.map((type) => (
                    <button
                      key={type.value}
                      onClick={() => {
                        setEditForm((f) => {
                          const newType = f.image_bank_type === type.value ? "" : type.value;
                          // Auto-suggest search pattern when selecting a known type
                          let newPattern = f.image_bank_search_pattern;
                          if (newType && newType !== "custom" && f.image_bank_url && !f.image_bank_search_pattern) {
                            const baseUrl = f.image_bank_url.replace(/\/$/, "");
                            const hint = IMAGE_BANK_TYPES.find((t) => t.value === newType)?.searchHint || "";
                            if (hint) {
                              newPattern = `${baseUrl}${hint}`;
                            }
                          }
                          return { ...f, image_bank_type: newType, image_bank_search_pattern: newPattern };
                        });
                      }}
                      className="flex items-center gap-1.5 rounded-[var(--radius-md)] px-3 py-2 text-[13px] font-medium transition-colors"
                      style={{
                        background:
                          editForm.image_bank_type === type.value
                            ? "var(--accent-light)"
                            : "var(--bg-primary)",
                        color:
                          editForm.image_bank_type === type.value
                            ? "var(--accent-text)"
                            : "var(--text-secondary)",
                        border:
                          editForm.image_bank_type === type.value
                            ? "1px solid var(--accent)"
                            : "1px solid var(--border-primary)",
                      }}
                    >
                      <span
                        className="text-[10px] font-bold"
                        style={{ color: "var(--text-tertiary)" }}
                      >
                        {type.icon}
                      </span>
                      {type.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label
                  className="mb-1.5 block text-[13px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Portal URL
                </label>
                <input
                  type="url"
                  value={editForm.image_bank_url}
                  onChange={(e) => {
                    const url = e.target.value;
                    setEditForm((f) => {
                      // Auto-update search pattern if type is selected and pattern is empty or was auto-generated
                      let newPattern = f.image_bank_search_pattern;
                      if (f.image_bank_type && f.image_bank_type !== "custom" && url) {
                        const hint = IMAGE_BANK_TYPES.find((t) => t.value === f.image_bank_type)?.searchHint || "";
                        if (hint) {
                          const baseUrl = url.replace(/\/$/, "");
                          newPattern = `${baseUrl}${hint}`;
                        }
                      }
                      return { ...f, image_bank_url: url, image_bank_search_pattern: newPattern };
                    });
                  }}
                  placeholder="https://brand.datadwell.app/gallery"
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
                  Søge-URL mønster
                  {editForm.image_bank_search_pattern && (
                    <span
                      className="ml-2 inline-flex items-center gap-1 rounded-[var(--radius-full)] px-1.5 py-0.5 text-[10px] font-medium"
                      style={{ background: "var(--success-light)", color: "var(--success)" }}
                    >
                      <Check className="h-2.5 w-2.5" />
                      Aktiv
                    </span>
                  )}
                </label>
                <input
                  type="text"
                  value={editForm.image_bank_search_pattern}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, image_bank_search_pattern: e.target.value }))
                  }
                  placeholder="https://portal.example.com/search?q={sku}"
                  className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px] font-mono"
                  style={{
                    background: "var(--bg-primary)",
                    color: "var(--text-primary)",
                    border: editForm.image_bank_search_pattern
                      ? "1px solid var(--success)"
                      : "1px solid var(--border-primary)",
                    outline: "none",
                  }}
                />
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  {"{sku}"} erstattes med produktets artikelnummer. Dette søgemønster bruges under import til at finde billeder.
                </p>
              </div>

              <div>
                <label
                  className="mb-1.5 block text-[13px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Noter
                  <span className="ml-1 font-normal" style={{ color: "var(--text-tertiary)" }}>
                    (valgfrit)
                  </span>
                </label>
                <textarea
                  value={editForm.image_bank_notes}
                  onChange={(e) =>
                    setEditForm((f) => ({
                      ...f,
                      image_bank_notes: e.target.value,
                    }))
                  }
                  placeholder="Login-oplysninger, kontaktperson..."
                  rows={2}
                  className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px] resize-none"
                  style={{
                    background: "var(--bg-primary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-primary)",
                    outline: "none",
                  }}
                />
              </div>
            </div>
          </div>

          {/* Website & Image Scraping */}
          <div className="card rounded-[var(--radius-lg)] p-6"
          >
            <h3
              className="mb-4 text-[13px] font-medium flex items-center gap-2"
              style={{ color: "var(--text-primary)" }}
            >
              <Globe className="h-4 w-4" style={{ color: "var(--accent)" }} />
              Brand website & billedsøgning
            </h3>

            <div className="space-y-4">
              <div>
                <label
                  className="mb-1.5 block text-[13px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Website URL
                </label>
                <input
                  type="url"
                  value={editForm.website_url}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, website_url: e.target.value }))
                  }
                  placeholder="https://www.brand.com"
                  className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
                  style={{
                    background: "var(--bg-primary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-primary)",
                    outline: "none",
                  }}
                />
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  Bruges til at finde produktbilleder via Google site-søgning
                </p>
              </div>

              <div>
                <label
                  className="mb-1.5 block text-[13px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  Søge-URL mønster
                  <span className="ml-1 font-normal" style={{ color: "var(--text-tertiary)" }}>
                    (valgfrit)
                  </span>
                </label>
                <input
                  type="url"
                  value={editForm.search_url_pattern}
                  onChange={(e) =>
                    setEditForm((f) => ({ ...f, search_url_pattern: e.target.value }))
                  }
                  placeholder="https://www.brand.com/search?q={sku}"
                  className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px] font-mono"
                  style={{
                    background: "var(--bg-primary)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-primary)",
                    outline: "none",
                  }}
                />
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  {"{sku}"} erstattes med produktets artikelnummer. Øger billedsøgningens præcision.
                </p>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-between">
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 rounded-[var(--radius-md)] px-3 py-2 text-[13px] font-medium transition-colors disabled:opacity-50"
              style={{ color: "var(--danger)" }}
            >
              {deleting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Fjern brand
            </button>
            <div className="flex gap-3">
              <Button variant="secondary" onClick={() => setEditingBrand(null)}>
                Annuller
              </Button>
              <Button onClick={handleSave} loading={saving}>
                Gem ændringer
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ─── Main list view ─── */

  return (
    <div>
      {/* Header */}
      <div className="mb-2">
        <Link
          href="/dashboard/settings"
          className="flex items-center gap-1.5 text-[13px] transition-colors"
          style={{ color: "var(--text-tertiary)" }}
        >
          <ChevronLeft className="h-4 w-4" />
          Indstillinger
        </Link>
      </div>

      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Brands
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Administrer de brands du arbejder med og deres image banks
          </p>
        </div>
        <Button
          onClick={() => {
            setShowAddPanel(true);
            setTimeout(() => addSearchRef.current?.focus(), 100);
          }}
          size="sm"
        >
          <Plus className="h-4 w-4" />
          Tilføj brand
        </Button>
      </div>

      {/* Stats */}
      {stats && stats.total_brands > 0 && (
        <div className="mb-6 grid grid-cols-3 gap-3">
          {[
            { label: "Brands total", value: stats.total_brands.toString() },
            { label: "Med image bank", value: stats.with_image_bank.toString(), accent: true },
            { label: "Uden image bank", value: stats.without_image_bank.toString() },
          ].map((kpi) => (
            <div
              key={kpi.label}
              className="card rounded-[var(--radius-md)] px-4 py-3"
            >
              <p
                className="text-[20px] font-medium"
                style={{ color: kpi.accent ? "var(--accent)" : "var(--text-primary)" }}
              >
                {kpi.value}
              </p>
              <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                {kpi.label}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Add brand panel */}
      {showAddPanel && (
        <div
          className="mb-6 rounded-[var(--radius-lg)] p-5"
          style={{
            background: "var(--accent-light)",
            border: "1px solid var(--accent)",
          }}
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
              Tilføj brand
            </h3>
            <button
              onClick={() => setShowAddPanel(false)}
              className="rounded-[var(--radius-md)] p-1"
              style={{ color: "var(--text-tertiary)" }}
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="relative mb-3">
            <Search
              className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
              style={{ color: "var(--text-tertiary)" }}
            />
            <input
              ref={addSearchRef}
              type="text"
              value={addSearch}
              onChange={(e) => setAddSearch(e.target.value)}
              placeholder="Søg efter brand..."
              className="w-full rounded-[var(--radius-md)] py-2.5 pl-9 pr-3 text-[13px]"
              style={{
                background: "var(--bg-primary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-primary)",
                outline: "none",
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && addSearch.trim()) {
                  handleAddCustomBrand();
                }
              }}
            />
          </div>

          {loadingSuggestions ? (
            <div className="flex items-center justify-center py-4">
              <Loader2
                className="h-4 w-4 animate-spin"
                style={{ color: "var(--text-tertiary)" }}
              />
            </div>
          ) : (
            <div className="max-h-64 space-y-1 overflow-y-auto">
              {suggestions.map((s) => (
                <button
                  key={s.slug}
                  onClick={() => handleAddBrand(s)}
                  disabled={s.already_added || adding === s.slug}
                  className="flex w-full items-center justify-between rounded-[var(--radius-md)] px-3 py-2.5 text-left text-[13px] transition-colors"
                  style={{
                    background: s.already_added ? "var(--bg-secondary)" : "var(--bg-primary)",
                    color: s.already_added ? "var(--text-tertiary)" : "var(--text-secondary)",
                    cursor: s.already_added ? "default" : "pointer",
                  }}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[11px] font-bold"
                      style={{
                        background: "var(--bg-tertiary)",
                        color: "var(--text-tertiary)",
                      }}
                    >
                      {s.name.charAt(0)}
                    </div>
                    <div>
                      <span className="font-medium">{s.name}</span>
                      {s.website && (
                        <span className="ml-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                          {new URL(s.website).hostname.replace("www.", "")}
                        </span>
                      )}
                    </div>
                  </div>
                  {s.already_added ? (
                    <span
                      className="flex items-center gap-1 text-[11px]"
                      style={{ color: "var(--accent)" }}
                    >
                      <Check className="h-3 w-3" />
                      Tilføjet
                    </span>
                  ) : adding === s.slug ? (
                    <Loader2
                      className="h-4 w-4 animate-spin"
                      style={{ color: "var(--accent)" }}
                    />
                  ) : (
                    <Plus className="h-4 w-4" style={{ color: "var(--text-tertiary)" }} />
                  )}
                </button>
              ))}

              {/* Custom brand option */}
              {addSearch.trim() &&
                !suggestions.some(
                  (s) => s.name.toLowerCase() === addSearch.toLowerCase()
                ) && (
                  <button
                    onClick={handleAddCustomBrand}
                    disabled={adding === "custom"}
                    className="flex w-full items-center justify-between rounded-[var(--radius-md)] px-3 py-2.5 text-left text-[13px] transition-colors border border-dashed"
                    style={{
                      background: "var(--bg-primary)",
                      color: "var(--text-secondary)",
                      borderColor: "var(--border-primary)",
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[11px] font-bold"
                        style={{
                          background: "var(--accent-light)",
                          color: "var(--accent)",
                        }}
                      >
                        +
                      </div>
                      <span>
                        Tilføj <strong>&ldquo;{addSearch.trim()}&rdquo;</strong> som
                        nyt brand
                      </span>
                    </div>
                    {adding === "custom" ? (
                      <Loader2
                        className="h-4 w-4 animate-spin"
                        style={{ color: "var(--accent)" }}
                      />
                    ) : (
                      <Plus className="h-4 w-4" style={{ color: "var(--text-tertiary)" }} />
                    )}
                  </button>
                )}
            </div>
          )}
        </div>
      )}

      {/* Search existing brands */}
      {brands.length > 5 && (
        <div className="relative mb-4">
          <Search
            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            style={{ color: "var(--text-tertiary)" }}
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filtrer brands..."
            className="w-full rounded-[var(--radius-md)] py-2.5 pl-9 pr-3 text-[13px]"
            style={{
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-primary)",
              outline: "none",
            }}
          />
        </div>
      )}

      {/* Brand list */}
      {filteredBrands.length === 0 ? (
        <div
          className="rounded-[var(--radius-lg)] border-2 border-dashed py-16 text-center"
          style={{
            borderColor: "var(--border-primary)",
            background: "var(--bg-primary)",
          }}
        >
          <Database className="mx-auto h-10 w-10" style={{ color: "var(--text-tertiary)" }} />
          <h3 className="mt-3 text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
            Ingen brands endnu
          </h3>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Tilføj de brands du arbejder med for at konfigurere image banks
          </p>
          <Button
            size="sm"
            className="mt-4"
            onClick={() => {
              setShowAddPanel(true);
              setTimeout(() => addSearchRef.current?.focus(), 100);
            }}
          >
            <Plus className="h-4 w-4" />
            Tilføj dit første brand
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredBrands.map((brand) => (
            <button
              key={brand.id}
              onClick={() => openEdit(brand)}
              className="card flex w-full items-center justify-between rounded-[var(--radius-lg)] px-5 py-4 text-left transition-all"
            >
              <div className="flex items-center gap-4">
                <div
                  className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)] text-[13px] font-bold"
                  style={{
                    background: "var(--bg-tertiary)",
                    color: "var(--text-tertiary)",
                  }}
                >
                  {brand.name.charAt(0)}
                </div>
                <div>
                  <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                    {brand.name}
                  </p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {brand.website_url && (
                      <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                        {(() => {
                          try {
                            return new URL(brand.website_url).hostname.replace("www.", "");
                          } catch {
                            return brand.website_url;
                          }
                        })()}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <span
                  className="rounded-[var(--radius-full)] px-2.5 py-1 text-[11px] font-medium"
                  style={{
                    background: "var(--bg-tertiary)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {brand.markup || 2.5}×
                </span>
                {brand.image_bank_search_pattern || brand.image_bank_url ? (
                  <span
                    className="flex items-center gap-1.5 rounded-[var(--radius-full)] px-2.5 py-1 text-[11px] font-medium"
                    style={{
                      background: brand.image_bank_search_pattern
                        ? "var(--success-light)"
                        : "var(--accent-light)",
                      color: brand.image_bank_search_pattern
                        ? "var(--success)"
                        : "var(--accent-text)",
                    }}
                  >
                    <ImageIcon className="h-3 w-3" />
                    {brand.image_bank_search_pattern
                      ? "Image bank aktiv"
                      : brand.image_bank_type
                        ? IMAGE_BANK_TYPES.find((t) => t.value === brand.image_bank_type)?.label || "Image bank"
                        : "Image bank"}
                  </span>
                ) : (
                  <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    Ingen image bank
                  </span>
                )}
                <ChevronLeft
                  className="h-4 w-4 rotate-180"
                  style={{ color: "var(--text-tertiary)" }}
                />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

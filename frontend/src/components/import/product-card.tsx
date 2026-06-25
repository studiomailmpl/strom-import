"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Check,
  X,
  Pencil,
  ImageIcon,
  Package,
  HardDrive,
  AlertTriangle,
  ExternalLink,
  Copy,
  BarChart3,
  TrendingUp,
} from "lucide-react";
import { sanitizeHtml } from "@/lib/sanitize";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ImageManager } from "./image-manager";

export interface ProductVariant {
  size: string;
  quantity: number;
  ean?: string;
}

export interface UploadedImage {
  id: string;
  filename: string;
  url: string;
  source: "uploaded";
  sort_order: number;
}

export interface SEOKeyword {
  keyword: string;
  search_volume: number;
  keyword_difficulty: number;
  cpc: number;
  competition: number;
  source?: string;
}

export interface ImportProduct {
  id: string;
  title: string;
  vendor: string;
  product_type: string;
  description_da: string;
  description_en?: string;
  style_code: string;
  color: string;
  cost_price_eur: number | null;
  gross_price_eur: number | null;
  discount_pct: number;
  retail_price_dkk: number | null;
  variants: ProductVariant[];
  images: string[];
  uploaded_images: UploadedImage[];
  status: "pending" | "approved" | "skipped" | "pushed" | "error";
  image_bank_direct_url?: string | null;
  is_restock: boolean;
  shopify_match_id: string | null;
  shopify_match_title: string | null;
  duplicate_of_import_id: string | null;
  duplicate_import_date: string | null;
  seo_keywords?: (SEOKeyword | string)[];
  qa_warnings?: QAWarning[];
}

export interface QAWarning {
  level: "error" | "warning" | "info";
  code: string;
  field: string;
  message: string;
}

interface ProductCardProps {
  product: ImportProduct;
  onApprove: (id: string) => void;
  onSkip: (id: string) => void;
  onUpdate: (id: string, data: Partial<ImportProduct>) => void;
  onToggleRestock?: (productId: string) => void;
}

function WarningTooltip({ warnings, hasErrors }: { warnings: string[]; hasErrors?: boolean }) {
  const [show, setShow] = useState(false);
  const borderColor = hasErrors ? "var(--error, #ef4444)" : "var(--warning)";
  const bgColor = hasErrors ? "var(--error-light, #fef2f2)" : "var(--warning-light)";
  const textColor = hasErrors ? "var(--error-text, #991b1b)" : "var(--warning-text)";
  const iconColor = hasErrors ? "var(--error, #ef4444)" : "var(--warning)";

  return (
    <div
      className="relative"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <AlertTriangle className="h-4 w-4 cursor-help" style={{ color: iconColor }} />
      {show && (
        <div
          className="absolute left-1/2 -translate-x-1/2 top-full mt-1.5 z-50 w-72 rounded-[var(--radius-md)] px-3 py-2"
          style={{
            background: bgColor,
            border: `1px solid ${borderColor}`,
            boxShadow: "var(--shadow-md)",
          }}
        >
          <p className="text-[11px] font-medium mb-1" style={{ color: textColor }}>
            {hasErrors ? "Fejl & advarsler" : "Advarsler"}
          </p>
          {warnings.map((w, i) => (
            <p key={i} className="text-[11px] leading-relaxed" style={{ color: textColor }}>
              • {w}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export function ProductCard({
  product,
  onApprove,
  onSkip,
  onUpdate,
  onToggleRestock,
}: ProductCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editData, setEditData] = useState({
    title: product.title,
    retail_price_dkk: product.retail_price_dkk,
    description_da: product.description_da,
  });
  const [uploadedImages, setUploadedImages] = useState<UploadedImage[]>(
    product.uploaded_images || []
  );

  const totalQty = product.variants?.reduce((sum, v) => sum + v.quantity, 0) || 0;
  const hasUploaded = uploadedImages.length > 0;
  const imageCount = hasUploaded ? uploadedImages.length : (product.images?.length || 0);

  const warnings: string[] = [];
  if (imageCount === 0) {
    warnings.push("Ingen billeder — produktet vil blive oprettet uden billeder i Shopify");
  }
  if (totalQty === 0) {
    warnings.push("Ingen stk. registreret — alle varianter har antal 0");
  }
  if (!product.cost_price_eur && product.cost_price_eur !== 0) {
    warnings.push("Kostpris mangler");
  }
  if (!product.retail_price_dkk && product.retail_price_dkk !== 0) {
    warnings.push("Udsalgspris mangler");
  }
  // Add QA warnings from backend validation (error + warning level only, skip info)
  let hasQaErrors = false;
  const qaInfoMessages: string[] = [];
  if (product.qa_warnings) {
    for (const w of product.qa_warnings) {
      if (!w.message) continue;
      if (w.level === "error") {
        hasQaErrors = true;
        if (!warnings.includes(w.message)) warnings.push(w.message);
      } else if (w.level === "warning") {
        if (!warnings.includes(w.message)) warnings.push(w.message);
      } else if (w.level === "info") {
        qaInfoMessages.push(w.message);
      }
    }
  }
  const hasWarnings = warnings.length > 0 && product.status === "pending";

  const statusBadge = {
    pending: { label: "Afventer", variant: "outline" as const },
    approved: { label: "Godkendt", variant: "success" as const },
    skipped: { label: "Sprunget over", variant: "warning" as const },
    pushed: { label: "Sendt", variant: "success" as const },
    error: { label: "Fejl", variant: "error" as const },
  };

  const badge = statusBadge[product.status];

  const handleSaveEdit = () => {
    onUpdate(product.id, editData);
    setEditing(false);
  };

  return (
    <div
      className="card rounded-[var(--radius-lg)] transition-all"
      style={{
        borderLeft: product.is_restock
          ? "4px solid var(--info)"
          : undefined,
        boxShadow: product.status === "approved"
          ? "0 0 0 1px var(--success), 0 1px 2px rgba(0,0,0,0.07)"
          : undefined,
        opacity: product.status === "skipped" ? 0.6 : 1,
      }}
    >
      {/* Main row */}
      <div className="flex items-center gap-4 p-4">
        {/* Thumbnail */}
        <div
          className="relative h-16 w-16 flex-shrink-0 overflow-hidden rounded-[var(--radius-md)]"
          style={{ background: "var(--bg-tertiary)" }}
        >
          {imageCount > 0 ? (
            <img
              src={hasUploaded ? uploadedImages[0].url : product.images[0]}
              alt={product.title}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <Package className="h-6 w-6" style={{ color: "var(--text-tertiary)" }} />
            </div>
          )}
          {imageCount > 1 && (
            <span className="absolute bottom-0.5 right-0.5 rounded-sm bg-black/60 px-1 py-0.5 text-[10px] font-medium text-white">
              +{imageCount - 1}
            </span>
          )}
          {hasUploaded && (
            <span
              className="absolute left-0.5 top-0.5 rounded-sm p-0.5"
              style={{ background: "rgba(15, 110, 86, 0.8)" }}
            >
              <HardDrive className="h-2.5 w-2.5 text-white" />
            </span>
          )}
        </div>

        {/* Product info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3
              className="truncate text-[13px] font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {product.title}
            </h3>
            {product.is_restock && (
              <span
                className="inline-flex items-center rounded-[var(--radius-full)] px-2 py-0.5 text-[10px] font-medium"
                style={{ background: "var(--info-light)", color: "var(--info-text)" }}
              >
                Supplering
              </span>
            )}
            <Badge variant={badge.variant}>{badge.label}</Badge>
            {hasWarnings && <WarningTooltip warnings={warnings} hasErrors={hasQaErrors} />}
          </div>
          {product.is_restock && product.shopify_match_title && (
            <p className="mt-0.5 text-[11px]" style={{ color: "var(--info-text)" }}>
              Supplerer: {product.shopify_match_title}
            </p>
          )}
          <div
            className="mt-1 flex items-center gap-3 text-[11px]"
            style={{ color: "var(--text-tertiary)" }}
          >
            <span>{product.vendor}</span>
            <span style={{ color: "var(--border-primary)" }}>·</span>
            <span>{product.product_type}</span>
            <span style={{ color: "var(--border-primary)" }}>·</span>
            <span>{product.color}</span>
            <span style={{ color: "var(--border-primary)" }}>·</span>
            <span>{product.style_code}</span>
          </div>
          {product.is_restock && onToggleRestock && (
            <button
              onClick={() => onToggleRestock(product.id)}
              className="mt-1 text-[11px] underline"
              style={{ color: "var(--text-tertiary)" }}
            >
              Opret som ny i stedet
            </button>
          )}
        </div>

        {/* Pricing + qty */}
        <div className="flex items-center gap-6 text-right">
          <div>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Kostpris
            </p>
            <div className="flex items-center gap-1.5 justify-end">
              {product.discount_pct > 0 && product.gross_price_eur ? (
                <>
                  <span
                    className="text-[11px] line-through"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    €{product.gross_price_eur.toFixed(2)}
                  </span>
                  <span
                    className="text-[13px] font-medium"
                    style={{ color: "var(--success-text)" }}
                  >
                    €{product.cost_price_eur?.toFixed(2)}
                  </span>
                </>
              ) : (
                <span
                  className="text-[13px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  €{product.cost_price_eur?.toFixed(2) || "—"}
                </span>
              )}
            </div>
            {product.discount_pct > 0 && (
              <span
                className="inline-flex items-center rounded-[var(--radius-full)] px-1.5 py-0.5 text-[10px] font-medium mt-0.5"
                style={{ background: "var(--success-light)", color: "var(--success-text)" }}
              >
                -{product.discount_pct}%
              </span>
            )}
          </div>
          <div>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Udsalgspris
            </p>
            <p
              className="text-[13px] font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {product.retail_price_dkk
                ? `${product.retail_price_dkk.toLocaleString("da-DK")} kr`
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Antal
            </p>
            <p
              className="text-[13px] font-medium"
              style={{ color: "var(--text-secondary)" }}
            >
              {totalQty}
            </p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5">
          {product.status === "pending" && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onApprove(product.id)}
                style={{ color: "var(--success)" }}
              >
                <Check className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onSkip(product.id)}
                style={{ color: "var(--text-tertiary)" }}
              >
                <X className="h-4 w-4" />
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          className="px-4 pb-4 pt-3"
          style={{ borderTop: "1px solid var(--border-secondary)" }}
        >
          <div className="grid gap-6 lg:grid-cols-3">
            {/* Images */}
            <div>
              {product.image_bank_direct_url && (
                <a
                  href={product.image_bank_direct_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => {
                    navigator.clipboard.writeText(product.style_code).catch(() => {});
                  }}
                  className="mb-3 flex items-center gap-2 rounded-[var(--radius-md)] px-3 py-2 text-[12px] font-medium transition-colors"
                  style={{
                    background: "var(--accent-light)",
                    color: "var(--accent-text)",
                    border: "1px solid var(--accent)",
                  }}
                >
                  <ImageIcon className="h-3.5 w-3.5 flex-shrink-0" />
                  <span className="flex-1">Hent fra image bank</span>
                  <span
                    className="flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[10px]"
                    style={{ background: "var(--bg-primary)", color: "var(--text-tertiary)" }}
                  >
                    <Copy className="h-2.5 w-2.5" />
                    {product.style_code}
                  </span>
                  <ExternalLink className="h-3 w-3 flex-shrink-0" style={{ opacity: 0.6 }} />
                </a>
              )}
              <ImageManager
                productId={product.id}
                scrapedImages={product.images || []}
                uploadedImages={uploadedImages}
                onUploadedImagesChange={setUploadedImages}
                readOnly={product.status === "pushed"}
              />
            </div>

            {/* Variants + SEO */}
            <div>
              <h4
                className="mb-2 text-[11px] font-medium uppercase tracking-[0.05em]"
                style={{ color: "var(--text-tertiary)" }}
              >
                Varianter ({product.variants?.length || 0})
              </h4>
              <div className="space-y-1">
                {product.variants?.map((v, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between rounded-[var(--radius-md)] px-3 py-1.5"
                    style={{ background: "var(--bg-secondary)" }}
                  >
                    <span className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
                      {v.size}
                    </span>
                    <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
                      ×{v.quantity}
                    </span>
                  </div>
                ))}
              </div>

              {/* SEO Keywords */}
              {product.seo_keywords && product.seo_keywords.length > 0 && (
                <div className="mt-4">
                  <h4
                    className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.05em]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    <BarChart3 className="h-3 w-3" />
                    SEO Keywords
                  </h4>
                  <div className="space-y-1">
                    {product.seo_keywords.map((kw, i) => {
                      const isEnriched = typeof kw === "object";
                      const keyword = isEnriched ? kw.keyword : kw;
                      const volume = isEnriched ? kw.search_volume : 0;
                      const difficulty = isEnriched ? kw.keyword_difficulty : 0;
                      const isDiscovered = isEnriched && kw.source === "discovered";

                      return (
                        <div
                          key={i}
                          className="rounded-[var(--radius-md)] px-3 py-1.5"
                          style={{ background: "var(--bg-secondary)" }}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>
                              {keyword}
                            </span>
                            {isDiscovered && (
                              <span
                                className="flex items-center gap-0.5 text-[9px] font-medium rounded-[var(--radius-sm)] px-1.5 py-0.5"
                                style={{ background: "var(--accent-light)", color: "var(--accent)" }}
                              >
                                <TrendingUp className="h-2 w-2" />
                                fundet
                              </span>
                            )}
                          </div>
                          {isEnriched && volume > 0 && (
                            <div className="flex items-center gap-3 mt-0.5">
                              <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                                {volume.toLocaleString("da-DK")} søg/md
                              </span>
                              <span
                                className="text-[10px] font-medium"
                                style={{
                                  color: difficulty <= 30
                                    ? "var(--success)"
                                    : difficulty <= 60
                                    ? "var(--warning)"
                                    : "var(--danger)",
                                }}
                              >
                                KD {difficulty}
                              </span>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* Description + Edit */}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <h4
                  className="text-[11px] font-medium uppercase tracking-[0.05em]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  Beskrivelse
                </h4>
                {!editing && product.status !== "pushed" && (
                  <button
                    onClick={() => setEditing(true)}
                    className="flex items-center gap-1 text-[11px]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    <Pencil className="h-3 w-3" />
                    Rediger
                  </button>
                )}
              </div>
              {editing ? (
                <div className="space-y-3">
                  <div>
                    <label
                      className="mb-1 block text-[11px]"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      Titel
                    </label>
                    <input
                      value={editData.title}
                      onChange={(e) =>
                        setEditData({ ...editData, title: e.target.value })
                      }
                      className="w-full rounded-[var(--radius-md)] px-2.5 py-1.5 text-[13px]"
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
                      className="mb-1 block text-[11px]"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      Pris (DKK)
                    </label>
                    <input
                      type="number"
                      value={editData.retail_price_dkk || ""}
                      onChange={(e) =>
                        setEditData({
                          ...editData,
                          retail_price_dkk: parseFloat(e.target.value) || null,
                        })
                      }
                      className="w-full rounded-[var(--radius-md)] px-2.5 py-1.5 text-[13px]"
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
                      className="mb-1 block text-[11px]"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      Beskrivelse
                    </label>
                    <textarea
                      value={editData.description_da}
                      onChange={(e) =>
                        setEditData({
                          ...editData,
                          description_da: e.target.value,
                        })
                      }
                      rows={4}
                      className="w-full rounded-[var(--radius-md)] px-2.5 py-1.5 text-[13px] resize-none"
                      style={{
                        background: "var(--bg-primary)",
                        color: "var(--text-primary)",
                        border: "1px solid var(--border-primary)",
                        outline: "none",
                      }}
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={handleSaveEdit}>
                      Gem
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditing(false)}
                    >
                      Annuller
                    </Button>
                  </div>
                </div>
              ) : (
                <div
                  className="prose prose-sm max-w-none text-[13px]"
                  style={{ color: "var(--text-secondary)" }}
                  dangerouslySetInnerHTML={{
                    __html: sanitizeHtml(product.description_da || "<em>Ingen beskrivelse</em>"),
                  }}
                />
              )}

              {/* English description (collapsed, expandable) */}
              {product.description_en && (
                <details className="mt-3">
                  <summary
                    className="cursor-pointer text-[11px] font-medium uppercase tracking-[0.05em]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    English description
                  </summary>
                  <div
                    className="prose prose-sm max-w-none mt-1.5 text-[13px]"
                    style={{ color: "var(--text-tertiary)" }}
                    dangerouslySetInnerHTML={{
                      __html: sanitizeHtml(product.description_en),
                    }}
                  />
                </details>
              )}

              {/* QA Info notes (only visible in expanded view) */}
              {qaInfoMessages.length > 0 && product.status === "pending" && (
                <div className="mt-4">
                  <h4
                    className="mb-2 text-[11px] font-medium uppercase tracking-[0.05em]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    QA-noter ({qaInfoMessages.length})
                  </h4>
                  <div className="space-y-1">
                    {qaInfoMessages.map((msg, i) => (
                      <p
                        key={i}
                        className="rounded-[var(--radius-md)] px-3 py-1.5 text-[12px]"
                        style={{ background: "var(--bg-secondary)", color: "var(--text-tertiary)" }}
                      >
                        {msg}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

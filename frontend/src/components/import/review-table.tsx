"use client";

import { useState, useMemo, Fragment, type ReactNode } from "react";
import {
  Check,
  AlertTriangle,
  XCircle,
  Package,
  FileCheck2,
  Link2,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ProductCard } from "./product-card";
import type { ImportProduct, ProductVariant } from "./product-card";

/* ─── Order confirmation match ─── */

/**
 * A match this strong is treated as verified; below it the confirmation was
 * matched on a weaker signal (a fuzzy title, a colour code) and is worth a
 * human glance before the data is trusted.
 */
const CONFIDENT_MATCH = 90;

type MatchState = "matched" | "uncertain" | "unmatched";

function matchState(product: ImportProduct): MatchState {
  if (!product.order_confirmation_line_id) return "unmatched";
  return (product.match_confidence ?? 0) >= CONFIDENT_MATCH ? "matched" : "uncertain";
}

/** Human labels for the merge policy's source values. */
const SOURCE_LABELS: Record<string, string> = {
  order_confirmation: "Ordrebekræftelse",
  invoice: "Faktura",
  web: "Web",
  manual: "Manuelt",
};

const FIELD_LABELS: Record<string, string> = {
  style_code: "Varenummer",
  title: "Titel",
  color_code: "Farvekode",
  color_original: "Farve (original)",
  size_range: "Størrelser",
  quantity: "Antal",
  cost_price_eur: "Kostpris",
  rrp: "Vejl. udsalgspris",
  images: "Billeder",
  description_da: "Beskrivelse",
};

/** Tooltip listing which source won each field. */
function DataSourceTooltip({ sources }: { sources: Record<string, string> }) {
  const [show, setShow] = useState(false);
  const entries = Object.entries(sources);
  if (entries.length === 0) return null;

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <FileCheck2
        className="h-3.5 w-3.5 cursor-help"
        style={{ color: "var(--text-tertiary)" }}
      />
      {show && (
        <span
          className="absolute right-0 top-full mt-1.5 z-50 w-56 rounded-[var(--radius-md)] px-3 py-2"
          style={{
            background: "var(--bg-primary)",
            border: "1px solid var(--border-primary)",
            boxShadow: "var(--shadow-md)",
          }}
        >
          <span
            className="mb-1 block text-[10px] font-medium uppercase tracking-[0.05em]"
            style={{ color: "var(--text-tertiary)" }}
          >
            Datakilde pr. felt
          </span>
          {entries.map(([field, source]) => (
            <span key={field} className="flex justify-between gap-2 text-[11px]">
              <span style={{ color: "var(--text-secondary)" }}>
                {FIELD_LABELS[field] || field}
              </span>
              <span style={{ color: "var(--text-primary)" }}>
                {SOURCE_LABELS[source] || source}
              </span>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

/* ─── Tooltip ─── */

function StatusTooltip({ warnings, children }: { warnings: string[]; children: ReactNode }) {
  const [show, setShow] = useState(false);

  if (warnings.length === 0) return <>{children}</>;

  return (
    <div
      className="relative inline-block"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div
          className="absolute right-0 top-full mt-1.5 z-50 w-56 rounded-[var(--radius-md)] px-3 py-2"
          style={{
            background: "var(--warning-light)",
            border: "1px solid var(--warning)",
            boxShadow: "var(--shadow-md)",
          }}
        >
          {warnings.map((w, i) => (
            <p key={i} className="text-[11px] leading-relaxed" style={{ color: "var(--warning-text)" }}>
              • {w}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── SKU ─── */

/**
 * The SKU the Shopify push will actually write.
 *
 * Source of truth: backend/app/services/shopify_service.py
 *   sku = f"{style_code}-{var_size}" if style_code else var_size
 *
 * Keep this in step with that line — showing anything else here means the
 * reviewer approves one SKU and Shopify receives another.
 */
function buildVariantSku(styleCode: string, size: string): string {
  const code = (styleCode || "").trim();
  const variantSize = (size || "").trim();
  return code ? `${code}-${variantSize}` : variantSize;
}

/**
 * A product with no variants is not pushed as-is: shopify_service falls back to
 * a single "One Size" variant, so that is the size its SKU ends up carrying.
 */
const PUSH_FALLBACK_SIZE = "One Size";

/* ─── Types ─── */

interface FlatVariant {
  productId: string;
  productTitle: string;
  vendor: string;
  styleCode: string;
  color: string;
  size: string;
  sku: string;
  quantity: number;
  costPriceEur: number | null;
  retailPriceDkk: number | null;
  status: RowStatus;
  warnings: string[];
  product: ImportProduct;
}

type RowStatus = "OK" | "ADVARSEL" | "FEJL";

/* ─── Status computation ─── */

function computeRowStatus(product: ImportProduct, variant: ProductVariant): { status: RowStatus; warnings: string[] } {
  const warnings: string[] = [];

  if (!product.variants || product.variants.length === 0) {
    return { status: "FEJL", warnings: ["Ingen varianter"] };
  }
  if (!product.cost_price_eur && product.cost_price_eur !== 0) {
    return { status: "FEJL", warnings: ["Ingen pris"] };
  }
  if (!product.title) {
    return { status: "FEJL", warnings: ["Mangler titel"] };
  }

  if (product.duplicate_of_import_id) {
    warnings.push(`Importeret før (${product.duplicate_import_date || "ukendt dato"})`);
  }
  if (!product.images || product.images.length === 0) {
    warnings.push("Ingen billeder");
  }
  if (!product.description_da) {
    warnings.push("Ingen beskrivelse");
  }
  if (product.cost_price_eur && product.cost_price_eur > 500) {
    warnings.push("Uforventet pris");
  }
  if (variant.quantity === 0) {
    warnings.push("Antal er 0");
  }

  if (warnings.length > 0) {
    return { status: "ADVARSEL", warnings };
  }

  return { status: "OK", warnings: [] };
}

/* ─── Color map ─── */

const colorMap: Record<string, string> = {
  black: "#000000", sort: "#000000",
  white: "#ffffff", hvid: "#ffffff",
  red: "#ef4444", rød: "#ef4444",
  blue: "#3b82f6", blå: "#3b82f6",
  navy: "#1e3a5f",
  green: "#22c55e", grøn: "#22c55e",
  grey: "#9ca3af", gray: "#9ca3af", grå: "#9ca3af",
  brown: "#92400e", brun: "#92400e",
  beige: "#d4c5a9",
  pink: "#ec4899", rosa: "#ec4899",
  yellow: "#eab308", gul: "#eab308",
  orange: "#f97316",
  camel: "#c19a6b",
  cream: "#fffdd0", creme: "#fffdd0",
  tan: "#d2b48c",
  khaki: "#bdb76b",
  olive: "#808000", oliven: "#808000",
};

function getColorHex(colorName: string): string {
  const lower = colorName.toLowerCase().trim();
  for (const [key, hex] of Object.entries(colorMap)) {
    if (lower.includes(key)) return hex;
  }
  return "#9ca3af";
}

/* ─── Props ─── */

interface ReviewTableProps {
  products: ImportProduct[];
  selectedIds: Set<string>;
  onToggleSelect: (productId: string) => void;
  onToggleAll: () => void;
  readOnly?: boolean;
  /** Persist an edit to a product. When omitted the table stays read-only —
   *  rows are not expandable and no editor is mounted. */
  onUpdateProduct?: (productId: string, data: Partial<ImportProduct>) => void | Promise<void>;
  /** Let the user point an unmatched product at an order confirmation in Drive.
   *  Omit to hide the match column entirely. */
  onLinkOrderConfirmation?: (productId: string) => void;
  /** Show the match column even without a link handler — the history view has
   *  nothing to link, but still wants to show what was matched. */
  showMatchColumn?: boolean;
}

/* ─── Component ─── */

export function ReviewTable({
  products,
  selectedIds,
  onToggleSelect,
  onToggleAll,
  readOnly = false,
  onUpdateProduct,
  onLinkOrderConfirmation,
  showMatchColumn,
}: ReviewTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const canEdit = !readOnly && Boolean(onUpdateProduct);

  // Show the column when asked to, when a link handler exists, or whenever any
  // product actually carries match data — otherwise it is dead space.
  const withMatchColumn =
    showMatchColumn ??
    (Boolean(onLinkOrderConfirmation) ||
      products.some((p) => p.order_confirmation_line_id || p.data_sources));
  const flatVariants = useMemo(() => {
    const rows: FlatVariant[] = [];
    for (const product of products) {
      if (!product.variants || product.variants.length === 0) {
        const { status, warnings } = computeRowStatus(product, { size: "-", quantity: 0 });
        rows.push({
          productId: product.id,
          productTitle: product.title,
          vendor: product.vendor,
          styleCode: product.style_code,
          color: product.color,
          size: "-",
          sku: buildVariantSku(product.style_code, PUSH_FALLBACK_SIZE),
          quantity: 0,
          costPriceEur: product.cost_price_eur,
          retailPriceDkk: product.retail_price_dkk,
          status,
          warnings,
          product,
        });
      } else {
        for (const variant of product.variants) {
          const { status, warnings } = computeRowStatus(product, variant);
          rows.push({
            productId: product.id,
            productTitle: product.title,
            vendor: product.vendor,
            styleCode: product.style_code,
            color: product.color,
            size: variant.size,
            sku: buildVariantSku(product.style_code, variant.size),
            quantity: variant.quantity,
            costPriceEur: product.cost_price_eur,
            retailPriceDkk: product.retail_price_dkk,
            status,
            warnings,
            product,
          });
        }
      }
    }
    return rows;
  }, [products]);

  const allSelected = products.length > 0 && products.every((p) => selectedIds.has(p.id));
  const someSelected = products.some((p) => selectedIds.has(p.id)) && !allSelected;

  const statusBadge = (status: RowStatus, warnings: string[]) => {
    switch (status) {
      case "OK":
        return (
          <Badge variant="success" className="gap-1">
            <Check className="h-3 w-3" />
            OK
          </Badge>
        );
      case "ADVARSEL":
        return (
          <StatusTooltip warnings={warnings}>
            <Badge variant="warning" className="gap-1 cursor-help">
              <AlertTriangle className="h-3 w-3" />
              ADVARSEL
            </Badge>
          </StatusTooltip>
        );
      case "FEJL":
        return (
          <StatusTooltip warnings={warnings}>
            <Badge variant="error" className="gap-1 cursor-help">
              <XCircle className="h-3 w-3" />
              FEJL
            </Badge>
          </StatusTooltip>
        );
    }
  };

  const matchBadge = (product: ImportProduct) => {
    const state = matchState(product);
    const confidence = product.match_confidence ?? 0;
    const sources = product.data_sources || {};

    if (state === "unmatched") {
      return (
        <div className="flex items-center gap-1.5">
          <Badge variant="error" className="gap-1">
            <XCircle className="h-3 w-3" />
            Ingen match
          </Badge>
          {!readOnly && onLinkOrderConfirmation && (
            <button
              onClick={(e) => {
                // The row itself toggles the editor when editing is enabled,
                // so without this the Link button would expand the row instead.
                e.stopPropagation();
                onLinkOrderConfirmation(product.id);
              }}
              title="Peg på den rigtige ordrebekræftelse i Drive"
              className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] px-1.5 py-1 text-[11px] transition-colors"
              style={{
                color: "var(--text-secondary)",
                border: "1px solid var(--border-primary)",
              }}
            >
              <Link2 className="h-3 w-3" />
              Link
            </button>
          )}
        </div>
      );
    }

    return (
      <div className="flex items-center gap-1.5">
        {state === "matched" ? (
          <Badge variant="success" className="gap-1">
            <Check className="h-3 w-3" />
            Matchet {confidence}%
          </Badge>
        ) : (
          <Badge variant="warning" className="gap-1">
            <AlertTriangle className="h-3 w-3" />
            Usikkert {confidence}%
          </Badge>
        )}
        <DataSourceTooltip sources={sources} />
      </div>
    );
  };

  if (products.length === 0) {
    return (
      <div
        className="rounded-[var(--radius-lg)] border-2 border-dashed p-16 text-center"
        style={{
          borderColor: "var(--border-primary)",
          background: "var(--bg-primary)",
        }}
      >
        <Package className="mx-auto h-8 w-8" style={{ color: "var(--text-tertiary)" }} />
        <p className="mt-3 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Ingen produkter fundet
        </p>
      </div>
    );
  }

  let lastProductId = "";
  // Width of the expanded editor row. Counts the seven data columns, the
  // checkbox when the table is editable, and the match column when shown —
  // get this wrong and the editor row is narrower or wider than the table.
  const colCount = 7 + (readOnly ? 0 : 1) + (withMatchColumn ? 1 : 0);

  const rowBackground = (isSelected: boolean, isExpanded: boolean) => {
    if (isExpanded) return "var(--bg-secondary)";
    if (isSelected && !readOnly) return "var(--accent-light)";
    return "transparent";
  };

  return (
    <div className="card overflow-x-auto rounded-[var(--radius-lg)]">
      <table className="w-full text-[13px]">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border-secondary)", background: "var(--bg-secondary)" }}>
            {!readOnly && (
              <th className="w-10 px-4 py-3">
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = someSelected;
                  }}
                  onChange={onToggleAll}
                  className="h-4 w-4 rounded"
                  style={{ accentColor: "var(--accent)" }}
                />
              </th>
            )}
            <th
              className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              Produkt / SKU
            </th>
            <th
              className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              Farve
            </th>
            <th
              className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              Str
            </th>
            <th
              className="px-4 py-3 text-right text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              Antal
            </th>
            <th
              className="px-4 py-3 text-right text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              EUR
            </th>
            <th
              className="px-4 py-3 text-right text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              DKK
            </th>
            <th
              className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-[0.05em]"
              style={{ color: "var(--text-tertiary)" }}
            >
              Status
            </th>
            {withMatchColumn && (
              <th
                className="px-4 py-3 text-left text-[11px] font-medium uppercase tracking-[0.05em]"
                style={{ color: "var(--text-tertiary)" }}
              >
                Ordrebekræftelse
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {flatVariants.map((row, idx) => {
            const showTitle = row.productId !== lastProductId;
            lastProductId = row.productId;
            const isSelected = selectedIds.has(row.productId);
            const isExpanded = expandedId === row.productId;
            // A product spans one row per variant — hang the editor off the last of them.
            const isLastRowOfProduct =
              idx === flatVariants.length - 1 ||
              flatVariants[idx + 1].productId !== row.productId;

            const sku = row.sku;

            return (
              <Fragment key={`${row.productId}-${row.size}-${idx}`}>
              <tr
                className="transition-colors"
                style={{
                  borderBottom: "1px solid var(--border-secondary)",
                  background: rowBackground(isSelected, isExpanded),
                  cursor: canEdit ? "pointer" : undefined,
                  ...(showTitle && idx > 0
                    ? { borderTop: "1px solid var(--border-primary)" }
                    : {}),
                }}
                onClick={canEdit ? () => setExpandedId(isExpanded ? null : row.productId) : undefined}
                onMouseEnter={(e) => {
                  if (!isExpanded && (!isSelected || readOnly)) {
                    e.currentTarget.style.background = "var(--bg-secondary)";
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = rowBackground(isSelected, isExpanded);
                }}
              >
                {!readOnly && (
                  <td className="px-4 py-3">
                    {showTitle && (
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => onToggleSelect(row.productId)}
                        onClick={(e) => e.stopPropagation()}
                        className="h-4 w-4 rounded"
                        style={{ accentColor: "var(--accent)" }}
                      />
                    )}
                  </td>
                )}
                <td className="px-4 py-3">
                  <div className="flex items-start gap-1.5">
                    {canEdit && (
                      <span className="mt-0.5 flex-shrink-0" style={{ color: "var(--text-tertiary)" }}>
                        {showTitle ? (
                          isExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )
                        ) : (
                          <span className="inline-block h-3.5 w-3.5" />
                        )}
                      </span>
                    )}
                    {showTitle ? (
                      <div>
                        <p className="font-medium" style={{ color: "var(--text-primary)" }}>
                          {row.productTitle}
                        </p>
                        <p
                          className="mt-0.5 font-mono text-[11px]"
                          style={{ color: "var(--text-tertiary)" }}
                        >
                          {sku}
                        </p>
                      </div>
                    ) : (
                      <p
                        className="font-mono text-[11px]"
                        style={{ color: "var(--text-tertiary)" }}
                      >
                        {sku}
                      </p>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span
                      className="inline-block h-3 w-3 rounded-full"
                      style={{
                        backgroundColor: getColorHex(row.color),
                        border: "1px solid var(--border-primary)",
                      }}
                    />
                    <span style={{ color: "var(--text-secondary)" }}>
                      {row.color || "-"}
                    </span>
                  </div>
                </td>
                <td className="px-4 py-3" style={{ color: "var(--text-secondary)" }}>
                  {row.size}
                </td>
                <td className="px-4 py-3 text-right" style={{ color: "var(--text-secondary)" }}>
                  {row.quantity}
                </td>
                <td className="px-4 py-3 text-right" style={{ color: "var(--text-secondary)" }}>
                  {row.costPriceEur != null
                    ? `\u20AC ${row.costPriceEur.toFixed(2).replace(".", ",")}`
                    : "\u2014"}
                </td>
                <td
                  className="px-4 py-3 text-right font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {row.retailPriceDkk != null
                    ? `${row.retailPriceDkk.toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kr`
                    : "\u2014"}
                </td>
                <td className="px-4 py-3">{statusBadge(row.status, row.warnings)}</td>
                {withMatchColumn && (
                  <td className="px-4 py-3">
                    {/* One badge per product, not per variant row. */}
                    {showTitle && matchBadge(row.product)}
                  </td>
                )}
              </tr>
              {canEdit && isExpanded && isLastRowOfProduct && (
                <tr style={{ borderBottom: "1px solid var(--border-primary)" }}>
                  <td
                    colSpan={colCount}
                    className="p-4"
                    style={{ background: "var(--bg-secondary)" }}
                  >
                    <ProductCard
                      product={row.product}
                      defaultExpanded
                      onUpdate={(id, data) => onUpdateProduct?.(id, data)}
                      onApprove={(id) => onUpdateProduct?.(id, { status: "approved" })}
                      onSkip={(id) => onUpdateProduct?.(id, { status: "skipped" })}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

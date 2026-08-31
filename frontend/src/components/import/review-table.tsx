"use client";

import { useState, useMemo, type ReactNode } from "react";
import { Check, AlertTriangle, XCircle, Package } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ImportProduct, ProductVariant } from "./product-card";

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
}

/* ─── Component ─── */

export function ReviewTable({
  products,
  selectedIds,
  onToggleSelect,
  onToggleAll,
  readOnly = false,
}: ReviewTableProps) {
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
          </tr>
        </thead>
        <tbody>
          {flatVariants.map((row, idx) => {
            const showTitle = row.productId !== lastProductId;
            lastProductId = row.productId;
            const isSelected = selectedIds.has(row.productId);

            const sku = row.sku;

            return (
              <tr
                key={`${row.productId}-${row.size}-${idx}`}
                className="transition-colors"
                style={{
                  borderBottom: "1px solid var(--border-secondary)",
                  background: isSelected && !readOnly ? "var(--accent-light)" : "transparent",
                  ...(showTitle && idx > 0
                    ? { borderTop: "1px solid var(--border-primary)" }
                    : {}),
                }}
                onMouseEnter={(e) => {
                  if (!isSelected || readOnly) {
                    e.currentTarget.style.background = "var(--bg-secondary)";
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background =
                    isSelected && !readOnly ? "var(--accent-light)" : "transparent";
                }}
              >
                {!readOnly && (
                  <td className="px-4 py-3">
                    {showTitle && (
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => onToggleSelect(row.productId)}
                        className="h-4 w-4 rounded"
                        style={{ accentColor: "var(--accent)" }}
                      />
                    )}
                  </td>
                )}
                <td className="px-4 py-3">
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
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

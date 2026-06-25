"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  FlaskConical,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ReviewTable } from "@/components/import/review-table";
import type { ImportProduct } from "@/components/import/product-card";
import { formatDate } from "@/lib/utils";
import { toast } from "sonner";

interface ImportFileDetail {
  id: string;
  file_name: string;
  file_size_bytes: number | null;
  status: string;
  products_found: number | null;
  error_message: string | null;
  created_at: string | null;
}

interface ImportDetail {
  id: string;
  name: string;
  is_test: boolean;
  status: string;
  file_name: string;
  file_count: number;
  total_products: number;
  products_pushed: number;
  eur_rate: number;
  markup: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  files: ImportFileDetail[];
  products: ImportProduct[];
}

const statusLabels: Record<string, string> = {
  uploading: "Uploader",
  uploaded: "Uploadet",
  analysing: "Analyserer",
  review: "Gennemgå",
  pushing: "Sender til Shopify",
  completed: "Færdig",
  failed: "Fejlet",
};

export default function ImportDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { getToken } = useAuth();
  const [imp, setImp] = useState<ImportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPushing, setIsPushing] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const importId = params.id as string;

  const fetchImport = useCallback(async () => {
    try {
      const token = await getToken();
      const data = await apiFetch<ImportDetail>(
        `/api/v1/imports/${importId}`,
        { token: token || undefined }
      );
      setImp(data);
      // Select all products for review, or for completed imports with 0 pushed (re-push)
      const allowSelect = data.status === "review" ||
        (data.status === "completed" && data.products_pushed === 0 && data.products.length > 0);
      if (allowSelect) {
        setSelectedIds(new Set(data.products.map((p) => p.id)));
      }
    } catch (error) {
      console.error("Failed to fetch import:", error);
      toast.error("Kunne ikke hente import");
    } finally {
      setLoading(false);
    }
  }, [getToken, importId]);

  useEffect(() => {
    fetchImport();
  }, [fetchImport]);

  const handleToggleSelect = (productId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(productId)) next.delete(productId);
      else next.add(productId);
      return next;
    });
  };

  const handleToggleAll = () => {
    if (!imp) return;
    if (selectedIds.size === imp.products.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(imp.products.map((p) => p.id)));
    }
  };

  const handlePush = async () => {
    if (!imp) return;
    setIsPushing(true);
    try {
      const token = await getToken();
      await apiFetch(`/api/v1/shopify/push/${imp.id}`, {
        method: "POST",
        token: token || undefined,
        body: JSON.stringify({ product_ids: Array.from(selectedIds) }),
      });

      let attempts = 0;
      while (attempts < 120) {
        await new Promise((r) => setTimeout(r, 2000));
        const freshToken = await getToken();
        const updated = await apiFetch<ImportDetail>(
          `/api/v1/imports/${imp.id}`,
          { token: freshToken || undefined }
        );
        if (updated.status === "completed" || updated.status === "failed") {
          setImp(updated);
          if (updated.status === "completed") {
            toast.success("Produkter sendt til Shopify");
          } else {
            toast.error("Push fejlede");
          }
          break;
        }
        attempts++;
      }
      if (attempts >= 120) {
        toast.error("Timeout");
      }
    } catch (error) {
      console.error("Push error:", error);
      toast.error("Push til Shopify fejlede");
    } finally {
      setIsPushing(false);
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

  if (!imp) {
    return (
      <div className="py-20 text-center">
        <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Import ikke fundet
        </p>
        <Button
          variant="secondary"
          className="mt-4"
          onClick={() => router.push("/dashboard/history")}
        >
          <ArrowLeft className="h-4 w-4" />
          Tilbage til historik
        </Button>
      </div>
    );
  }

  const statusVariant =
    imp.status === "completed"
      ? "success"
      : imp.status === "failed"
        ? "error"
        : ("outline" as const);

  // Allow re-push if import is "completed" but nothing was actually pushed
  const canRePush = imp.status === "completed" && imp.products_pushed === 0 && imp.products.length > 0;
  const isReadOnly = (imp.status === "completed" && !canRePush) || imp.status === "pushed" || imp.status === "failed";

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => router.push("/dashboard/history")}
          className="mb-4 flex items-center gap-1 text-[13px] transition-colors"
          style={{ color: "var(--text-tertiary)" }}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Historik
        </button>

        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1
                className="text-[20px] font-medium tracking-tight"
                style={{ color: "var(--text-primary)" }}
              >
                {imp.name || imp.file_name}
              </h1>
              <Badge variant={statusVariant}>
                {imp.status === "completed" && (
                  <CheckCircle2 className="mr-1 h-3 w-3" />
                )}
                {imp.status === "failed" && (
                  <XCircle className="mr-1 h-3 w-3" />
                )}
                {statusLabels[imp.status] || imp.status}
              </Badge>
              {imp.is_test && (
                <Badge variant="warning">
                  <FlaskConical className="mr-1 h-3 w-3" />
                  Test
                </Badge>
              )}
            </div>
            <p className="mt-1 text-[13px]" style={{ color: "var(--text-tertiary)" }}>
              {formatDate(imp.created_at)} ·{" "}
              {imp.file_count > 1 ? `${imp.file_count} filer` : imp.file_name} ·{" "}
              {imp.total_products} produkter ·
              EUR {imp.eur_rate} · x{imp.markup} markup
            </p>
          </div>

          {(imp.status === "review" || canRePush) && (
            <Button onClick={handlePush} loading={isPushing} disabled={selectedIds.size === 0}>
              Push {selectedIds.size} til Shopify
            </Button>
          )}
        </div>

        {imp.error_message && (
          <div
            className="mt-4 rounded-[var(--radius-md)] px-4 py-3"
            style={{
              background: "var(--danger-light)",
              border: "1px solid var(--danger)",
            }}
          >
            <p className="text-[13px]" style={{ color: "var(--danger-text)" }}>
              {imp.error_message}
            </p>
          </div>
        )}

        {/* Stats for completed */}
        {imp.status === "completed" && (
          <div className="mt-4 grid grid-cols-3 gap-3">
            {[
              { label: "Produkter", value: imp.total_products.toString() },
              {
                label: "Sendt til Shopify",
                value: imp.products_pushed.toString(),
                accent: true,
              },
              {
                label: "Varianter",
                value: imp.products
                  .reduce((s, p) => s + (p.variants?.length || 0), 0)
                  .toString(),
              },
            ].map((kpi) => (
              <div
                key={kpi.label}
                className="card rounded-[var(--radius-md)] p-4"
              >
                <p
                  className="text-[11px] font-medium uppercase tracking-[0.05em]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  {kpi.label}
                </p>
                <p
                  className="mt-1 text-[20px] font-medium"
                  style={{ color: kpi.accent ? "var(--success)" : "var(--text-primary)" }}
                >
                  {kpi.value}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Products table */}
      {imp.products.length > 0 && (
        <ReviewTable
          products={imp.products}
          selectedIds={selectedIds}
          onToggleSelect={handleToggleSelect}
          onToggleAll={handleToggleAll}
          readOnly={isReadOnly}
        />
      )}
    </div>
  );
}

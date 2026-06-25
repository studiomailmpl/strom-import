"use client";

/**
 * Legacy ReviewPanel - kept for backward compatibility.
 * New code should use ReviewTable directly.
 */

import { useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ReviewTable } from "./review-table";
import type { ImportProduct } from "./product-card";

interface ReviewPanelProps {
  products: ImportProduct[];
  importId: string;
  onApprove: (id: string) => void;
  onSkip: (id: string) => void;
  onUpdate: (id: string, data: Partial<ImportProduct>) => void;
  onPush: () => void;
  isPushing: boolean;
  onToggleRestock?: (productId: string) => void;
}

export function ReviewPanel({
  products,
  importId,
  onApprove,
  onSkip,
  onUpdate,
  onPush,
  isPushing,
  onToggleRestock,
}: ReviewPanelProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    new Set(products.map((p) => p.id))
  );
  const [showPushConfirm, setShowPushConfirm] = useState(false);

  const handleToggleSelect = (productId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(productId)) next.delete(productId);
      else next.add(productId);
      return next;
    });
  };

  const handleToggleAll = () => {
    if (selectedIds.size === products.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(products.map((p) => p.id)));
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
          {selectedIds.size} af {products.length} produkter valgt
        </p>
        {selectedIds.size > 0 && (
          <Button onClick={() => setShowPushConfirm(true)} loading={isPushing}>
            <Upload className="h-4 w-4" />
            Send {selectedIds.size} til Shopify
          </Button>
        )}
      </div>

      <ReviewTable
        products={products}
        selectedIds={selectedIds}
        onToggleSelect={handleToggleSelect}
        onToggleAll={handleToggleAll}
      />

      <ConfirmDialog
        open={showPushConfirm}
        title="Send til Shopify"
        description={`Er du sikker på, at du vil sende ${selectedIds.size} produkter til Shopify?`}
        confirmLabel="Send til Shopify"
        onConfirm={() => {
          setShowPushConfirm(false);
          onPush();
        }}
        onCancel={() => setShowPushConfirm(false)}
      />
    </div>
  );
}

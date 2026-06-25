"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "default";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Bekræft",
  cancelLabel = "Annuller",
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="fixed inset-0 transition-opacity"
        style={{ background: "rgba(0,0,0,0.35)", backdropFilter: "blur(2px)" }}
        onClick={onCancel}
      />
      {/* Dialog */}
      <div
        className="relative z-10 w-full max-w-sm rounded-[var(--radius-lg)] p-6"
        style={{
          background: "var(--bg-primary)",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        <h3
          className="text-[15px] font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {title}
        </h3>
        <p
          className="mt-2 text-[13px] leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          {description}
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            variant={variant === "danger" ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

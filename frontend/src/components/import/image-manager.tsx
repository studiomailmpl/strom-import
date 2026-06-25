"use client";

import { useState, useRef, useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  ImageIcon,
  Upload,
  Trash2,
  Loader2,
  Globe,
  HardDrive,
  Plus,
} from "lucide-react";
import { toast } from "sonner";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* ─── Types ─── */

interface UploadedImage {
  id: string;
  filename: string;
  url: string;
  source: "uploaded";
  sort_order: number;
}

interface ImageManagerProps {
  productId: string;
  scrapedImages: string[];
  uploadedImages: UploadedImage[];
  onUploadedImagesChange: (images: UploadedImage[]) => void;
  readOnly?: boolean;
}

/* ─── Component ─── */

export function ImageManager({
  productId,
  scrapedImages,
  uploadedImages,
  onUploadedImagesChange,
  readOnly = false,
}: ImageManagerProps) {
  const { getToken } = useAuth();
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const hasUploaded = uploadedImages.length > 0;
  const totalImages = hasUploaded ? uploadedImages.length : scrapedImages.length;

  /* ─── Upload ─── */

  const handleUpload = useCallback(
    async (files: FileList | File[]) => {
      const fileArray = Array.from(files);
      if (fileArray.length === 0) return;

      const allowed = ["image/jpeg", "image/png", "image/webp", "image/avif"];
      for (const f of fileArray) {
        if (!allowed.includes(f.type)) {
          toast.error(`${f.name}: Filtype ikke understøttet. Brug JPEG, PNG eller WebP.`);
          return;
        }
        if (f.size > 10 * 1024 * 1024) {
          toast.error(`${f.name}: For stor (maks 10 MB)`);
          return;
        }
      }

      setUploading(true);
      try {
        const token = await getToken();
        const formData = new FormData();
        for (const f of fileArray) {
          formData.append("files", f);
        }

        const res = await fetch(
          `${API_BASE}/api/v1/products/${productId}/images`,
          {
            method: "POST",
            headers: {
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: formData,
          }
        );

        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: "Upload fejlede" }));
          throw new Error(err.detail || "Upload fejlede");
        }

        const newImages: UploadedImage[] = await res.json();
        onUploadedImagesChange([...uploadedImages, ...newImages]);
        toast.success(
          `${newImages.length} billede${newImages.length > 1 ? "r" : ""} uploadet`
        );
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Kunne ikke uploade billeder"
        );
      } finally {
        setUploading(false);
      }
    },
    [getToken, productId, uploadedImages, onUploadedImagesChange]
  );

  /* ─── Delete ─── */

  const handleDelete = useCallback(
    async (imageId: string) => {
      setDeletingId(imageId);
      try {
        const token = await getToken();
        const res = await fetch(
          `${API_BASE}/api/v1/products/${productId}/images/${imageId}`,
          {
            method: "DELETE",
            headers: {
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
          }
        );

        if (!res.ok && res.status !== 204) {
          throw new Error("Kunne ikke slette billede");
        }

        onUploadedImagesChange(
          uploadedImages.filter((img) => img.id !== imageId)
        );
        toast.success("Billede slettet");
      } catch {
        toast.error("Kunne ikke slette billede");
      } finally {
        setDeletingId(null);
      }
    },
    [getToken, productId, uploadedImages, onUploadedImagesChange]
  );

  /* ─── Drag and drop ─── */

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);

      const files = e.dataTransfer.files;
      if (files.length > 0) {
        handleUpload(files);
      }
    },
    [handleUpload]
  );

  /* ─── Display images ─── */

  const displayImages = hasUploaded
    ? uploadedImages.map((img) => ({
        id: img.id,
        url: img.url,
        source: "uploaded" as const,
        filename: img.filename,
      }))
    : scrapedImages.map((url, i) => ({
        id: `scraped-${i}`,
        url,
        source: "scraped" as const,
        filename: "",
      }));

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4
          className="text-[11px] font-medium uppercase tracking-[0.05em]"
          style={{ color: "var(--text-tertiary)" }}
        >
          Billeder ({totalImages})
          {hasUploaded && (
            <span
              className="ml-1.5 normal-case font-normal"
              style={{ color: "var(--accent)" }}
            >
              · uploadet
            </span>
          )}
        </h4>
        {!readOnly && !uploading && (
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1 text-[11px] font-medium"
            style={{ color: "var(--accent)" }}
          >
            <Plus className="h-3 w-3" />
            Tilføj
          </button>
        )}
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,image/avif"
        className="hidden"
        onChange={(e) => {
          if (e.target.files) handleUpload(e.target.files);
          e.target.value = "";
        }}
      />

      {/* Image grid + drop zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className="rounded-[var(--radius-md)] border-2 border-dashed transition-all"
        style={{
          borderColor: dragOver
            ? "var(--accent)"
            : totalImages > 0
              ? "transparent"
              : "var(--border-primary)",
          background: dragOver
            ? "var(--accent-light)"
            : totalImages > 0
              ? "transparent"
              : "var(--bg-secondary)",
        }}
      >
        {totalImages > 0 ? (
          <div className="grid grid-cols-3 gap-2 p-0.5">
            {displayImages.map((img) => (
              <div
                key={img.id}
                className="group relative aspect-square overflow-hidden rounded-[var(--radius-md)]"
                style={{ background: "var(--bg-tertiary)" }}
              >
                <img
                  src={img.url}
                  alt={img.filename || "Product image"}
                  className="h-full w-full object-cover"
                  loading="lazy"
                />

                {/* Source badge */}
                <div className="absolute left-1 top-1">
                  <span
                    className="flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium text-white"
                    style={{
                      background:
                        img.source === "uploaded"
                          ? "rgba(15, 110, 86, 0.8)"
                          : "rgba(0, 0, 0, 0.4)",
                    }}
                  >
                    {img.source === "uploaded" ? (
                      <HardDrive className="h-2 w-2" />
                    ) : (
                      <Globe className="h-2 w-2" />
                    )}
                    {img.source === "uploaded" ? "Upload" : "Web"}
                  </span>
                </div>

                {/* Delete button for uploaded images */}
                {img.source === "uploaded" && !readOnly && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(img.id);
                    }}
                    disabled={deletingId === img.id}
                    className="absolute right-1 top-1 rounded-full p-1 text-white opacity-0 transition-opacity group-hover:opacity-100"
                    style={{ background: "rgba(226, 75, 74, 0.8)" }}
                  >
                    {deletingId === img.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                  </button>
                )}
              </div>
            ))}

            {/* Add more button in grid */}
            {!readOnly && totalImages < 10 && (
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex aspect-square items-center justify-center rounded-[var(--radius-md)] border-2 border-dashed transition-colors"
                style={{
                  borderColor: "var(--border-primary)",
                  background: "var(--bg-secondary)",
                  color: "var(--text-tertiary)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--accent)";
                  e.currentTarget.style.color = "var(--accent)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--border-primary)";
                  e.currentTarget.style.color = "var(--text-tertiary)";
                }}
              >
                {uploading ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  <Upload className="h-5 w-5" />
                )}
              </button>
            )}
          </div>
        ) : (
          /* Empty state / drop zone */
          <button
            onClick={() => !readOnly && fileInputRef.current?.click()}
            disabled={readOnly || uploading}
            className="flex w-full flex-col items-center justify-center py-8"
          >
            {uploading ? (
              <>
                <Loader2
                  className="h-6 w-6 animate-spin"
                  style={{ color: "var(--accent)" }}
                />
                <p
                  className="mt-2 text-[11px] font-medium"
                  style={{ color: "var(--accent)" }}
                >
                  Uploader...
                </p>
              </>
            ) : (
              <>
                <div
                  className="rounded-full p-2.5"
                  style={{
                    background: dragOver
                      ? "var(--accent-light)"
                      : "var(--bg-tertiary)",
                  }}
                >
                  <ImageIcon
                    className="h-5 w-5"
                    style={{
                      color: dragOver
                        ? "var(--accent)"
                        : "var(--text-tertiary)",
                    }}
                  />
                </div>
                <p
                  className="mt-2 text-[11px] font-medium"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {dragOver
                    ? "Slip billeder her"
                    : readOnly
                      ? "Ingen billeder"
                      : "Træk billeder hertil"}
                </p>
                {!readOnly && (
                  <p
                    className="mt-0.5 text-[10px]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    eller klik for at vælge · JPEG, PNG, WebP
                  </p>
                )}
              </>
            )}
          </button>
        )}
      </div>

      {/* Info text when scraped images are being replaced */}
      {hasUploaded && scrapedImages.length > 0 && (
        <p
          className="mt-1.5 text-[10px]"
          style={{ color: "var(--text-tertiary)" }}
        >
          {scrapedImages.length} web-billede{scrapedImages.length > 1 ? "r" : ""}{" "}
          erstattet af dine uploads
        </p>
      )}
    </div>
  );
}

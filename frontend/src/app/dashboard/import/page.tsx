"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  Upload,
  FileText,
  X,
  CheckCircle2,
  ArrowRight,
  ExternalLink,
  FlaskConical,
} from "lucide-react";
import { apiUploadMultiple, apiFetch, apiStream } from "@/lib/api";
import type { SSEEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { StepIndicator } from "@/components/import/step-indicator";
import { ReviewTable } from "@/components/import/review-table";
import type { ImportProduct } from "@/components/import/product-card";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

type ImportStep = 0 | 1 | 2 | 3;

const STEP_LABELS = ["Upload faktura", "Analyser", "Review", "Push til Shopify"];
const MAX_FILE_SIZE = 25 * 1024 * 1024;
const ALLOWED_TYPES = ["application/pdf"];

interface FileEntry {
  file: File;
  id: string;
  status: "pending" | "reading" | "vision" | "done" | "error";
  productCount?: number;
}

interface LogEntry {
  timestamp: string;
  message: string;
}

export default function ImportPage() {
  const { getToken } = useAuth();
  const [step, setStep] = useState<ImportStep>(0);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [importId, setImportId] = useState<string | null>(null);
  const [products, setProducts] = useState<ImportProduct[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isPushing, setIsPushing] = useState(false);
  const [testMode, setTestMode] = useState(false);
  const [shopDomain, setShopDomain] = useState<string | null>(null);
  const abortStreamRef = useRef<(() => void) | null>(null);
  const mountedRef = useRef(true);

  const [analyseProgress, setAnalyseProgress] = useState(0);
  const [analyseLabel, setAnalyseLabel] = useState("");
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);

  const [pushResult, setPushResult] = useState<{
    productsCreated: number;
    totalVariants: number;
    totalCost: number;
  } | null>(null);

  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (step === 1 || step === 2 || isPushing) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [step, isPushing]);

  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const data = await apiFetch<{ connected: boolean; shop_domain?: string }>(
          "/api/v1/shopify/connection",
          { token: token || undefined }
        );
        if (data.connected && data.shop_domain) {
          setShopDomain(data.shop_domain);
        }
      } catch {
        // Non-critical
      }
    })();
  }, [getToken]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortStreamRef.current?.();
    };
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logEntries]);

  const addLog = (timestamp: string, message: string) => {
    setLogEntries((prev) => [...prev, { timestamp, message }]);
  };

  /* ─── FILE HANDLING ─── */

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const entries: FileEntry[] = [];
    for (const file of Array.from(newFiles)) {
      if (!ALLOWED_TYPES.includes(file.type)) {
        toast.error(`${file.name}: Filtype ikke understøttet`);
        continue;
      }
      if (file.size > MAX_FILE_SIZE) {
        toast.error(`${file.name}: For stor (maks 25 MB)`);
        continue;
      }
      entries.push({
        file,
        id: crypto.randomUUID(),
        status: "pending",
      });
    }
    setFiles((prev) => [...prev, ...entries]);
  }, []);

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      addFiles(e.dataTransfer.files);
    },
    [addFiles]
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) addFiles(e.target.files);
      e.target.value = "";
    },
    [addFiles]
  );

  /* ─── UPLOAD + ANALYSE ─── */

  const handleStartAnalyse = async () => {
    if (files.length === 0) return;
    setStep(1);
    setLogEntries([]);
    setAnalyseProgress(0);
    setAnalyseLabel(`Uploader ${files.length} faktura${files.length > 1 ? "er" : ""}...`);

    try {
      const token = await getToken();

      const uploadResult = await apiUploadMultiple<{ id: string }>(
        "/api/v1/imports/",
        files.map((f) => f.file),
        token || undefined,
        testMode ? { test_mode: "true" } : undefined
      );
      setImportId(uploadResult.id);

      const now = new Date();
      const ts = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}:${now.getSeconds().toString().padStart(2, "0")}`;
      addLog(ts, `Upload færdig - starter analyse`);

      await apiFetch(`/api/v1/imports/${uploadResult.id}/analyse`, {
        method: "POST",
        token: token || undefined,
      });

      const freshToken = await getToken();
      // Abort any previous stream before starting a new one
      abortStreamRef.current?.();

      // Create the stream — apiStream returns an abort function synchronously,
      // so we assign it to the ref IMMEDIATELY to avoid the race condition
      // where an unmount between stream start and ref assignment would miss cleanup.
      const abort = apiStream(
        `/api/v1/imports/${uploadResult.id}/stream`,
        freshToken || "",
        (event: SSEEvent) => {
          if (!mountedRef.current) return;
          switch (event.type) {
            case "file_start":
              addLog(event.timestamp || "", `Claude Vision - analyserer ${event.file_name}`);
              setFiles((prev) =>
                prev.map((f, i) =>
                  i === (event.file_index || 1) - 1 ? { ...f, status: "vision" } : f
                )
              );
              break;
            case "log":
              addLog(event.timestamp || "", event.message || "");
              break;
            case "progress":
              setAnalyseProgress(event.percent || 0);
              setAnalyseLabel(`Analyserer faktura ${event.current_file} af ${event.total_files}`);
              break;
            case "file_done":
              addLog(event.timestamp || "", `${event.file_name}: ${event.products_found} produkter fundet`);
              setFiles((prev) =>
                prev.map((f) =>
                  f.file.name === event.file_name
                    ? { ...f, status: "done", productCount: event.products_found }
                    : f
                )
              );
              break;
            case "done":
              addLog(new Date().toTimeString().slice(0, 8), `Analyse færdig - ${event.total_products} produkter i alt`);
              fetchProducts(uploadResult.id);
              break;
            case "error":
              addLog(new Date().toTimeString().slice(0, 8), `FEJL: ${event.message}`);
              toast.error(event.message || "Analysefejl");
              break;
          }
        },
        () => {
          if (mountedRef.current) {
            pollForCompletion(uploadResult.id);
          }
        }
      );
      // Assign IMMEDIATELY after apiStream returns (synchronous)
      abortStreamRef.current = abort;
    } catch (error) {
      console.error("Upload/analyse error:", error);
      toast.error("Upload fejlede - prøv igen");
      setStep(0);
    }
  };

  const fetchProducts = async (id: string) => {
    try {
      const token = await getToken();
      const imp = await apiFetch<{ products: ImportProduct[] }>(
        `/api/v1/imports/${id}`,
        { token: token || undefined }
      );
      setProducts(imp.products);
      setSelectedIds(new Set(imp.products.map((p) => p.id)));
      setStep(2);
    } catch {
      toast.error("Kunne ikke hente produkter");
      setStep(0);
    }
  };

  const pollForCompletion = async (id: string) => {
    let attempts = 0;
    while (attempts < 120 && mountedRef.current) {
      await new Promise((r) => setTimeout(r, 2000));
      if (!mountedRef.current) return;
      try {
        const token = await getToken();
        const imp = await apiFetch<{
          status: string;
          products: ImportProduct[];
          total_products: number;
        }>(`/api/v1/imports/${id}`, { token: token || undefined });

        setAnalyseProgress(Math.min(95, 20 + attempts * 2));
        setAnalyseLabel(`Analyserer... ${imp.total_products || 0} produkter fundet`);

        if (imp.status === "review") {
          setProducts(imp.products);
          setSelectedIds(new Set(imp.products.map((p) => p.id)));
          setStep(2);
          return;
        } else if (imp.status === "failed") {
          throw new Error("Analyse fejlede");
        }
      } catch (err) {
        if ((err as Error).message === "Analyse fejlede") {
          toast.error("Analyse fejlede");
          setStep(0);
          return;
        }
      }
      attempts++;
    }
    toast.error("Analysen tog for lang tid");
    setStep(0);
  };

  /* ─── REVIEW ─── */

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

  /* ─── ORDER CONFIRMATION FALLBACK ─── */

  // The automatic search in step 4c missed this one, so the user names the
  // Drive file. Linking runs against the whole import, so every still-unmatched
  // product gets a chance at the file, not just the row that was clicked.
  const handleLinkOrderConfirmation = async () => {
    if (!importId) return;

    const driveFileId = window.prompt(
      "Indsæt Google Drive fil-ID for ordrebekræftelsen.\n\n" +
        "ID'et er den lange del af filens URL i Drive:\n" +
        "drive.google.com/file/d/<FIL-ID>/view"
    );
    if (!driveFileId || !driveFileId.trim()) return;

    try {
      const token = await getToken();
      const result = await apiFetch<{
        file_name: string;
        lines_parsed: number;
        matched: number;
        unmatched: number;
      }>(`/api/v1/imports/${importId}/link-order-confirmation`, {
        method: "POST",
        token: token || undefined,
        body: JSON.stringify({ drive_file_id: driveFileId.trim() }),
      });

      if (result.matched > 0) {
        toast.success(
          `${result.file_name}: ${result.matched} produkter matchet` +
            (result.unmatched > 0 ? `, ${result.unmatched} stadig umatchede` : "")
        );
      } else {
        toast.warning(
          `${result.file_name} blev læst (${result.lines_parsed} linjer), ` +
            "men ingen produkter matchede"
        );
      }

      // Pull the merged data back so the table reflects the new match.
      const refreshToken = await getToken();
      const imp = await apiFetch<{ products: ImportProduct[] }>(
        `/api/v1/imports/${importId}`,
        { token: refreshToken || undefined }
      );
      setProducts(imp.products);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Kunne ikke linke ordrebekræftelsen"
      );
    }
  };

  /* ─── EDIT ─── */

  const handleUpdateProduct = useCallback(
    async (productId: string, data: Partial<ImportProduct>) => {
      try {
        const token = await getToken();
        const updated = await apiFetch<ImportProduct>(
          `/api/v1/products/${productId}`,
          {
            method: "PATCH",
            token: token || undefined,
            body: JSON.stringify(data),
          }
        );
        setProducts((prev) =>
          prev.map((p) => (p.id === productId ? { ...p, ...updated } : p))
        );
        toast.success("Produkt opdateret");
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Kunne ikke opdatere produktet"
        );
      }
    },
    [getToken]
  );

  /* ─── PUSH ─── */

  const handlePush = async () => {
    if (!importId || selectedIds.size === 0) return;
    setIsPushing(true);

    try {
      const token = await getToken();
      await apiFetch(`/api/v1/shopify/push/${importId}`, {
        method: "POST",
        token: token || undefined,
        body: JSON.stringify({ product_ids: Array.from(selectedIds) }),
      });

      let attempts = 0;
      while (attempts < 120 && mountedRef.current) {
        await new Promise((r) => setTimeout(r, 2000));
        if (!mountedRef.current) return;
        const freshToken = await getToken();
        const imp = await apiFetch<{
          status: string;
          products_pushed: number;
          total_products: number;
          products: ImportProduct[];
        }>(`/api/v1/imports/${importId}`, {
          token: freshToken || undefined,
        });

        if (imp.status === "completed") {
          const totalVariants = imp.products.reduce(
            (sum, p) => sum + (p.variants?.length || 0),
            0
          );
          const totalCost = imp.products.reduce(
            (sum, p) => sum + (p.retail_price_dkk || 0),
            0
          );
          setPushResult({
            productsCreated: imp.products_pushed || selectedIds.size,
            totalVariants,
            totalCost,
          });
          setStep(3);
          toast.success("Produkter sendt til Shopify");
          return;
        } else if (imp.status === "failed") {
          throw new Error("Push fejlede");
        }
        attempts++;
      }
      throw new Error("Timeout");
    } catch (error) {
      console.error("Push error:", error);
      toast.error("Push til Shopify fejlede");
    } finally {
      setIsPushing(false);
    }
  };

  /* ─── RESET ─── */

  const handleReset = () => {
    setStep(0);
    setFiles([]);
    setImportId(null);
    setProducts([]);
    setSelectedIds(new Set());
    setPushResult(null);
    setLogEntries([]);
    setAnalyseProgress(0);
  };

  /* ─── COMPUTED ─── */

  const completedSteps: number[] = [];
  for (let i = 0; i < step; i++) completedSteps.push(i);

  const fileStatusBadge = (entry: FileEntry) => {
    switch (entry.status) {
      case "done":
        return <Badge variant="success">{entry.productCount} PRODUKTER</Badge>;
      case "vision":
        return <Badge variant="info">VISION</Badge>;
      case "reading":
        return <Badge variant="default">LÆSER</Badge>;
      case "error":
        return <Badge variant="error">FEJL</Badge>;
      default:
        return <Badge variant="outline">KLAR</Badge>;
    }
  };

  return (
    <div>
      {/* Step indicator */}
      <div className="mb-12 px-4">
        <StepIndicator
          steps={STEP_LABELS}
          currentStep={step}
          completedSteps={completedSteps}
        />
      </div>

      {/* ═══ STEP 0: UPLOAD ═══ */}
      {step === 0 && (
        <div>
          <p
            className="mb-1 text-[11px] font-medium uppercase tracking-[0.05em]"
            style={{ color: "var(--text-tertiary)" }}
          >
            Trin 1 af 4
          </p>
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Upload faktura
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Træk PDF-fakturaer hertil. Systemet udtrækker produkter, størrelser og priser automatisk.
          </p>

          {/* Drop zone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className="card relative mt-6 flex flex-col items-center justify-center rounded-[var(--radius-lg)] border-2 border-dashed px-6 py-20 text-center transition-colors"
            style={{
              background: isDragging ? "var(--accent-light)" : "var(--bg-primary)",
              borderColor: isDragging ? "var(--accent)" : "var(--border-primary)",
            }}
          >
            <Upload
              className="mb-4 h-10 w-10"
              style={{ color: isDragging ? "var(--accent)" : "var(--text-tertiary)" }}
            />
            <p className="text-[13px] font-medium" style={{ color: "var(--text-secondary)" }}>
              Slip PDF-fakturaer her
            </p>
            <p className="mt-1 text-[12px]" style={{ color: "var(--text-tertiary)" }}>
              eller{" "}
              <label className="cursor-pointer underline" style={{ color: "var(--accent)" }}>
                vælg fra computer
                <input
                  type="file"
                  accept=".pdf"
                  multiple
                  onChange={handleFileSelect}
                  className="hidden"
                />
              </label>{" "}
              · op til 25 MB
            </p>
            <p className="mt-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              PDF
            </p>
          </div>

          {/* File list */}
          {files.length > 0 && (
            <div className="mt-4 space-y-2">
              {files.map((entry) => (
                <div
                  key={entry.id}
                  className="card flex items-center gap-3 rounded-[var(--radius-md)] px-4 py-3"
                >
                  <FileText className="h-5 w-5 flex-shrink-0" style={{ color: "var(--danger)" }} />
                  <div className="flex-1 min-w-0">
                    <p
                      className="truncate text-[13px] font-medium"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {entry.file.name}
                    </p>
                    <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      {(entry.file.size / 1024 / 1024).toFixed(1)} MB
                    </p>
                  </div>
                  {fileStatusBadge(entry)}
                  <button
                    onClick={() => removeFile(entry.id)}
                    className="rounded-[var(--radius-sm)] p-1 transition-colors"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Test mode toggle */}
          {files.length > 0 && (
            <div
              className="mt-4 rounded-[var(--radius-md)] px-4 py-3"
              style={{
                background: "var(--warning-light)",
                border: "1px solid var(--warning)",
              }}
            >
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={testMode}
                  onChange={(e) => setTestMode(e.target.checked)}
                  className="h-4 w-4 rounded"
                  style={{ accentColor: "var(--warning)" }}
                />
                <FlaskConical className="h-4 w-4" style={{ color: "var(--warning-text)" }} />
                <div>
                  <span className="text-[13px] font-medium" style={{ color: "var(--warning-text)" }}>
                    Test mode
                  </span>
                  <p className="text-[11px]" style={{ color: "var(--warning-text)" }}>
                    Springer supplerings-tjek over og tagger produkter med _test-import for nem oprydning.
                  </p>
                </div>
              </label>
            </div>
          )}

          {/* Bottom actions */}
          {files.length > 0 && (
            <div className="mt-6 flex items-center justify-between">
              <Button variant="secondary" onClick={handleReset}>
                Annuller
              </Button>
              <Button onClick={handleStartAnalyse}>
                <ArrowRight className="h-4 w-4" />
                Analyser {files.length} faktura{files.length > 1 ? "er" : ""}
              </Button>
            </div>
          )}
        </div>
      )}

      {/* ═══ STEP 1: ANALYSE ═══ */}
      {step === 1 && (
        <div>
          <p
            className="mb-1 text-[11px] font-medium uppercase tracking-[0.05em]"
            style={{ color: "var(--text-tertiary)" }}
          >
            Trin 2 af 4
          </p>
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Analyser
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Claude Vision læser fakturaerne og udtrækker produktdata.
          </p>

          {/* Progress bar */}
          <div className="card mt-6 rounded-[var(--radius-lg)] p-6"
          >
            <div className="flex items-center justify-between text-[13px]">
              <span className="font-medium" style={{ color: "var(--text-secondary)" }}>
                {analyseLabel}
              </span>
              <span style={{ color: "var(--text-tertiary)" }}>{analyseProgress}%</span>
            </div>
            <div
              className="mt-3 h-2 overflow-hidden rounded-[var(--radius-full)]"
              style={{ background: "var(--bg-tertiary)" }}
            >
              <div
                className="h-full rounded-[var(--radius-full)] transition-all duration-500"
                style={{
                  width: `${analyseProgress}%`,
                  background: "var(--accent)",
                }}
              />
            </div>
          </div>

          {/* Log */}
          <div
            className="mt-4 max-h-64 overflow-y-auto rounded-[var(--radius-lg)] p-4 font-mono text-[11px]"
            style={{
              background: "var(--bg-inverse)",
              color: "var(--text-inverse)",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            {logEntries.length === 0 && (
              <p style={{ opacity: 0.5 }}>Venter på log...</p>
            )}
            {logEntries.map((entry, i) => (
              <div key={i} className="py-0.5">
                <span style={{ opacity: 0.5 }}>{entry.timestamp}</span>{" "}
                <span style={{ opacity: 0.9 }}>{entry.message}</span>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* ═══ STEP 2: REVIEW ═══ */}
      {step === 2 && (
        <div>
          <p
            className="mb-1 text-[11px] font-medium uppercase tracking-[0.05em]"
            style={{ color: "var(--text-tertiary)" }}
          >
            Trin 3 af 4
          </p>
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Review produkter
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            {selectedIds.size} af {products.length} produkter valgt. Ret priser,
            varianter eller SKU&apos;er før push.
          </p>

          <div className="mt-6">
            <ReviewTable
              products={products}
              selectedIds={selectedIds}
              onToggleSelect={handleToggleSelect}
              onToggleAll={handleToggleAll}
              onUpdateProduct={handleUpdateProduct}
              onLinkOrderConfirmation={handleLinkOrderConfirmation}
            />
          </div>

          <div className="mt-6 flex items-center justify-between">
            <Button variant="secondary" onClick={handleReset}>
              Annuller
            </Button>
            <Button
              onClick={handlePush}
              loading={isPushing}
              disabled={selectedIds.size === 0}
            >
              <ArrowRight className="h-4 w-4" />
              Push {selectedIds.size} produkter til Shopify
            </Button>
          </div>
        </div>
      )}

      {/* ═══ STEP 3: DONE ═══ */}
      {step === 3 && pushResult && (
        <div>
          <p
            className="mb-1 text-[11px] font-medium uppercase tracking-[0.05em]"
            style={{ color: "var(--text-tertiary)" }}
          >
            Import {importId ? `#${importId.slice(0, 4).toUpperCase()}` : ""} · Gennemført
          </p>
          <h1
            className="text-[20px] font-medium tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Færdig
          </h1>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Produkter er oprettet i Shopify. Du kan se dem i admin eller køre en ny import.
          </p>

          {/* Success card */}
          <div
            className="mt-6 rounded-[var(--radius-lg)] p-8 text-center"
            style={{
              background: "var(--success-light)",
              border: "1px solid var(--success)",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <CheckCircle2 className="mx-auto h-12 w-12" style={{ color: "var(--success)" }} />
            <p
              className="mt-4 text-[16px] font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {pushResult.productsCreated} produkter oprettet i Shopify
            </p>
            <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
              Varianter og lager er synkroniseret
            </p>
          </div>

          {/* KPI row */}
          <div className="mt-6 grid grid-cols-3 gap-4">
            {[
              { label: "Produkter", value: pushResult.productsCreated.toString() },
              { label: "Varianter", value: pushResult.totalVariants.toString() },
              { label: "Samlet indkøb", value: `${pushResult.totalCost.toLocaleString("da-DK")} kr` },
            ].map((kpi) => (
              <div
                key={kpi.label}
                className="card rounded-[var(--radius-lg)] p-5 text-center"
              >
                <p
                  className="text-[11px] font-medium uppercase tracking-[0.05em]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  {kpi.label}
                </p>
                <p
                  className="mt-1 text-[24px] font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {kpi.value}
                </p>
              </div>
            ))}
          </div>

          {/* Action buttons */}
          <div className="mt-6 flex items-center gap-3">
            <Button
              onClick={() =>
                window.open(
                  shopDomain
                    ? `https://${shopDomain}/admin/products`
                    : "https://admin.shopify.com",
                  "_blank"
                )
              }
            >
              <ExternalLink className="h-4 w-4" />
              Åbn i Shopify admin
            </Button>
            <Button variant="secondary" onClick={handleReset}>
              Ny import
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

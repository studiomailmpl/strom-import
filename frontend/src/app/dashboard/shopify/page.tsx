"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import { useAuth } from "@clerk/nextjs";
import { useSearchParams } from "next/navigation";
import {
  ShoppingBag,
  ExternalLink,
  CheckCircle2,
  Unplug,
  Store,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { formatDate } from "@/lib/utils";
import { toast } from "sonner";

interface ShopifyConnection {
  connected: boolean;
  shop_domain?: string;
  is_active?: boolean;
  scopes?: string;
  created_at?: string;
}

export default function ShopifyPage() {
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
      <ShopifyPageInner />
    </Suspense>
  );
}

function ShopifyPageInner() {
  const { getToken } = useAuth();
  const searchParams = useSearchParams();
  const [connection, setConnection] = useState<ShopifyConnection | null>(null);
  const [loading, setLoading] = useState(true);
  const [shopDomain, setShopDomain] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [showDisconnectConfirm, setShowDisconnectConfirm] = useState(false);
  const justConnected = searchParams.get("connected") === "true";

  const fetchConnection = useCallback(async () => {
    try {
      const token = await getToken();
      const data = await apiFetch<ShopifyConnection>(
        "/api/v1/shopify/connection",
        { token: token || undefined }
      );
      setConnection(data);
    } catch (err) {
      setConnection({ connected: false });
      toast.error("Kunne ikke hente Shopify-forbindelse");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchConnection();
  }, [fetchConnection]);

  const handleConnect = async () => {
    if (!shopDomain.trim()) return;
    setConnecting(true);
    try {
      const token = await getToken();
      const result = await apiFetch<{ redirect_url: string }>(
        `/api/v1/shopify/install?shop=${encodeURIComponent(shopDomain.trim())}`,
        { token: token || undefined }
      );
      // Validate redirect URL — only allow Shopify OAuth or relative URLs
      const url = result.redirect_url;
      try {
        const parsed = new URL(url, window.location.origin);
        const isShopify =
          parsed.hostname.endsWith(".myshopify.com") ||
          parsed.hostname.endsWith(".shopify.com");
        const isRelative = parsed.origin === window.location.origin;
        if (!isShopify && !isRelative) {
          throw new Error("Ugyldig redirect URL");
        }
        window.location.href = parsed.href;
      } catch {
        toast.error("Ugyldig redirect URL fra serveren");
        setConnecting(false);
        return;
      }
    } catch (error) {
      console.error("Connect error:", error);
      toast.error("Kunne ikke forbinde til Shopify");
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    setShowDisconnectConfirm(false);
    setDisconnecting(true);
    try {
      const token = await getToken();
      await apiFetch("/api/v1/shopify/disconnect", {
        method: "POST",
        token: token || undefined,
      });
      setConnection({ connected: false });
      toast.success("Shopify-forbindelse afbrudt");
    } catch (error) {
      console.error("Disconnect error:", error);
      toast.error("Kunne ikke afbryde forbindelsen");
    } finally {
      setDisconnecting(false);
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

  const isConnected = connection?.connected && connection?.is_active;

  return (
    <div>
      <div className="mb-6">
        <h1
          className="text-[20px] font-medium tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          Shopify-forbindelse
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Forbind din Shopify-butik for at sende produkter
        </p>
      </div>

      {/* Success banner after OAuth callback */}
      {justConnected && isConnected && (
        <div
          className="mb-6 flex items-center gap-3 rounded-[var(--radius-md)] px-4 py-3"
          style={{
            background: "var(--success-light)",
            border: "1px solid var(--success)",
          }}
        >
          <CheckCircle2 className="h-5 w-5" style={{ color: "var(--success)" }} />
          <p className="text-[13px] font-medium" style={{ color: "var(--success-text)" }}>
            Shopify-butik forbundet! Du kan nu importere produkter.
          </p>
        </div>
      )}

      <div className="card rounded-[var(--radius-lg)] p-8"
      >
        {isConnected ? (
          <div className="space-y-6">
            <div className="flex items-start gap-4">
              <div
                className="flex h-12 w-12 items-center justify-center rounded-[var(--radius-lg)]"
                style={{ background: "var(--success-light)" }}
              >
                <Store className="h-6 w-6" style={{ color: "var(--success)" }} />
              </div>
              <div className="flex-1">
                <h3 className="font-medium" style={{ color: "var(--text-primary)" }}>
                  Forbundet
                </h3>
                <p className="mt-0.5 text-[13px]" style={{ color: "var(--text-secondary)" }}>
                  {connection?.shop_domain}
                </p>
                {connection?.created_at && (
                  <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    Forbundet {formatDate(connection.created_at)}
                  </p>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowDisconnectConfirm(true)}
                loading={disconnecting}
                style={{ color: "var(--danger)" }}
              >
                <Unplug className="h-4 w-4" />
                Afbryd
              </Button>
            </div>

            {/* Scopes info */}
            <div
              className="rounded-[var(--radius-md)] px-4 py-3"
              style={{ background: "var(--bg-secondary)" }}
            >
              <p
                className="text-[11px] font-medium"
                style={{ color: "var(--text-tertiary)" }}
              >
                Tilladelser
              </p>
              <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                {connection?.scopes?.split(",").join(", ")}
              </p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center text-center">
            <div
              className="mb-4 flex h-12 w-12 items-center justify-center rounded-[var(--radius-lg)]"
              style={{ background: "var(--bg-tertiary)" }}
            >
              <ShoppingBag className="h-6 w-6" style={{ color: "var(--text-tertiary)" }} />
            </div>
            <h3 className="font-medium" style={{ color: "var(--text-primary)" }}>
              Ingen butik forbundet
            </h3>
            <p
              className="mt-2 max-w-sm text-[13px]"
              style={{ color: "var(--text-secondary)" }}
            >
              Forbind din Shopify-butik for at kunne sende produkter direkte fra
              importerede fakturaer.
            </p>

            {/* OAuth connect form */}
            <div className="mt-6 flex w-full max-w-sm items-center gap-2">
              <input
                type="text"
                value={shopDomain}
                onChange={(e) => setShopDomain(e.target.value)}
                placeholder="din-butik.myshopify.com"
                className="flex-1 rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
                style={{
                  background: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-primary)",
                  outline: "none",
                }}
                onKeyDown={(e) => e.key === "Enter" && handleConnect()}
              />
              <Button onClick={handleConnect} loading={connecting}>
                <ExternalLink className="h-4 w-4" />
                Forbind
              </Button>
            </div>
            <p className="mt-3 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Du bliver sendt til Shopify for at godkende adgang
            </p>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={showDisconnectConfirm}
        title="Afbryd Shopify-forbindelse"
        description="Er du sikker på, at du vil afbryde forbindelsen til din Shopify-butik? Du kan altid forbinde igen senere."
        confirmLabel="Afbryd"
        variant="danger"
        onConfirm={handleDisconnect}
        onCancel={() => setShowDisconnectConfirm(false)}
      />
    </div>
  );
}

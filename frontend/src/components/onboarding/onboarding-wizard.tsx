"use client";

import { useState } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  ExternalLink,
  Check,
  ArrowRight,
  Settings,
  Upload,
  Store,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

interface OnboardingWizardProps {
  onComplete: () => void;
}

type Step = "welcome" | "shopify" | "settings" | "done";

export function OnboardingWizard({ onComplete }: OnboardingWizardProps) {
  const { getToken } = useAuth();
  const [step, setStep] = useState<Step>("welcome");
  const [shopDomain, setShopDomain] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [eurRate, setEurRate] = useState(7.46);
  const [markup, setMarkup] = useState(2.5);

  const handleConnectShopify = async () => {
    if (!shopDomain.trim()) return;
    setConnecting(true);
    try {
      const token = await getToken();
      const result = await apiFetch<{ redirect_url: string }>(
        `/api/v1/shopify/install?shop=${encodeURIComponent(shopDomain.trim())}`,
        { token: token || undefined }
      );
      window.location.href = result.redirect_url;
    } catch (error) {
      console.error("Connect error:", error);
      toast.error("Kunne ikke forbinde til Shopify");
      setConnecting(false);
    }
  };

  const steps = [
    { key: "welcome", label: "Velkommen" },
    { key: "shopify", label: "Shopify" },
    { key: "settings", label: "Indstillinger" },
  ];

  return (
    <div
      className="flex min-h-screen items-center justify-center p-4"
      style={{ background: "var(--bg-tertiary)" }}
    >
      <div className="w-full max-w-lg">
        {/* Progress */}
        <div className="mb-8 flex items-center justify-center gap-2">
          {steps.map((s, i) => {
            const stepKeys: Step[] = ["welcome", "shopify", "settings"];
            const current = stepKeys.indexOf(step === "done" ? "settings" : step);
            const isComplete = i < current || step === "done";
            const isCurrent = s.key === step;

            return (
              <div key={s.key} className="flex items-center gap-2">
                {i > 0 && (
                  <div
                    className="h-px w-6"
                    style={{
                      background: isComplete ? "var(--accent)" : "var(--border-primary)",
                    }}
                  />
                )}
                <div
                  className="flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-medium"
                  style={
                    isCurrent || isComplete
                      ? { background: "var(--accent)", color: "#fff" }
                      : {
                          background: "var(--bg-secondary)",
                          color: "var(--text-tertiary)",
                        }
                  }
                >
                  {isComplete ? <Check className="h-3.5 w-3.5" /> : i + 1}
                </div>
              </div>
            );
          })}
        </div>

        {/* Card */}
        <div
          className="card rounded-[var(--radius-lg)] p-8"
        >
          {/* Welcome */}
          {step === "welcome" && (
            <div className="text-center">
              <div
                className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-[var(--radius-lg)]"
                style={{ background: "var(--accent)" }}
              >
                <Upload className="h-7 w-7 text-white" />
              </div>
              <h2
                className="text-[18px] font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                Velkommen til STRØM Import
              </h2>
              <p
                className="mx-auto mt-3 max-w-sm text-[13px]"
                style={{ color: "var(--text-secondary)" }}
              >
                Importér produkter fra PDF-fakturaer direkte til din
                Shopify-butik. Lad os komme i gang med opsætningen.
              </p>
              <Button className="mt-8" size="lg" onClick={() => setStep("shopify")}>
                Kom i gang
                <ArrowRight className="h-4 w-4" />
              </Button>
            </div>
          )}

          {/* Shopify connect */}
          {step === "shopify" && (
            <div>
              <div className="mb-6 flex items-center gap-3">
                <div
                  className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-md)]"
                  style={{ background: "var(--bg-tertiary)" }}
                >
                  <Store className="h-5 w-5" style={{ color: "var(--text-secondary)" }} />
                </div>
                <div>
                  <h2 className="text-[16px] font-medium" style={{ color: "var(--text-primary)" }}>
                    Forbind Shopify
                  </h2>
                  <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
                    Vi skal bruge adgang til din butik for at oprette produkter
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <div>
                  <label
                    className="mb-1.5 block text-[11px] font-medium"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Butik-domæne
                  </label>
                  <input
                    type="text"
                    value={shopDomain}
                    onChange={(e) => setShopDomain(e.target.value)}
                    placeholder="din-butik.myshopify.com"
                    className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
                    style={{
                      background: "var(--bg-primary)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--border-primary)",
                      outline: "none",
                    }}
                    onKeyDown={(e) => e.key === "Enter" && handleConnectShopify()}
                  />
                </div>

                <div className="flex items-center gap-3">
                  <Button onClick={handleConnectShopify} loading={connecting} className="flex-1">
                    <ExternalLink className="h-4 w-4" />
                    Forbind med Shopify
                  </Button>
                </div>

                <button
                  onClick={() => setStep("settings")}
                  className="w-full text-center text-[11px]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  Spring over — jeg forbinder senere
                </button>
              </div>
            </div>
          )}

          {/* Settings */}
          {step === "settings" && (
            <div>
              <div className="mb-6 flex items-center gap-3">
                <div
                  className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-md)]"
                  style={{ background: "var(--bg-tertiary)" }}
                >
                  <Settings className="h-5 w-5" style={{ color: "var(--text-secondary)" }} />
                </div>
                <div>
                  <h2 className="text-[16px] font-medium" style={{ color: "var(--text-primary)" }}>
                    Standardindstillinger
                  </h2>
                  <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
                    Du kan altid ændre disse senere
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <div>
                  <label
                    className="mb-1.5 block text-[11px] font-medium"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    EUR til DKK kurs
                  </label>
                  <input
                    type="number"
                    value={eurRate}
                    onChange={(e) => setEurRate(parseFloat(e.target.value) || 7.46)}
                    step={0.01}
                    className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
                    style={{
                      background: "var(--bg-primary)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--border-primary)",
                      outline: "none",
                    }}
                  />
                  <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    Bruges til at omregne kostpriser fra EUR til DKK
                  </p>
                </div>
                <div>
                  <label
                    className="mb-1.5 block text-[11px] font-medium"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Markup-faktor
                  </label>
                  <input
                    type="number"
                    value={markup}
                    onChange={(e) => setMarkup(parseFloat(e.target.value) || 2.5)}
                    step={0.1}
                    className="w-full rounded-[var(--radius-md)] px-3 py-2.5 text-[13px]"
                    style={{
                      background: "var(--bg-primary)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--border-primary)",
                      outline: "none",
                    }}
                  />
                  <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    Kostpris × kurs × markup = udsalgspris (afrundet til nærmeste 50 kr)
                  </p>
                </div>

                <Button
                  size="lg"
                  className="w-full mt-2"
                  onClick={async () => {
                    try {
                      const token = await getToken();
                      await apiFetch("/api/v1/settings", {
                        method: "PATCH",
                        token: token || undefined,
                        body: JSON.stringify({
                          default_eur_rate: eurRate,
                          default_markup: markup,
                        }),
                      });
                    } catch {
                      toast.error("Kunne ikke gemme indstillinger");
                    }
                    setStep("done");
                  }}
                >
                  Afslut opsætning
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}

          {/* Done */}
          {step === "done" && (
            <div className="text-center">
              <div
                className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-[var(--radius-lg)]"
                style={{ background: "var(--success-light)" }}
              >
                <Check className="h-8 w-8" style={{ color: "var(--success)" }} />
              </div>
              <h2
                className="text-[18px] font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                Alt er klar!
              </h2>
              <p
                className="mx-auto mt-3 max-w-sm text-[13px]"
                style={{ color: "var(--text-secondary)" }}
              >
                Du kan nu uploade din første PDF-faktura og importere produkter
                til Shopify.
              </p>
              <Button className="mt-8" size="lg" onClick={onComplete}>
                <Upload className="h-4 w-4" />
                Start første import
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

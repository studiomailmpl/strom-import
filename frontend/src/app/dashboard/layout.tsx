"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth, useOrganization, useUser, UserButton } from "@clerk/nextjs";
import {
  LayoutDashboard,
  FileUp,
  History,
  Tag,
  Store,
  Settings,
  Menu,
  X,
  ChevronRight,
  BarChart3,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { OnboardingWizard } from "@/components/onboarding/onboarding-wizard";
import { ErrorBoundary } from "@/components/error-boundary";
import { Toaster } from "sonner";

/* ─── Types ─── */

interface NavItem {
  name: string;
  href: string;
  icon: typeof LayoutDashboard;
  badge?: number;
  group?: string;
}

/* ─── Helpers ─── */

function getGreeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "God nat";
  if (h < 10) return "God morgen";
  if (h < 13) return "God formiddag";
  if (h < 17) return "God eftermiddag";
  return "God aften";
}

function formatDanishDate(): string {
  const d = new Date();
  const days = ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"];
  const months = [
    "januar", "februar", "marts", "april", "maj", "juni",
    "juli", "august", "september", "oktober", "november", "december",
  ];
  return `${days[d.getDay()]} ${d.getDate()}. ${months[d.getMonth()]}`;
}

/* ─── Component ─── */

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { getToken } = useAuth();
  const { organization } = useOrganization();
  const { user } = useUser();
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [checkingOnboarding, setCheckingOnboarding] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [importCount, setImportCount] = useState(0);
  const [shopifyOk, setShopifyOk] = useState(false);
  const [shopDomain, setShopDomain] = useState("");

  const checkOnboardingNeeded = useCallback(async () => {
    try {
      const token = await getToken();
      const conn = await apiFetch<{
        connected: boolean;
        shop_domain?: string;
      }>("/api/v1/shopify/connection", { token: token || undefined });
      const imports = await apiFetch<Array<{ id: string }>>("/api/v1/imports/", {
        token: token || undefined,
      });

      setShopifyOk(conn.connected);
      if (conn.shop_domain) setShopDomain(conn.shop_domain);
      setImportCount(imports.length);

      if (!conn.connected && imports.length === 0) {
        setShowOnboarding(true);
      }
    } catch {
      // API not available yet
    } finally {
      setCheckingOnboarding(false);
    }
  }, [getToken]);

  useEffect(() => {
    checkOnboardingNeeded();
  }, [checkOnboardingNeeded]);

  // Close sidebar on route change
  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  const handleOnboardingComplete = () => {
    setShowOnboarding(false);
    router.push("/dashboard/import");
  };

  if (showOnboarding && !checkingOnboarding) {
    return <OnboardingWizard onComplete={handleOnboardingComplete} />;
  }

  const firstName = user?.firstName || "der";

  /* ─── Navigation config ─── */

  const mainNav: NavItem[] = [
    { name: "Oversigt", href: "/dashboard", icon: LayoutDashboard },
    { name: "Ny import", href: "/dashboard/import", icon: FileUp },
    {
      name: "Historik",
      href: "/dashboard/history",
      icon: History,
      badge: importCount || undefined,
    },
  ];

  const settingsNav: NavItem[] = [
    { name: "Brands", href: "/dashboard/settings/brands", icon: Tag },
    { name: "SEO Keywords", href: "/dashboard/seo", icon: BarChart3 },
    { name: "Shopify", href: "/dashboard/shopify", icon: Store },
    { name: "Indstillinger", href: "/dashboard/settings", icon: Settings },
  ];

  function isActive(href: string): boolean {
    if (href === "/dashboard") return pathname === "/dashboard";
    if (href === "/dashboard/settings")
      return pathname === "/dashboard/settings";
    return pathname.startsWith(href);
  }

  /* ─── Breadcrumb ─── */

  function getBreadcrumb(): string {
    if (pathname === "/dashboard") return "Oversigt";
    if (pathname === "/dashboard/import") return "Ny import";
    if (pathname.startsWith("/dashboard/history")) return "Historik";
    if (pathname.startsWith("/dashboard/settings/brands")) return "Brands";
    if (pathname.startsWith("/dashboard/seo")) return "SEO Keywords";
    if (pathname === "/dashboard/shopify") return "Shopify";
    if (pathname === "/dashboard/settings") return "Indstillinger";
    return "";
  }

  /* ─── Render nav item ─── */

  function NavLink({ item }: { item: NavItem }) {
    const active = isActive(item.href);
    return (
      <Link
        href={item.href}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "7px 10px",
          borderRadius: "var(--radius-md)",
          fontSize: "13px",
          fontWeight: active ? 500 : 400,
          color: active ? "var(--accent-text)" : "var(--text-secondary)",
          background: active ? "var(--accent-light)" : "transparent",
          transition: "all 0.12s ease",
        }}
        onMouseEnter={(e) => {
          if (!active) {
            e.currentTarget.style.background = "var(--bg-hover)";
            e.currentTarget.style.color = "var(--text-primary)";
          }
        }}
        onMouseLeave={(e) => {
          if (!active) {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--text-secondary)";
          }
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <item.icon
            style={{
              width: 16,
              height: 16,
              color: active ? "var(--accent)" : "var(--text-tertiary)",
            }}
          />
          {item.name}
        </span>
        {item.badge != null && item.badge > 0 && (
          <span
            style={{
              borderRadius: "var(--radius-sm)",
              padding: "1px 7px",
              fontSize: "11px",
              fontWeight: 500,
              background: active ? "var(--accent)" : "var(--bg-subdued)",
              color: active ? "#fff" : "var(--text-tertiary)",
            }}
          >
            {item.badge}
          </span>
        )}
      </Link>
    );
  }

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "var(--bg-tertiary)" }}>
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 30,
            background: "rgba(0,0,0,0.3)",
          }}
          className="md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ═══ Sidebar ═══ */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-40 flex w-[220px] flex-col transition-transform md:static md:translate-x-0
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
        style={{
          background: "var(--bg-primary)",
          borderRight: "1px solid var(--border-secondary)",
        }}
      >
        {/* Logo area */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 56,
            padding: "0 16px",
            borderBottom: "1px solid var(--border-secondary)",
          }}
        >
          <Link href="/dashboard" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                fontSize: 15,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
              }}
            >
              STRØM
            </span>
            <span
              style={{
                fontSize: 11,
                fontWeight: 500,
                color: "var(--text-tertiary)",
                background: "var(--bg-subdued)",
                padding: "2px 6px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Import
            </span>
          </Link>
          <button
            className="rounded p-1 md:hidden"
            style={{ color: "var(--text-tertiary)" }}
            onClick={() => setSidebarOpen(false)}
          >
            <X style={{ width: 16, height: 16 }} />
          </button>
        </div>

        {/* Main nav */}
        <nav style={{ flex: 1, padding: "12px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          {mainNav.map((item) => (
            <NavLink key={item.href} item={item} />
          ))}

          {/* Settings group */}
          <p
            style={{
              padding: "20px 10px 6px",
              fontSize: 11,
              fontWeight: 500,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-tertiary)",
            }}
          >
            Indstillinger
          </p>
          {settingsNav.map((item) => (
            <NavLink key={item.href} item={item} />
          ))}
        </nav>

        {/* Shopify connection status */}
        <div style={{ padding: "0 12px 12px" }}>
          <div
            style={{
              borderRadius: "var(--radius-md)",
              padding: "10px 12px",
              background: shopifyOk ? "var(--success-light)" : "var(--bg-subdued)",
              borderTop: `1px solid ${shopifyOk ? "var(--success)" : "var(--border-primary)"}`,
              borderRight: `1px solid ${shopifyOk ? "var(--success)" : "var(--border-primary)"}`,
              borderBottom: `1px solid ${shopifyOk ? "var(--success)" : "var(--border-primary)"}`,
              borderLeft: `3px solid ${shopifyOk ? "var(--success)" : "var(--border-primary)"}`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Store style={{ width: 14, height: 14, color: shopifyOk ? "var(--success)" : "var(--text-tertiary)" }} />
              <span style={{ fontSize: 12, fontWeight: 500, color: shopifyOk ? "var(--success-text)" : "var(--text-secondary)" }}>
                {shopifyOk ? "Forbundet" : "Ikke forbundet"}
              </span>
            </div>
            {shopifyOk && shopDomain && (
              <p style={{ marginTop: 2, marginLeft: 20, fontSize: 11, color: "var(--text-tertiary)" }}>
                {shopDomain}
              </p>
            )}
          </div>
        </div>

        {/* User */}
        <div
          style={{
            padding: "10px 14px",
            borderTop: "1px solid var(--border-secondary)",
          }}
        >
          <UserButton
            appearance={{
              elements: {
                rootBox: "w-full",
                userButtonTrigger: "w-full justify-start",
              },
            }}
            showName
          />
        </div>
      </aside>

      {/* ═══ Main area ═══ */}
      <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
        {/* Top bar */}
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 56,
            padding: "0 24px",
            background: "var(--bg-primary)",
            borderBottom: "1px solid var(--border-secondary)",
            flexShrink: 0,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {/* Mobile menu */}
            <button
              onClick={() => setSidebarOpen(true)}
              className="md:hidden"
              style={{
                padding: 6,
                borderRadius: "var(--radius-sm)",
                color: "var(--text-secondary)",
              }}
            >
              <Menu style={{ width: 20, height: 20 }} />
            </button>
            {/* Breadcrumb */}
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
              <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}>STRØM</span>
              <ChevronRight style={{ width: 14, height: 14, color: "var(--text-disabled)" }} />
              <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{getBreadcrumb()}</span>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Link
              href="/dashboard/import"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                borderRadius: "var(--radius-md)",
                padding: "7px 14px",
                fontSize: 13,
                fontWeight: 500,
                color: "#fff",
                background: "var(--accent)",
                boxShadow: "0 1px 0 rgba(0,0,0,0.08), inset 0 -1px 0 rgba(0,0,0,0.15)",
                transition: "all 0.15s ease",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--accent-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "var(--accent)")}
            >
              <FileUp style={{ width: 14, height: 14 }} />
              Ny import
            </Link>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: "var(--radius-full)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 600,
                background: "var(--bg-subdued)",
                color: "var(--text-secondary)",
                border: "1px solid var(--border-secondary)",
              }}
            >
              {firstName.charAt(0).toUpperCase()}
            </div>
          </div>
        </header>

        {/* Content */}
        <main style={{ flex: 1, overflowY: "auto" }}>
          {/* Greeting bar (only on dashboard) */}
          {pathname === "/dashboard" && (
            <div style={{ padding: "20px 24px 0" }}>
              <p style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
                {getGreeting()}, {firstName} · {formatDanishDate()}
              </p>
            </div>
          )}
          <div style={{ padding: "20px 24px 24px" }}>
              <ErrorBoundary>{children}</ErrorBoundary>
            </div>
        </main>
      </div>

      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: "var(--bg-primary)",
            color: "var(--text-primary)",
            border: "1px solid var(--border-primary)",
            boxShadow: "var(--shadow-lg)",
            borderRadius: "var(--radius-md)",
          },
        }}
      />
    </div>
  );
}

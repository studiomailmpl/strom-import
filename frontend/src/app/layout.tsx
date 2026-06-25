import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: {
    default: "STRØM Import",
    template: "%s — STRØM Import",
  },
  description: "Importér modeprodukter fra PDF-fakturaer direkte til Shopify. AI-drevet parsing, billedsøgning og produktoprettelse.",
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL || "https://app.stromimport.dk"),
  openGraph: {
    title: "STRØM Import",
    description: "AI-drevet produktimport fra PDF til Shopify for modebranchen.",
    siteName: "STRØM Import",
    locale: "da_DK",
    type: "website",
  },
  robots: {
    index: false, // SaaS admin UI — don't index
    follow: false,
  },
  icons: {
    icon: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <ClerkProvider appearance={{ baseTheme: undefined }}>
      <html lang="da" className={`${inter.variable} h-full`} style={{ colorScheme: "light" }} suppressHydrationWarning>
        <body className="min-h-full font-sans">{children}</body>
      </html>
    </ClerkProvider>
  );
}

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output for Docker deployment (produces self-contained server.js)
  output: "standalone",

  // Allow Shopify CDN images in next/image
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "cdn.shopify.com" },
    ],
  },

  // Disable X-Powered-By header for security
  poweredByHeader: false,
};

export default nextConfig;

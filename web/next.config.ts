import type { NextConfig } from "next";

// Every page is per-user (Supabase session cookie + database RLS), so there
// is no static shell worth prerendering: Cache Components / partial
// prefetching are left off and routes render dynamically on each request.
const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

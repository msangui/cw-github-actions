import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The panel is a tool for a handful of operators; nothing here is worth prerendering.
  reactStrictMode: true,
  // The local-dev file store writes under .data/; keep it out of the server bundle tracing.
  outputFileTracingExcludes: { "*": [".data/**"] },
};

export default nextConfig;

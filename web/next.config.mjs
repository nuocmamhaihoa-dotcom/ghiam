/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  agentRules: false,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;

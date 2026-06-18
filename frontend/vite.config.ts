import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 网关地址：开发期前端与网关多为不同源。
// - 设了 VITE_AGENT_NETWORK_API_BASE：客户端直连该绝对地址（生产/一体化部署用）。
// - 未设：客户端走同源相对路径，由下面的 dev proxy 把 /api/* 反代到网关，
//   反代目标用 VITE_GATEWAY_PROXY 覆盖（默认本机网关 8000，见 backend/scripts/run_gateway.py）。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const gatewayProxy = env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8000";
  const hasAbsoluteBase = Boolean(env.VITE_AGENT_NETWORK_API_BASE);
  const proxy = hasAbsoluteBase
    ? undefined
    : { "/api": { target: gatewayProxy, changeOrigin: true } };

  return {
    plugins: [react()],
    server: { host: "0.0.0.0", port: 18180, proxy },
    preview: { host: "0.0.0.0", port: 18181, proxy },
  };
});

module.exports = {
  apps: [
    {
      name: "callcraft",
      cwd: "/var/www/callcraft",
      script: "node_modules/next/dist/bin/next",
      args: "start -H 0.0.0.0 -p 3000",
      instances: 1,
      exec_mode: "fork",
      env: {
        NODE_ENV: "production",
        PORT: "3000",
      },
    },
    {
      name: "ity-auto",
      cwd: "/var/www/callcraft",
      script: "scripts/ity-auto-worker.mjs",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 50,
      restart_delay: 5000,
      env: {
        NODE_ENV: "production",
        CALLCRAFT_BASE_URL: "http://127.0.0.1:3000",
        ITY_AUTO_INTERVAL_SEC: "45",
      },
    },
  ],
};

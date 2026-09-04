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
  ],
};

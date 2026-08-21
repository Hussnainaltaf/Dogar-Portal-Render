// PM2 daemon config for the Dogar Trading Corporation Portal.
// Built by HussnainTechVertex Pvt Ltd.
//
// Usage:
//   pm2 start ecosystem.config.cjs
//   pm2 logs dogar-portal --nostream
//   pm2 restart dogar-portal --update-env
//   pm2 delete dogar-portal
//
// FIX-06: Added --forwarded-allow-ips 127.0.0.1 to match the Dockerfile.
// Without this flag uvicorn trusts ANY X-Forwarded-For header, allowing
// an attacker to spoof their IP and bypass the brute-force rate limiter.
// Only trust X-Forwarded-For from the local reverse-proxy (127.0.0.1).
// If your nginx/proxy runs on a different IP, update this value accordingly.
//
// M-4 note (audit 2026-07-03): this config correctly targets uvicorn (this is
// a FastAPI project — there is no src/index.js). The script path assumes a
// local virtualenv at ./venv. On environments without a venv (e.g. hosts
// where packages are installed globally by a system toolchain), change
// `script` to just 'uvicorn' — or skip PM2 entirely and launch uvicorn
// directly.
module.exports = {
    apps: [{
        name: 'dogar-portal',
        script: './venv/bin/uvicorn',
        args: 'app.main:app --host 0.0.0.0 --port 3000 --workers 1 --forwarded-allow-ips 127.0.0.1',
        cwd: '.',
        interpreter: 'none',
        env: {
            PYTHONUNBUFFERED: '1',
            PYTHONPATH: '.',
            ENV: 'production'
        },
        watch: false,
        instances: 1,
        exec_mode: 'fork',
        autorestart: true,
        max_restarts: 10,
        max_memory_restart: '512M'
    }]
};

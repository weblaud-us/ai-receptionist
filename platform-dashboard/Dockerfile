# Platform admin app image: static build served by an unprivileged Nginx with the security
# headers from Task 3.8. The frontends are npm workspaces, so build from the REPO ROOT:
#
#   docker build -f platform-dashboard/Dockerfile -t receptionist-platform-dashboard \
#     --build-arg VITE_API_URL=https://api.example.com \
#     --build-arg VITE_CLIENT_URL=https://app.example.com \
#     --build-arg VITE_PLATFORM_URL=https://admin.example.com \
#     --build-arg VITE_APP_ENV=production .
#   docker run -p 8080:8080 -e CSP_CONNECT_SRC="https://api.example.com wss://your-project.livekit.cloud" \
#     receptionist-platform-dashboard
#
# Vite bakes VITE_* in at build time, so build once per environment.
#
# Serve it on its own origin (e.g. admin.example.com). Put an IP allow-list or SSO proxy in front
# too, on top of the platform-admin + two-factor checks the API already enforces.

FROM node:26-alpine AS build
WORKDIR /repo
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY platform-dashboard/package.json platform-dashboard/
RUN npm ci -w platform-dashboard --include-workspace-root
COPY packages/shared packages/shared
COPY platform-dashboard platform-dashboard
ARG VITE_API_URL
ARG VITE_CLIENT_URL
ARG VITE_PLATFORM_URL
ARG VITE_APP_ENV=production
ARG VITE_SENTRY_DSN=""
ARG VITE_GIT_SHA=""
ENV VITE_API_URL=$VITE_API_URL VITE_CLIENT_URL=$VITE_CLIENT_URL VITE_PLATFORM_URL=$VITE_PLATFORM_URL
ENV VITE_APP_ENV=$VITE_APP_ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN VITE_GIT_SHA=$VITE_GIT_SHA
RUN test -n "$VITE_API_URL" || (echo "VITE_API_URL build arg is required" >&2; exit 1)
RUN test -n "$VITE_CLIENT_URL" || (echo "VITE_CLIENT_URL build arg is required" >&2; exit 1)
RUN test -n "$VITE_PLATFORM_URL" || (echo "VITE_PLATFORM_URL build arg is required" >&2; exit 1)
RUN npm run build -w platform-dashboard

FROM nginxinc/nginx-unprivileged:1.27-alpine
# Origins the app may call: the API, the LiveKit wss:// URL (the sales dialer listens in and takes
# over calls in the browser) and the Sentry ingest host if VITE_SENTRY_DSN is set.
ENV CSP_CONNECT_SRC=""
COPY platform-dashboard/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/platform-dashboard/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1

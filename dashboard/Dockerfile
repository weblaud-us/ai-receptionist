# Client dashboard image: static build served by an unprivileged Nginx with the security headers
# from Task 3.8. The frontends are npm workspaces, so build from the REPO ROOT:
#
#   docker build -f dashboard/Dockerfile -t receptionist-dashboard \
#     --build-arg VITE_API_URL=https://api.example.com \
#     --build-arg VITE_CLIENT_URL=https://app.example.com \
#     --build-arg VITE_PLATFORM_URL=https://admin.example.com \
#     --build-arg VITE_APP_ENV=production .
#   docker run -p 8080:8080 -e CSP_CONNECT_SRC="https://api.example.com" receptionist-dashboard
#
# Vite bakes VITE_* in at build time, so build once per environment.
#
# Hosting it on Cloudflare Pages / Netlify instead? `npm run build -w dashboard` and deploy
# dashboard/dist; the same headers ship in public/_headers (edit the API origin there).

FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build
WORKDIR /repo
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY dashboard/package.json dashboard/
RUN npm ci -w dashboard --include-workspace-root
COPY packages/shared packages/shared
COPY dashboard dashboard
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
RUN npm run build -w dashboard

FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
# Origins the dashboard may call: the API, plus the Sentry ingest host if VITE_SENTRY_DSN is set.
ENV CSP_CONNECT_SRC=""
COPY dashboard/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/dashboard/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1

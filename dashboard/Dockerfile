# Dashboard image (Roadmap Task 3.3): static build served by an unprivileged Nginx with the
# security headers from Task 3.8. Vite bakes VITE_* in at build time, so build once per environment:
#
#   docker build -t receptionist-dashboard \
#     --build-arg VITE_API_URL=https://api.example.com --build-arg VITE_APP_ENV=production .
#   docker run -p 8080:8080 -e CSP_CONNECT_SRC="https://api.example.com" receptionist-dashboard
#
# Hosting it on Cloudflare Pages / Netlify instead? `npm run build` and deploy dist/ — the same
# headers ship in public/_headers (edit the API origin there).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL
ARG VITE_APP_ENV=production
ARG VITE_SENTRY_DSN=""
ARG VITE_GIT_SHA=""
ENV VITE_API_URL=$VITE_API_URL VITE_APP_ENV=$VITE_APP_ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN VITE_GIT_SHA=$VITE_GIT_SHA
RUN test -n "$VITE_API_URL" || (echo "VITE_API_URL build arg is required" >&2; exit 1)
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
# Origins the dashboard may call: the API, plus the Sentry ingest host if VITE_SENTRY_DSN is set.
ENV CSP_CONNECT_SRC=""
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1

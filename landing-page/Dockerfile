# Public landing page: static Astro build served by an unprivileged Nginx with the same security
# headers as the dashboards. The frontends are npm workspaces, so build from the REPO ROOT:
#
#   docker build -f landing-page/Dockerfile -t receptionist-landing \
#     --build-arg VITE_SITE_URL=https://www.example.com \
#     --build-arg VITE_DASHBOARD_SIGNUP_URL=https://app.example.com/signup \
#     --build-arg VITE_DASHBOARD_LOGIN_URL=https://app.example.com/login .
#   docker run -p 8080:8080 receptionist-landing
#
# VITE_* values are baked in at build time (see landing-page/.env.example), so build once per
# environment. Hosting on Cloudflare Pages / Netlify instead? `npm run build -w landing-page` and
# deploy landing-page/dist with the headers from nginx.conf.template.

FROM node:22-alpine AS build
WORKDIR /repo
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY landing-page/package.json landing-page/
RUN npm ci -w landing-page --include-workspace-root
COPY landing-page landing-page
ARG VITE_SITE_URL
ARG VITE_DASHBOARD_SIGNUP_URL
ARG VITE_DASHBOARD_LOGIN_URL
ARG VITE_DEMO_CALL_URL=""
ARG VITE_BOOK_DEMO_URL=""
ARG VITE_DEMO_PHONE_AU=""
ARG VITE_DEMO_PHONE_UK=""
ARG VITE_PRIVACY_URL=""
ARG VITE_TERMS_URL=""
ARG VITE_DPA_URL=""
ARG VITE_CLAIM_ZDR=false
ENV VITE_SITE_URL=$VITE_SITE_URL VITE_DASHBOARD_SIGNUP_URL=$VITE_DASHBOARD_SIGNUP_URL VITE_DASHBOARD_LOGIN_URL=$VITE_DASHBOARD_LOGIN_URL
ENV VITE_DEMO_CALL_URL=$VITE_DEMO_CALL_URL VITE_BOOK_DEMO_URL=$VITE_BOOK_DEMO_URL
ENV VITE_DEMO_PHONE_AU=$VITE_DEMO_PHONE_AU VITE_DEMO_PHONE_UK=$VITE_DEMO_PHONE_UK
ENV VITE_PRIVACY_URL=$VITE_PRIVACY_URL VITE_TERMS_URL=$VITE_TERMS_URL VITE_DPA_URL=$VITE_DPA_URL VITE_CLAIM_ZDR=$VITE_CLAIM_ZDR
ENV ASTRO_TELEMETRY_DISABLED=1
RUN test -n "$VITE_SITE_URL" || (echo "VITE_SITE_URL build arg is required" >&2; exit 1)
RUN test -n "$VITE_DASHBOARD_SIGNUP_URL" || (echo "VITE_DASHBOARD_SIGNUP_URL build arg is required" >&2; exit 1)
RUN test -n "$VITE_DASHBOARD_LOGIN_URL" || (echo "VITE_DASHBOARD_LOGIN_URL build arg is required" >&2; exit 1)
RUN npm run build -w landing-page

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY landing-page/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/landing-page/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1

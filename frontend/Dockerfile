ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE} AS builder

USER root
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts

COPY frontend/ ./
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine AS runtime

USER root
RUN apk add --no-cache tzdata

COPY infra/gateway/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder /app/dist /usr/share/nginx/html

USER nginx
EXPOSE 8080
STOPSIGNAL SIGQUIT

HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=10s \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/nginx-health || exit 1

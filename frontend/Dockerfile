# Admin Console image: build the Vite SPA, serve it with nginx.
# Multi-stage so the final image carries only static assets + nginx.

# ---- build stage ----------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# install deps first for layer caching
COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build

# ---- serve stage ----------------------------------------------------------
FROM nginx:1.27-alpine AS serve

# nginx official image runs envsubst over /etc/nginx/templates/*.template at
# startup, writing the result to /etc/nginx/conf.d/. PROXY_UPSTREAM is the
# host:port of the proxy admin API (default proxy:8080), overridable per env.
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 makes the image export
# NGINX_LOCAL_RESOLVERS (from /etc/resolv.conf) so the template's `resolver`
# directive is filled in — enabling request-time DNS for the upstream.
ENV PROXY_UPSTREAM=proxy:8080 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
# nginx:alpine has a default CMD; the entrypoint handles template substitution.

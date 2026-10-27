FROM node:24.22.1-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY public ./public
COPY src ./src
RUN npm run build

# Runs as uid 101 and listens on 8080. Its default config serves /usr/share/nginx/html.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine

# Security fixes released since the base image was built.
USER 0
RUN apk upgrade --no-cache
USER 101

COPY --from=build /app/build /usr/share/nginx/html

FROM node:24.22.1-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY public ./public
COPY src ./src
RUN npm run build

# Runs as uid 101 and listens on 8080. Its default config serves /usr/share/nginx/html.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine

COPY --from=build /app/build /usr/share/nginx/html

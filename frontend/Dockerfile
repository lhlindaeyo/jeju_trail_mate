# 운영용 웹 이미지: 프런트를 빌드해 Caddy가 서빙하고, /v1·/health 는 backend 컨테이너로 넘깁니다(Caddyfile).
# 빌드 컨텍스트는 frontend/ 입니다(docker-compose.prod.yml 참고).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv

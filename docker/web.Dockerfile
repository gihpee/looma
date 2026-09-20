# Три сборки — лендинг, консоль, админка — и nginx, который по Host решает,
# какую отдать. Node нужен только здесь: ни в образе оркестратора, ни на
# узлах его нет.

FROM node:22-alpine AS build
WORKDIR /web
# Сначала манифесты всех workspace-пакетов: слой с зависимостями переживает
# правку исходников. Manifest-ы копируются по одному, потому что COPY не
# умеет «только package.json из каждой папки».
COPY web/package.json web/package-lock.json* ./
COPY web/packages/ui/package.json packages/ui/
COPY web/packages/api/package.json packages/api/
COPY web/apps/landing/package.json apps/landing/
COPY web/apps/console/package.json apps/console/
COPY web/apps/admin/package.json apps/admin/
RUN npm ci --no-audit --no-fund
COPY web/tsconfig.base.json ./
COPY web/packages ./packages
COPY web/apps ./apps
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /web/apps/landing/dist /usr/share/nginx/html/landing
COPY --from=build /web/apps/console/dist /usr/share/nginx/html/console
COPY --from=build /web/apps/admin/dist   /usr/share/nginx/html/admin
# nginx:alpine подставляет переменные окружения в шаблоны при старте, так что
# порты и домен берутся из .env и не дублируются в двух местах.
COPY web/nginx/default.conf.template /etc/nginx/templates/default.conf.template
# 127.0.0.1 верно при host-сети, которой compose и пользуется. Вынесено
# переменной, чтобы образ годился и при обычной сети docker.
ENV LOOMA_WEB_PORT=8080 \
    LOOMA_HTTP_PORT=8000 \
    LOOMA_API_HOST=127.0.0.1 \
    LOOMA_DOMAIN=localhost

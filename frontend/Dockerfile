# 前端镜像：Node 构建 → nginx 托管静态文件（多阶段构建，最终镜像不含 node_modules）
FROM node:20-alpine AS build
WORKDIR /app

# 先只拷 lockfile 装依赖，改业务代码不触发重装
COPY package.json package-lock.json* ./
RUN npm ci || npm install

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

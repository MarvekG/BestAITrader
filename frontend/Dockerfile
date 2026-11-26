FROM node:alpine
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories \
    && apk add --no-cache tzdata

WORKDIR /app

# 设置权限
RUN chown -R node:node /app

USER node

COPY --chown=node:node package*.json ./
RUN npm install

COPY --chown=node:node . .

# 通过 backend-locales 构建上下文注入后端语言包，直接覆盖前端静态语言包。
# 仓库根目录手动构建：
# docker build --build-context backend-locales=./backend/app/locales -f frontend/Dockerfile frontend
COPY --from=backend-locales --chown=node:node zh.json en.json ./public/locales/
RUN npm run build

# 使用 5173 端口
EXPOSE 5173

# 开发容器中使用 Vite dev server，保留热重载能力
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

FROM node:22-alpine
WORKDIR /app
COPY billing-gateway/package*.json ./
RUN npm install --omit=dev
COPY billing-gateway/server.mjs ./
COPY billing/cloudpayments.mjs /billing/cloudpayments.mjs
ENV NODE_ENV=production PORT=8080
EXPOSE 8080
CMD ["node","server.mjs"]

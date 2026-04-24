FROM node:20-alpine

WORKDIR /app/frontend

COPY frontend/package.json ./
COPY frontend/package-lock.json* ./
RUN npm install

COPY frontend ./
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

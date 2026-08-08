# Single-image build: compiles the frontend, then serves it from FastAPI.
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY config/ config/
COPY --from=frontend /app/frontend/dist frontend/dist
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "backend.app.main"]

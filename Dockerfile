# Sodexo Healthcare - Labor Forecasting & Workforce Intelligence
#
# One image, three processes, one public port - the same shape the app has
# locally, so there is no CORS and no cross-service networking to configure:
#
#   :80    nginx  - serves the built frontend, reverse-proxies /api -> :8300
#   :8300  backend application API (uploads, forecasting, auth, assistant)
#
# Python is pinned to 3.12 to match the environment this was built and tested
# against, and because tensorflow-cpu 2.17 / pmdarima 2.0.4 publish wheels for
# it - building those from source would make this image take an hour.

# --- Stage 1: build the frontend -------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend_sodexo
COPY frontend_sodexo/package*.json ./
RUN npm ci
COPY frontend_sodexo/ ./
RUN npm run build

# --- Stage 2: python dependencies ------------------------------------------
# Kept separate so the (slow, ~2GB) ML dependency layer is cached and not
# rebuilt every time application code changes.
FROM python:3.12-slim AS pydeps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY backend_sodexo/requirements.txt /tmp/backend-requirements.txt
RUN pip install --prefix=/install -r /tmp/backend-requirements.txt

# --- Stage 3: runtime -------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx supervisor curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pydeps /install /usr/local

# Force the numpy the compiled wheels were built against. pmdarima 2.0.4 and
# tensorflow-cpu 2.17 are built for the numpy 1.x ABI; anything 2.x makes
# auto_arima fail with "numpy.dtype size changed" and lstm with
# "object __array__ method not producing an array". The assert runs HERE, in the
# runtime image where numpy is actually importable, so it verifies the real
# thing and fails the build if this ever regresses.
RUN pip install --no-cache-dir --force-reinstall "numpy==1.26.4"     && python -c "import numpy; assert numpy.__version__ == '1.26.4', numpy.__version__; print('numpy pinned:', numpy.__version__)"

WORKDIR /app
COPY backend_sodexo/ /app/backend_sodexo/
COPY scripts/ /app/scripts/
# No data ships in the image. The app starts empty and wipes itself on every
# restart (RESET_ON_STARTUP), so the client only ever sees the file they
# uploaded themselves. The directory is created so the first upload has
# somewhere to land.
RUN mkdir -p /app/data/uploads
COPY --from=frontend /app/frontend_sodexo/dist/ /usr/share/nginx/html/

COPY deploy/nginx.conf /etc/nginx/sites-available/default
COPY deploy/supervisord.conf /etc/supervisor/conf.d/sodexo.conf

# The backend resolves DATABASE_URL's ./data relative to the project root,
# which is this WORKDIR.
ENV DATABASE_URL=sqlite:///./data/sodexo.db

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:80/health || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf", "-n"]

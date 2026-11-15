# HisaabAI bot, ready for any host that runs Docker (Render, Railway, Fly.io, a VM...).
# Build:  docker build -t hisaabai .
# Run:    docker run --env-file .env -p 8000:8000 hisaabai
# Settings come from the host's environment variables (same names as .env.example); .env is never copied in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Fewer malloc arenas: in a load test (25 photos/PDFs at once) peak memory fell from 667 MB to ~400 MB,
# which keeps the bot inside Render free's 512 MB (docs/13 Build log, 29 Oct).
ENV MALLOC_ARENA_MAX=2
WORKDIR /app

COPY requirements-bot.txt requirements-ocr-bot.txt ./
RUN pip install --no-cache-dir -r requirements-bot.txt

# Free OCR check (read-policy checks a and b): RapidOCR reads the photo, Tesseract only turns sideways photos.
# Adds about 360 MB to the image (packages +330, Tesseract +30) and needs a host with >= 1 GB RAM (docs/11_setup_and_hosting.md).
# Small host? Build without it: docker build --build-arg OCR=0 -t hisaabai .  (checks a and b are then skipped)
ARG OCR=1
RUN if [ "$OCR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
      && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir -r requirements-ocr-bot.txt \
      && pip uninstall -y -q sympy mpmath; \
    fi
# (sympy comes with onnxruntime but is never used to run a model: -74 MB)

COPY bot/ bot/
COPY db/ db/

# Run as a normal user, not root
RUN useradd --create-home hisaab && chown -R hisaab /app
USER hisaab

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.getenv(\"PORT\",\"8000\")}/health', timeout=4)"
CMD ["sh", "-c", "uvicorn bot.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

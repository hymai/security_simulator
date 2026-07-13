# Certus — fully local operational readiness platform.
# CPU-only image; the model runs in the ollama sidecar (see docker-compose.yml)
# or on any OpenAI-compatible endpoint via CERTUS_OPENAI_BASE_URL.
FROM python:3.12-slim

WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # BGE-M3 downloads (~2 GB) land here — mounted as a volume in compose so
    # the download happens once, not on every container rebuild.
    HF_HOME=/data/hf

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
COPY profiles ./profiles

EXPOSE 8501
CMD ["streamlit", "run", "certus.py", \
     "--server.headless=true", "--server.address=0.0.0.0", \
     "--server.fileWatcherType=none"]

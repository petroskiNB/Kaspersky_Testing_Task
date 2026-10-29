FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

WORKDIR /app
COPY downloader/requirements.txt /app/requirements.txt
COPY scripts/check_dependencies.py /app/check_dependencies.py
RUN python -m pip install --no-cache-dir --no-deps --only-binary=:all: -r /app/requirements.txt \
    && python -m pip check \
    && python /app/check_dependencies.py /app/requirements.txt --allow-extra pip setuptools wheel \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app \
    && mkdir -p /models \
    && chown 10001:10001 /models

COPY downloader/download_model.py /app/download_model.py
ENV HOME=/home/app \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/home/app/.cache/huggingface \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_VERBOSITY=error \
    MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct \
    MODEL_DIR=/models/qwen

USER 10001:10001
ENTRYPOINT ["python", "/app/download_model.py"]

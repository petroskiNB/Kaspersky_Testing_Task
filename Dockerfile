# CPU server with metadata-only Python logging.
FROM vllm/vllm-openai-cpu:v0.27.0-x86_64@sha256:ad2d073b2ff1b755e0d0b9e33a581bfcbadde22c13a073af1dfff1d553119d38

USER root
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app \
    && mkdir -p /app /home/app/.cache \
    && chown -R 10001:10001 /app /home/app

WORKDIR /app
# vLLM and its dependencies are already installed in the CPU base image.
# This file records all Python package versions from that environment.
COPY requirements.txt /app/requirements.txt
COPY scripts/check_dependencies.py /app/check_dependencies.py
RUN python /app/check_dependencies.py /app/requirements.txt \
    && python -m pip check
COPY app /app/app
COPY config /app/config

ENV HOME=/home/app \
    PYTHONPATH=/app \
    VLLM_LOGGING_CONFIG_PATH=/app/config/logging.json \
    HF_HOME=/home/app/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    VLLM_CPU_KVCACHE_SPACE=1 \
    VLLM_CPU_OMP_THREADS_BIND=nobind \
    OMP_NUM_THREADS=4

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["vllm", "serve"]
CMD ["/models/qwen", "--served-model-name", "qwen", "--host", "0.0.0.0", "--port", "8000", "--dtype", "bfloat16", "--max-model-len", "2048", "--max-num-seqs", "1", "--enforce-eager", "--no-enable-log-requests", "--no-enable-log-outputs", "--disable-uvicorn-access-log", "--log-config-file", "/app/config/logging.json", "--middleware", "app.request_logging.RequestMetadataMiddleware"]

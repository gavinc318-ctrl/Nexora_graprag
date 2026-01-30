FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y \
    python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install -U pip
RUN pip3 install -U \
    "vllm>=0.11.0" \
    "transformers>=4.45" \
    "accelerate" \
    "pillow"  \
    "qwen-vl-utils==0.0.14" 

EXPOSE 8000
ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]

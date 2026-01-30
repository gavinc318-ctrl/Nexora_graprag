# GPU OCR Docker (CUDA 12.x)

适配宿主机 CUDA Driver = 12.4。

说明：
- NVIDIA 驱动 **向后兼容**，CUDA 12.4 Driver 可运行 CUDA 12.1 Runtime
- 本镜像使用 Paddle 官方：
  paddlepaddle/paddle:3.2.2-gpu-cuda12.1-cudnn9.1

## 启动
```bash
docker compose -f docker-compose.gpu.yml up -d --build
```

## 验证
```bash
docker exec -it paddle_ocr_service_gpu_cuda12 nvidia-smi
```

FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码（保持 yaobi_radar.py 在正确路径）
COPY yaobi_radar.py .
COPY api/ api/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

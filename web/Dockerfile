FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY web/requirements.txt .
RUN pip install -i https://mirrors.aliyun.com/pypi/simple --no-cache-dir -r requirements.txt

# Copy app code
COPY web/ .
# Copy shared simulator (single source for web and standalone)
COPY shared/simulator static/simulator

# Expose Flask port
EXPOSE 8085

# Unbuffer stdout for docker logs
ENV PYTHONUNBUFFERED=1
# Suppress TensorFlow verbose logs (3=errors only)
ENV TF_CPP_MIN_LOG_LEVEL=3

# Run the server
CMD ["python", "app.py"]

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY radar.py feishu.py regional.py regions.json scheduler.py dashboard.html config.json ./
RUN mkdir -p /app/data && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 8765
CMD ["python", "radar.py", "serve", "--host", "0.0.0.0"]

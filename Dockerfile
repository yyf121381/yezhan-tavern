FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home tavern && mkdir /data && chown tavern:tavern /data
COPY server.py model_adapter.py ./
COPY static ./static
USER tavern
ENV TAVERN_DB=/data/tavern.sqlite3
VOLUME ["/data"]
EXPOSE 8080
CMD ["python","-c","from waitress import serve; from server import app, initialize; initialize(); serve(app, host='0.0.0.0', port=8080, threads=12)"]

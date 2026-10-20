FROM python:3.12-slim
WORKDIR /srv
COPY apps/api /srv
RUN pip install --no-cache-dir -r requirements.txt
ENV SANDBOX_DB=/data/sandbox.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

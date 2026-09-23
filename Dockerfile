FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY app ./app
COPY static ./static
COPY seed ./seed
COPY run.py backup_db.py ./
RUN mkdir /app/data && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["python", "run.py"]

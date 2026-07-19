FROM python:3.12-slim

WORKDIR /srv/anota
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .
COPY app ./app
COPY static ./static
COPY data ./data

EXPOSE 8420
VOLUME ["/data"]

ENTRYPOINT ["python", "run.py", "--host", "0.0.0.0"]
CMD ["--db", "/data/anota.db"]
# demo mode instead:  docker run -p 8420:8420 anota --demo

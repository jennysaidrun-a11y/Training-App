# The training app on Fly.io (see fly.toml and fly/start.sh).
# The app's code is not baked in: on start the machine keeps its own copy of the
# GitHub repo on the /data volume and updates itself every minute, like the PC.
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git procps psmisc ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install -q --no-cache-dir -r /tmp/requirements.txt
COPY fly/start.sh /start.sh
CMD ["bash", "/start.sh"]

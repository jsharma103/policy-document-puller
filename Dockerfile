# Reproducible boot for the policy-document puller.
#
# Playwright's Python image ships the browser system dependencies already; we
# add Xvfb (State Farm runs headful — see README) and patchright's stealth
# Chromium on top.
#
# Build:  docker build -t policy-puller .
# Run:    docker run --rm -p 8000:8000 \
#           -e SOAX_USER=... -e SOAX_PASS=... policy-puller
#         (SOAX_* only needed for State Farm from a datacenter IP; Lemonade
#          works with no env. Carrier credentials are entered in the UI at
#          runtime and never baked into the image.)
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

# Xvfb: State Farm is headful, so the server runs under a virtual display.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && patchright install chromium

COPY app ./app
COPY frontend ./frontend

EXPOSE 8000

# Whole server under a virtual display (State Farm headful; Lemonade headless
# ignores it). Use exec form so signals reach uvicorn.
CMD ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", \
     "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# VeriKöprü - YKB OpenShift imajı: kurum Python 3.11 + Oracle Client taban imajı, ön yüz önceden derlenmiş
# (prebuilt/frontend-dist), kümede Node/npm gerekmez. Taban imaj registrypullsecret ile çekilir (build.yaml).
# Ön yüz değişince prebuilt'i yenileyin:
#   cd frontend; pnpm build; cd ..; Remove-Item -Recurse prebuilt\frontend-dist; Copy-Item -Recurse frontend\dist prebuilt\frontend-dist
# Node aşamalı genel imaj: Containerfile.
FROM sdlc.yapikredi.com.tr/internal-docker-ops/ykb/base-images/python:3.11-oracle-client.29
USER 0
# İsteğe bağlı iç PyPI aynası (simple index adresi); boşsa taban imajın pip ayarı ya da pypi.org.
ARG PIP_MIRROR=
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TT_ENV=openshift \
    TT_FRONTEND_DIST=/app/frontend/dist \
    TT_SQLITE_PATH=/data/verikopru.db

# Uygulama Python >= 3.11 ister; taban imajdaki python3 daha eskiyse burada açık hatayla durur.
RUN python3 -c "import sys; assert sys.version_info >= (3, 11), 'Python >= 3.11 gerekli: ' + sys.version"

COPY backend/requirements.txt backend/requirements.lock* /tmp/req/
RUN if [ -n "$PIP_MIRROR" ]; then export PIP_INDEX_URL="$PIP_MIRROR"; fi; \
    if [ -f /tmp/req/requirements.lock ]; then \
        python3 -m pip install --no-cache-dir -r /tmp/req/requirements.lock; \
    else \
        python3 -m pip install --no-cache-dir -r /tmp/req/requirements.txt; \
    fi && rm -rf /tmp/req

WORKDIR /app/backend
COPY backend/app ./app
COPY prebuilt/frontend-dist /app/frontend/dist

# OpenShift rastgele UID atar (grup 0): /app ve /data grup yazılabilir olmalı.
RUN mkdir -p /data \
    && chgrp -R 0 /app /data \
    && chmod -R g=u /app /data

USER 1001
EXPOSE 8080
# TEK worker: süreç içi oturum, SQLite ve hedef kilidi buna bağlı.
CMD ["python3", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers"]

FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install --require-hashes -r requirements-dev.txt; else pip install --require-hashes -r requirements.txt; fi
COPY --chown=app:app manage.py pyproject.toml ./
COPY --chown=app:app config ./config
COPY --chown=app:app core ./core
COPY --chown=app:app accounts ./accounts
COPY --chown=app:app organization ./organization
COPY --chown=app:app standards ./standards
COPY --chown=app:app assessments ./assessments
COPY --chown=app:app evidence ./evidence
COPY --chown=app:app calculations ./calculations
COPY --chown=app:app reports ./reports
COPY --chown=app:app auditlog ./auditlog

RUN mkdir -p /app/staticfiles /app/private-evidence && chown -R app:app /app/staticfiles /app/private-evidence
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-", "--worker-tmp-dir", "/tmp"]

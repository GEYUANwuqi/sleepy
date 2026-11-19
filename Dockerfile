FROM python:3.12-slim AS base

FROM base AS build
WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN python -m pip install --no-cache-dir uv==0.12.18
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/sleepy/ src/sleepy/
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python

FROM base
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
RUN groupadd --gid 1000 sleepy \
    && useradd --uid 1000 --gid sleepy --no-create-home sleepy \
    && install -d -m 700 -o sleepy -g sleepy /app/data
USER sleepy:sleepy
EXPOSE 8080
ENTRYPOINT ["sleepy"]
CMD ["run", "--host", "0.0.0.0", "--port", "8080"]

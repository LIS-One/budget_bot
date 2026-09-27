FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    MPLCONFIGDIR=/home/bot/.matplotlib

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app/data

COPY budget_bot ./budget_bot

USER bot
# прогреваем кеш шрифтов matplotlib, чтобы первая диаграмма рисовалась быстро
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.font_manager"

VOLUME ["/app/data"]
CMD ["python", "-m", "budget_bot"]

FROM python:3.12-slim

RUN pip install --no-cache-dir maxminddb==2.6.2 \
 && useradd --system --uid 10001 --home /var/lib/logsnpm logsnpm \
 && mkdir -p /var/lib/logsnpm /etc/logsnpm \
 && chown logsnpm /var/lib/logsnpm

WORKDIR /app
COPY logsnpm ./logsnpm

ENV PYTHONUNBUFFERED=1 \
    LOGSNPM_DATA_DIR=/var/lib/logsnpm
USER logsnpm
EXPOSE 7881
VOLUME ["/var/lib/logsnpm"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('LOGSNPM_PORT','7881')+'/api/config',timeout=4)" || exit 1
ENTRYPOINT ["python", "-m", "logsnpm"]
CMD ["serve"]

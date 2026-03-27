FROM odoo:19

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pip && \
    python3 -m pip install --no-cache-dir --break-system-packages --ignore-installed \
        pymysql pydantic paramiko && \
    rm -rf /var/lib/apt/lists/*
USER odoo

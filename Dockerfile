# Nullcell — imagen para operar un relevo (§1, §10).
#
# Dos etapas: las ruedas se construyen con compilador, la imagen final no lo
# lleva. El resultado corre como usuario sin privilegios, porque un relevo no
# necesita root para nada (§0, decisión 3).
FROM python:3.12-slim AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md ./
COPY nullcell ./nullcell
RUN pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------- imagen final
FROM python:3.12-slim

# El estado del nodo va en un volumen, nunca en la imagen: contiene la clave
# privada de la celda (§3) y su base de peers.
ENV NULLCELL_HOME=/var/lib/nullcell \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --system --create-home --home-dir /var/lib/nullcell --shell /usr/sbin/nologin nullcell
COPY --from=build /install /usr/local

USER nullcell
WORKDIR /var/lib/nullcell
VOLUME ["/var/lib/nullcell"]

# QUIC sobre UDP. Un relevo sólo necesita este puerto abierto hacia fuera.
EXPOSE 4433/udp

# `docker stop` envía SIGTERM, que vacía los circuitos vivos antes de salir
# (§10). El plazo por defecto de Docker son 10 s; dale más que el --drain-timeout.
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s \
  CMD ["ncell", "id"]

ENTRYPOINT ["ncell"]
CMD ["relay", "--help"]

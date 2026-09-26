# Herramientas de PC de PulseHost

## Panel local

Arráncalo en el ordenador:

```bash
python3 tools/pulse-panel.py
```

Abre `http://127.0.0.1:8787`. Desde el panel puedes probar la conexión, seleccionar un archivo, añadirlo a la cola, ver el progreso/estado y explorar `/data/pkg` u otra ruta FTP.

El panel también admite URL HTTP con pausa/reanudación cooperativa, progreso, categorías automáticas, cancelación, extracción segura de ZIP/7z y gestión de los archivos locales descargados.

Para abrirlo desde otro dispositivo de tu red local, usa `--host 0.0.0.0` y entra en `http://IP-DEL-PC:8787`. Hazlo solo en una red de confianza: esta primera versión no incluye autenticación web.

## Transferencias FTP reanudables

`pulse-transfer.py` conecta con el FTP de GoldHEN (normalmente puerto `2121`) y:

- Detecta el tamaño que ya existe en `/data/pkg`.
- Intenta continuar con `REST` + `STOR`.
- Guarda el progreso en `~/.pulsehost-transfers.json`.
- Reintenta tras una desconexión.
- Verifica el tamaño remoto al terminar.
- Procesa una cola mediante un manifiesto.
- Empieza a transferir inmediatamente por defecto, sin calcular el hash completo antes.
- Usa bloques de 4 MiB y un búfer TCP mayor para reducir sobrecarga.

### Un archivo

```bash
python3 tools/pulse-transfer.py \
  --host 192.168.1.50 \
  --file ./juego.pkg \
  --remote /data/pkg/juego.pkg
```

Para priorizar una comprobación SHA-256 antes de empezar, añade `--hash`; ralentiza el inicio. Si la red concreta rinde mejor con otro tamaño, prueba `--block-size 2097152` o `--block-size 8388608`.

### Cola

Crea `queue.txt`:

```text
./juego-1.pkg /data/pkg/juego-1.pkg
./update.pkg /data/pkg/update.pkg
./dlc.pkg /data/pkg/dlc.pkg
```

Ejecuta:

```bash
python3 tools/pulse-transfer.py \
  --host 192.168.1.50 \
  --manifest queue.txt
```

Si se corta la conexión, vuelve a ejecutar el mismo comando: continuará desde el tamaño remoto cuando el FTP anuncie `REST`; si el servidor no soporta reanudación, avisará y empezará de cero para no corromper el archivo.

El helper se ejecuta en el PC. La página de la PS4 no habla FTP directamente porque el navegador no expone un cliente FTP; el panel web podrá controlar este helper más adelante mediante una API local.

## Download Hub tipo Apollo

Para descargar cualquier archivo desde una URL (`.zip`, `.7z`, `.json`, `.pkg`, etc.) con reanudación HTTP:

```bash
python3 tools/pulse-download.py \
  https://servidor.example/archivo.zip \
  --output ./downloads/archivo.zip
```

También puede descargarlo y enviarlo automáticamente a la PS4 al terminar:

```bash
python3 tools/pulse-download.py \
  https://servidor.example/archivo.pkg \
  --output ./downloads/archivo.pkg \
  --ftp-host 192.168.1.50 \
  --remote /data/pkg/archivo.pkg
```

Si se corta la conexión, repite el mismo comando y continuará mediante HTTP `Range`. `--sha256 HASH` añade comprobación de integridad. El estado se guarda en `~/.pulsehost-downloads.json`.

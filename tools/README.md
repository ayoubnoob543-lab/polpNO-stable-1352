# Herramientas de PC de PulseHost

## Transferencias FTP reanudables

`pulse-transfer.py` conecta con el FTP de GoldHEN (normalmente puerto `2121`) y:

- Detecta el tamaño que ya existe en `/data/pkg`.
- Intenta continuar con `REST` + `STOR`.
- Guarda el progreso en `~/.pulsehost-transfers.json`.
- Reintenta tras una desconexión.
- Verifica el tamaño remoto al terminar.
- Procesa una cola mediante un manifiesto.

### Un archivo

```bash
python3 tools/pulse-transfer.py \
  --host 192.168.1.50 \
  --file ./juego.pkg \
  --remote /data/pkg/juego.pkg
```

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

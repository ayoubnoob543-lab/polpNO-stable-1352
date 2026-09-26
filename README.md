# polpNO-stable-1352

Variante privada de trabajo basada en `mansoor0x/polpNO` para PS4 13.52.

## Qué cambia

- Preflight de firmware/tabla y de los archivos `patches/1352.bin` y `goldhen.bin` antes de establecer la primitiva.
- Aborta explícitamente si falta un recurso, está truncado o el payload no tiene el byte de entrada esperado.
- Mantiene los reintentos automáticos únicamente en la fase de lectura segura.
- Limita el parámetro `retry` a 0–32 para evitar bucles accidentales.
- Elimina la fuente externa del panel para reducir esperas y tráfico durante el exploit.

## Límite importante

Un kernel panic no puede capturarse desde JavaScript después de que comienzan las escrituras del kernel. Esta variante reduce intentos inválidos antes de esa fase, pero no puede garantizar que un exploit de kernel no falle ni impedir un panic ya iniciado.

## Uso

La URL publicada debe servirse desde un host HTTPS compatible. Para más reintentos en la fase segura:

```text
/?retry=20
```

No usar `force=1`, `patch=0` ni `payload=0` en 13.52.

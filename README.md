# PulseHost 13.52
Host privado de estabilidad para PS4 13.52, basado en el trabajo upstream de `mansoor0x/polpNO` y rebrandeado como PulseHost.

## Gestor de archivos y PKG

La primera pieza está en [`tools/pulse-transfer.py`](tools/pulse-transfer.py): es un helper para PC que usa el FTP de GoldHEN en el puerto 2121, reanuda transferencias con `REST`/`STOR`, guarda estado local y procesa colas. La página de la PS4 no puede hablar FTP directamente; el panel web debe comunicarse con este helper local.

La siguiente capa puede añadir inventario de `/data/pkg`, `/user/app` y `/user/addcont`, verificación de hashes, cola con prioridades y un panel móvil. La instalación de PKG debe seguir siendo una acción explícita del usuario; transferir un archivo no equivale a instalarlo.

El panel local ya está disponible en [`tools/pulse-panel.py`](tools/pulse-panel.py) con interfaz en [`tools/pulse-panel.html`](tools/pulse-panel.html): conexión FTP, cola, subida reanudable y explorador de rutas.

El módulo [`tools/pulse-download.py`](tools/pulse-download.py) añade descargas automáticas desde URL para cualquier extensión, reanudación HTTP, comprobación SHA-256 opcional y envío posterior a la PS4.

## Qué cambia

- Preflight de firmware/tabla y de los archivos `patches/1352.bin` y `goldhen.bin` antes de establecer la primitiva.
- Aborta explícitamente si falta un recurso, está truncado o el payload no tiene el byte de entrada esperado.
- Mantiene los reintentos automáticos únicamente en la fase de lectura segura.
- Limita el parámetro `retry` a 0–32 para evitar bucles accidentales.
- Permite entre 4 y 12 intentos del primitive mediante `attempts`, con 8 por defecto.
- Elimina la fuente externa del panel para reducir esperas y tráfico durante el exploit.
- Desactiva las peticiones de telemetría y las animaciones pesadas por defecto para reducir el lag del navegador.
- Guarda localmente `retry` y `attempts` cuando el payload termina bien y los reutiliza al siguiente arranque del host.
- Precarga `1352.bin` y `goldhen.bin` en paralelo y reutiliza esos bytes, evitando dos descargas durante una ejecución.
- No usa URLs con `?v=` para evitar entradas duplicadas en AppCache y acelerar el arranque offline.
- Marca una ejecución activa y avisa si la anterior no terminó; en ese caso hay que reiniciar antes de reintentar.
- Añade perfiles `stable` (por defecto) y `fast`; `fast` reduce esperas solo en fases seguras.
- `fast` también reduce los retardos de composición, reintento y aparcado del worker; puede ser menos tolerante al timing que `stable`.
- En `fast` los retardos internos de captura/composición bajan de 50/100 ms a 20/50 ms y el reintento seguro mínimo baja de 750 ms a 300 ms.
- Verifica con SHA-256 que `1352.bin` y `goldhen.bin` son exactamente los recursos probados antes de iniciar.
- En `fast`, verifica muestras al copiar GoldHEN en vez de releer los 293 KB byte a byte; `stable` mantiene la verificación completa.

## Límite importante

Un kernel panic no puede capturarse desde JavaScript después de que comienzan las escrituras del kernel. Esta variante reduce intentos inválidos antes de esa fase, pero no puede garantizar que un exploit de kernel no falle ni impedir un panic ya iniciado.

## Uso

La URL publicada debe servirse desde un host HTTPS compatible. Para más reintentos en la fase segura:

```text
/?retry=20
```

Para ampliar únicamente los intentos previos a la fase de kernel:

```text
/?retry=20&attempts=10
```

No usar `force=1`, `patch=0` ni `payload=0` en 13.52.

El perfil normal es el estable:

```text
?profile=stable&retry=12&attempts=8
```

Para comparar velocidad sin tocar los offsets ni el kpatch:

```text
?profile=fast&retry=4&attempts=6
```

`fast` no es el modo recomendado si la prioridad es evitar kernel panic.

Si quieres que cargue y ejecute con la menor espera:

```text
?profile=fast&retry=2&attempts=6
```

Si quieres el perfil rápido pero con verificación completa del payload:

```text
?profile=fast&verify=1&retry=2&attempts=6
```

Si se bloquea o empeoran los resultados, vuelve inmediatamente a `profile=stable`.

El autodiagnóstico puede rechazar una caché incompleta o un binario cambiado, pero no puede predecir el resultado de una carrera del kernel ni garantizar que un panic sea recuperable.

## Logs

Para generar un log local completo usa:

```text
?profile=stable&log=1&retry=0&attempts=8&verify=1
```

Al terminar aparecerá **Guardar log**. Descarga el `.txt` y súbelo al chat o al repositorio privado manualmente. El host también guarda el último log en `localStorage` como `polpNO:last-log`; no envía datos automáticamente a GitHub.

La telemetría queda desactivada en el modo normal. Solo actívala para depurar con:

```text
/?telemetry=1&log=1&verbose=1
```

Esto no hace persistente GoldHEN: solo recuerda la configuración que funcionó mejor.

## Plan de estabilidad para 13.52

La caché elimina fallos de red y de recursos incompletos, pero no corrige un kernel panic. Para mejorar la fiabilidad sin cambiar offsets a ciegas:

1. Probar primero con `?log=1&retry=0&attempts=8` y anotar la última etapa visible.
2. Repetir 10 veces desde arranque en frío y 10 veces desde modo reposo; no mezclar resultados.
3. Cambiar una sola variable por vez: primero `attempts`, luego `retry`; no tocar `kpatch`, offsets, `KA` o `sweep` sin una medición.
4. Si el fallo ocurre antes de `PREFLIGHT-OK`, es un problema de caché/recursos; si ocurre antes de `PRIMITIVE-OK`, es la fase WebKit; si ocurre después de `KERNEL-PATCHED`, requiere ajustar el exploit en hardware real.
5. No recargar después de una escritura del kernel: reiniciar la PS4 y repetir desde cero.

Parámetros seguros para comparar:

```text
?log=1&retry=0&attempts=6
?log=1&retry=0&attempts=8
?log=1&retry=0&attempts=10
```

La telemetría de red se deja apagada para no añadir lag. El log local (`log=1`) no necesita conexión.

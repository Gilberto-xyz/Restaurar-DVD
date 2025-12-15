# Restaurar-DVD

Script en Python para intentar volcar un DVD/CD a un archivo `.iso` desde Windows, incluso si el disco tiene sectores difíciles/ilegibles. Está pensado para “rescatar” el máximo posible: cuando un bloque no se puede leer, reduce el tamaño de lectura y reintenta; si aun así falla, rellena ese sector con ceros y continúa.

## Requisitos

- Windows (usa lectura “raw” del dispositivo: `\\.\X:`).
- Python 3.x.
- Ejecutar la consola como **Administrador**.
- Espacio libre suficiente para el `.iso` resultante.
- Cerrar programas que estén usando la unidad (VLC, IsoBuster, etc.).

## Archivos

- `DVD_restorer_validado.py`: script principal.

## Uso rápido

Desde PowerShell/CMD **como Administrador**:

```bash
python DVD_restorer_validado.py F DVD_dump.iso
```

La letra puede pasarse como `F` o `F:` (el script normaliza el formato).

### Ejemplo con parámetros

```bash
python DVD_restorer_validado.py F DVD_dump.iso --cols 60 --rows 18 --zero-tail-mb 512 --no-progress-min 5 --no-progress-armed-mb 300
```

## Qué hace y cómo funciona

1. Abre el dispositivo de la unidad en modo raw (`\\.\F:`) y crea el archivo `.iso` de salida.
2. Intenta estimar el tamaño total del disco para poder mostrar progreso:
   - TOC (tabla de contenidos) del CD/DVD.
   - ISO9660 (PVD en el sector 16), si aplica.
   - IOCTL de longitud del dispositivo.
3. Lee de forma “adaptativa”:
   - Prueba bloques grandes y, si falla, baja el tamaño (por defecto: 512KiB → 64KiB → 8KiB → 1 sector).
   - Hace varios reintentos por bloque (`RETRIES` en el script).
4. Si no puede leer un sector, escribe `0x00` para mantener el tamaño/offset del `.iso`.
5. Finaliza por alguno de estos motivos:
   - EOF (fin real del dispositivo).
   - Si no hay tamaño total fiable: detecta una “cola larga de ceros” (parámetro `--zero-tail-mb`) y asume que llegó al final.
   - “Corte por no progreso”: si durante un tiempo no logra leer ningún bloque válido, detiene el volcado (útil cuando el disco entra en una zona totalmente ilegible).

## Parámetros (CLI)

- `letter` (posicional): letra de unidad, por ejemplo `F`.
- `output` (posicional): ruta del `.iso` de salida.
- `--cols` / `--rows`: tamaño de la matriz de progreso cuando se conoce el total.
- `--zero-tail-mb`: umbral de “cola de ceros” (solo cuando no se conoce el total). Default: `512`.
- `--no-progress-min`: minutos sin lograr lecturas válidas antes de cortar. Default: `5`.
- `--no-progress-armed-mb`: cantidad mínima de MB leídos antes de habilitar el corte por no progreso (evita falsos positivos al inicio). Default: `500`.

## Interpretación del resultado

- El `.iso` puede contener tramos con ceros donde el disco fue ilegible. Eso significa que la imagen puede montar/abrir pero tener archivos corruptos en esas zonas.
- El contador de “sectores 0x00” indica cuántos sectores se rellenaron con ceros por errores de lectura.

## Problemas comunes

- **Permission denied / acceso denegado**: ejecuta la consola como Administrador y cierra programas que estén usando la unidad.
- **La barra/matriz “se queda”**: puede ser el lector reintentando sectores; espera un rato. Si entra en una zona totalmente ilegible, el script puede cortar por “no progreso” según tus parámetros.

## Limitaciones

- No evita protecciones anticopia/DRM.
- Depende del estado del lector y del disco: en discos muy dañados puede quedar un `.iso` incompleto o con muchos sectores a `0x00`.

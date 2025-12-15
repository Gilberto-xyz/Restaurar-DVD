# Uso:
#   python volcar_dvd_grid_safe_noprogress.py F DVD_dump.iso --cols 60 --rows 18 --zero-tail-mb 512 --no-progress-min 5 --no-progress-armed-mb 300
#
# Ejecuta PowerShell/CMD como **Administrador**. Cierra IsoBuster/VLC.

import sys, os, time, struct, argparse

SECTOR_SIZE = 2048
RETRIES = 3
PROGRESS_EVERY = 0.1  # s
BLOCK_SIZES_SECTORS = [256, 32, 4, 1]  # 512KiB, 64KiB, 8KiB, 1 sector
CSI = "\x1b["

# ----------------- util -----------------
def is_admin():
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def human(n):
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024 or u == "TB":
            return f"{n:.2f} {u}"
        n /= 1024

def credible_total(n):
    return 500*1024*1024 <= n <= 9*1024*1024*1024  # ~500MB..9GB

# ----------------- estimaciones de tamaño -----------------
def get_total_from_iso9660(dvd_file):
    try:
        pos = dvd_file.tell()
        dvd_file.seek(16 * SECTOR_SIZE)
        pvd = dvd_file.read(SECTOR_SIZE)
        dvd_file.seek(pos)
        if len(pvd) == SECTOR_SIZE and pvd[0] == 1 and pvd[1:6] == b"CD001":
            vss_le = struct.unpack_from("<I", pvd, 80)[0]
            vss_be = struct.unpack_from(">I", pvd, 84)[0]
            blocks = max(vss_le, vss_be)
            total = int(blocks) * SECTOR_SIZE
            return total if credible_total(total) else None
    except Exception:
        pass
    return None

def get_total_from_ioctl(letter):
    try:
        import ctypes
        from ctypes import wintypes
        GENERIC_READ  = 0x80000000
        FILE_SHARE_READ  = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        IOCTL_DISK_GET_LENGTH_INFO = 0x7405C
        CreateFileW = ctypes.windll.kernel32.CreateFileW
        DeviceIoControl = ctypes.windll.kernel32.DeviceIoControl
        CloseHandle = ctypes.windll.kernel32.CloseHandle
        path = f"\\\\.\\{letter}:"
        h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if h in (0, -1):
            return None
        class LENINFO(ctypes.Structure):
            _fields_ = [("Length", ctypes.c_longlong)]
        out = LENINFO()
        ret = wintypes.DWORD(0)
        ok = DeviceIoControl(h, IOCTL_DISK_GET_LENGTH_INFO, None, 0,
                             ctypes.byref(out), ctypes.sizeof(out),
                             ctypes.byref(ret), None)
        CloseHandle(h)
        if ok:
            total = int(out.Length)
            return total if credible_total(total) else None
    except Exception:
        pass
    return None

def get_total_from_toc(letter):
    try:
        import ctypes
        from ctypes import wintypes
        GENERIC_READ  = 0x80000000
        FILE_SHARE_READ  = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        IOCTL_CDROM_READ_TOC_EX = 0x24054

        CreateFileW = ctypes.windll.kernel32.CreateFileW
        DeviceIoControl = ctypes.windll.kernel32.DeviceIoControl
        CloseHandle = ctypes.windll.kernel32.CloseHandle

        path = f"\\\\.\\{letter}:"
        h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if h in (0, -1):
            return None

        class CDROM_READ_TOC_EX(ctypes.Structure):
            _fields_ = [
                ("Format", ctypes.c_ubyte),      # 0x00 = Full TOC
                ("SessionTrack", ctypes.c_ubyte),
                ("Reserved", ctypes.c_ubyte * 2),
                ("Msf", ctypes.c_ubyte),         # 0 = LBA
                ("Reserved2", ctypes.c_ubyte * 3)
            ]

        outsize = 2048
        outbuf = (ctypes.c_ubyte * outsize)()
        inbuf = CDROM_READ_TOC_EX()
        inbuf.Format = 0x00
        inbuf.SessionTrack = 0
        inbuf.Msf = 0

        bytes_returned = wintypes.DWORD(0)
        ok = DeviceIoControl(h, IOCTL_CDROM_READ_TOC_EX,
                             ctypes.byref(inbuf), ctypes.sizeof(inbuf),
                             ctypes.byref(outbuf), outsize,
                             ctypes.byref(bytes_returned), None)
        CloseHandle(h)
        if not ok or bytes_returned.value < 10:
            return None

        data = bytes(outbuf[:bytes_returned.value])
        for off in range(4, len(data)-11, 11):
            track = data[off+5]
            if track == 0xAA:  # lead-out
                lba = struct.unpack_from(">I", b"\x00"+data[off+8:off+11])[0]
                if lba == 0:
                    lba = struct.unpack_from("<I", data, off+7)[0]
                total = (int(lba)+1) * SECTOR_SIZE
                return total if credible_total(total) else None
        return None
    except Exception:
        return None

def estimate_total(letter, dvd_file):
    t = get_total_from_toc(letter)
    if t: return t, "TOC"
    t = get_total_from_iso9660(dvd_file)
    if t: return t, "ISO9660"
    t = get_total_from_ioctl(letter)
    if t: return t, "IOCTL"
    return None, None

# ----------------- UI -----------------
def draw_grid(cols, rows, filled_cells, total_cells):
    filled_cells = max(0, min(total_cells, filled_cells))
    full = "█"
    empty = "░"
    out_lines = []
    for r in range(rows):
        line = []
        for c in range(cols):
            idx = r*cols + c
            line.append(full if idx < filled_cells else empty)
        out_lines.append("".join(line))
    return "\n".join(out_lines)

def print_ui_header():
    print()

def update_ui_matrix(cols, rows, bytes_done, total, zero_sectors, rescued, t0, note=""):
    total_cells = cols * rows
    pct = (bytes_done/total) if total else 0.0
    filled = int(total_cells * min(1.0, pct))
    grid_txt = draw_grid(cols, rows, filled, total_cells)
    print(f"{CSI}H", end="")
    print_ui_header()
    print(grid_txt)
    speed = bytes_done / max(1e-6, (time.time()-t0))
    print(f"\nProgreso: {(pct*100):.2f}%  |  {human(bytes_done)} de {human(total)}")
    print(f"Vel: {human(speed)}/s  |  Sectores 0x00: {zero_sectors}  |  Rescates: {rescued}{note}")

def update_ui_bar(bytes_done, zero_sectors, rescued, t0, width=50, note=""):
    filled = min(width, int(width))  # solo estética; no % real
    speed = bytes_done / max(1e-6, (time.time()-t0))
    bar = "█"*min(width, int((bytes_done/ (1024*1024)) % (width+1))) + "░"*max(0, width - min(width, int((bytes_done/ (1024*1024)) % (width+1))))
    print(f"\r[{bar:<{width}}]  {human(bytes_done)} | {human(speed)}/s | 0x00:{zero_sectors} | rescates:{rescued}{note} ", end="", flush=True)

# ----------------- volcado adaptativo + colas + no-progreso -----------------
def dump(letter, out_path, cols, rows, zero_tail_mb, no_progress_min, no_progress_armed_mb):
    if not is_admin():
        print("ERROR: este script debe ejecutarse como **Administrador** (lectura raw de \\.\DEVICE).")
        sys.exit(2)

    device = f"\\\\.\\{letter}:"
    with open(device, "rb", buffering=0) as dvd:
        total, src = estimate_total(letter, dvd)
        has_percent = total is not None
        print(f"Tamaño estimado: {human(total)} ({src})" if has_percent else
              "Sin tamaño total creíble → usaré barra + detección de cola de ceros + corte por no-progreso.")

        print_ui_header()
        with open(out_path, "wb", buffering=0) as iso:
            bytes_done = 0
            t0 = time.time()
            last_draw = 0.0
            zero_filled_sectors = 0
            rescued_errors = 0

            # Cola de ceros
            zero_tail_bytes = 0
            zero_tail_limit = int(zero_tail_mb * 1024 * 1024)

            # No-progreso (según bytes escritos)
            no_progress_secs = int(no_progress_min * 60)
            last_advance_bytes = 0
            last_advance_ts = time.time()
            no_progress_triggered = False

            while True:
                if has_percent and bytes_done >= total:
                    probe = dvd.read(SECTOR_SIZE)
                    if not probe:
                        break
                    dvd.seek(-len(probe), os.SEEK_CUR)

                advanced = False
                last_size = BLOCK_SIZES_SECTORS[0] * SECTOR_SIZE

                for blk_secs in BLOCK_SIZES_SECTORS:
                    blk = blk_secs * SECTOR_SIZE
                    ok = False
                    tries = 0
                    while tries < RETRIES:
                        try:
                            data = dvd.read(blk)
                            if not data:
                                ok = True
                                break
                            ok = True
                            break
                        except PermissionError:
                            raise
                        except OSError:
                            time.sleep(0.02)
                            tries += 1
                    if ok:
                        if not data:  # EOF
                            break
                        iso.write(data)
                        bytes_done += len(data)
                        iso.flush()  # asegurar que el tamaño en disco avance
                        os.fsync(iso.fileno())
                        advanced = True
                        last_size = blk

                        if not has_percent:
                            if all(b == 0 for b in data):
                                zero_tail_bytes += len(data)
                            else:
                                zero_tail_bytes = 0
                        break
                    else:
                        continue

                if not advanced:
                    iso.write(b"\x00" * SECTOR_SIZE)
                    bytes_done += SECTOR_SIZE
                    iso.flush(); os.fsync(iso.fileno())
                    zero_filled_sectors += 1
                    last_size = SECTOR_SIZE
                    if not has_percent:
                        zero_tail_bytes += SECTOR_SIZE

                # --- control de no-progreso ---
                if bytes_done > last_advance_bytes:
                    last_advance_bytes = bytes_done
                    last_advance_ts = time.time()
                else:
                    # Si ya leímos al menos 'armed' MB y no avanzamos en 'no_progress_secs', cortar
                    if bytes_done >= no_progress_armed_mb * 1024 * 1024 and (time.time() - last_advance_ts) >= no_progress_secs:
                        no_progress_triggered = True
                        print("\nCorte por **no progreso**: el archivo no aumentó de tamaño en el intervalo definido.")
                        break

                # --- cola de ceros (solo sin total fiable) ---
                if not has_percent:
                    if bytes_done > 500*1024*1024 and zero_tail_bytes >= zero_tail_limit:
                        print("\nDetectada cola larga de ceros sin tamaño total fiable → asumo final del disco.")
                        break

                # UI
                now = time.time()
                if now - last_draw >= PROGRESS_EVERY:
                    if has_percent:
                        update_ui_matrix(cols, rows, bytes_done, total, zero_filled_sectors, rescued_errors, t0)
                    else:
                        update_ui_bar(bytes_done, zero_filled_sectors, rescued_errors, t0)
                    last_draw = now

            # cierre UI
            if has_percent:
                update_ui_matrix(cols, rows, bytes_done, total, zero_filled_sectors, rescued_errors, t0,
                                 note=("  |  (corte por no-progreso)" if no_progress_triggered else ""))
            else:
                update_ui_bar(bytes_done, zero_filled_sectors, rescued_errors, t0,
                              note=("  |  (corte por no-progreso)" if no_progress_triggered else ""))
                print()
            dur = time.time()-t0
            print(f"\nListo: {human(bytes_done)} en {dur:.1f}s (~{human(bytes_done/max(1,dur))}/s)")
            if no_progress_triggered:
                print("Motivo de fin: no hubo incremento de tamaño durante el período configurado; es probable que ya no existan más datos útiles.")
            elif zero_filled_sectors:
                print(f"Aviso: {zero_filled_sectors} sector(es) ilegibles se rellenaron con ceros.")
            if rescued_errors:
                print(f"Se recuperaron tramos reduciendo tamaño de lectura {rescued_errors} vez/veces.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("letter", help="Letra de unidad (ej. F)")
    ap.add_argument("output", help="Archivo de salida .iso")
    ap.add_argument("--cols", type=int, default=50, help="Columnas matriz (default 50)")
    ap.add_argument("--rows", type=int, default=20, help="Filas matriz (default 20)")
    ap.add_argument("--zero-tail-mb", type=int, default=512,
                    help="Umbral de cola de ceros si no hay tamaño fiable (MB). Default 512.")
    ap.add_argument("--no-progress-min", type=int, default=5,
                    help="Minutos sin avance para cortar por no-progreso. Default 5.")
    ap.add_argument("--no-progress-armed-mb", type=int, default=500,
                    help="MB mínimos leídos antes de habilitar el corte por no-progreso (evita falsos positivos). Default 500.")
    args = ap.parse_args()
    letter = args.letter.strip(":").upper()
    dump(letter, args.output, args.cols, args.rows, args.zero_tail_mb, args["no_progress_min"] if isinstance(args, dict) else args.no_progress_min, args.no_progress_armed_mb)

if __name__ == "__main__":
    try:
        main()
    except PermissionError:
        print("\nERROR: Permiso denegado. Ejecuta la consola como **Administrador** y cierra programas que usen la unidad.")
        sys.exit(2)

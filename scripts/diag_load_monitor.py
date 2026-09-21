"""Linux load diagnosis: monitor CPU/Memory/load/uptime, and optionally apply bounded CPU + memory
load. Purpose (user 2026-09-09): troubleshoot "High load causes shutdown/power outage". This
machine is VM host, and the host temperature cannot be seen in the guest (/sys/class/thermal
only cooling_device, no temperature reading), so VM suddenly Restart/Shut down=host action
(temperature/power supply/resource) rather than the client's own power event. Usage example:
Example: python scripts/diag_load_monitor.py --load cpu --csv /tmp/diag.csv --duration 30
Example: python scripts/diag_load_monitor.py --load cpu --csv /tmp/diag.csv --duration 30."""

from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import time


def _stat() -> tuple[int, int]:
    """Returns (total jiffies, idle jiffies)."""
    with open("/proc/stat") as fh:
        for line in fh:
            if line.startswith("cpu "):
                parts = [int(x) for x in line.split()[1:]]
                return sum(parts), parts[3] + parts[4]
    return 0, 0


def _busy(stop) -> None:
    while not stop.is_set():
        x = 0
        for _ in range(200_000):
            x = (x + 1) % 1_000_000


def _mem_hog(gb: float, stop) -> None:
    target = int(gb * (1 << 30))
    blocks: list[bytearray] = []
    while sum(len(b) for b in blocks) < target and not stop.is_set():
        blocks.append(bytearray(256 * (1 << 20)))
        time.sleep(0.1)
    while not stop.is_set():
        time.sleep(0.5)
    globals()["_hold"] = blocks


def _boot_time() -> float:
    for line in open("/proc/stat"):
        if line.startswith("btime "):
            return float(line.split()[1])
    return 0.0


def _uptime() -> float:
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])


def _loadavg() -> str:
    try:
        return open("/proc/loadavg").read().strip()
    except OSError:
        return ""


def _mem_gb() -> dict[str, float]:
    d: dict[str, int] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, rest = line.split(":", 1)
            d[key] = int(rest.strip().split()[0])
    return {
        "used_gb": round((d["MemTotal"] - d["MemAvailable"]) / (1 << 20), 2),
        "avail_gb": round(d["MemAvailable"] / (1 << 20), 2),
        "swap_gb": round((d["SwapTotal"] - d["SwapFree"]) / (1 << 20), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", choices=("none", "cpu", "mem", "both"), default="none")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--mem-gb", type=float, default=0.0)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    start_boot = _boot_time()
    t0 = time.time()
    stop = mp.Event()
    procs: list[mp.Process] = []
    if args.load in ("cpu", "both"):
        for _ in range(max(1, args.threads)):
            p = mp.Process(target=_busy, args=(stop,))
            p.start()
            procs.append(p)
    if args.load in ("mem", "both") and args.mem_gb > 0:
        p = mp.Process(target=_mem_hog, args=(args.mem_gb, stop))
        p.start()
        procs.append(p)

    t1, i1 = _stat()
    with open(args.csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "uptime_s", "cpu_pct", "mem_used_gb", "mem_avail_gb",
                    "swap_used_gb", "loadavg", "boot_changed"])
        while time.time() - t0 < args.duration:
            time.sleep(args.interval)
            t2, i2 = _stat()
            dt = (t2 - t1) - (i2 - i1)
            busy = 100.0 * dt / max(1, (t2 - t1))
            m = _mem_gb()
            w.writerow([round(time.time() - t0, 1), round(_uptime(), 1),
                        f"{busy:.1f}", m["used_gb"], m["avail_gb"], m["swap_gb"],
                        _loadavg(), _boot_time() != start_boot])
            fh.flush()
            t1, i1 = t2, i2

    stop.set()
    for p in procs:
        p.terminate()
    for p in procs:
        p.join(timeout=2)
    print("done wrote=" + args.csv + " load=" + args.load + " threads=" + str(args.threads) +
          " mem=" + str(args.mem_gb) + "G dur=" + str(args.duration) +
          "s boot_changed=" + str(_boot_time() != start_boot))


if __name__ == "__main__":
    main()

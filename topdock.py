#!/usr/bin/env python3
"""
TopDock - Docker Stats Dashboard
"""

__version__ = "0.2.1"

import sys
import time
import json
import csv
import threading
import argparse
from datetime import datetime

# ── dependency checks with helpful messages ───────────────────────────────────
def _check_deps():
    missing = []
    try:
        import docker  # noqa: F401
    except ImportError:
        missing.append("docker")
    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("rich")
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print(f"Fix: pip install {' '.join(missing)}")
        sys.exit(1)

_check_deps()

import docker
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.align import Align

# ─────────────────────────────────────────────
#  THEME
# ─────────────────────────────────────────────
THEME = {
    "bg":        "#0d0d0f",
    "border":    "#2a0a3a",
    "accent":    "#cc00ff",
    "accent2":   "#ff003c",
    "accent3":   "#00ffe0",
    "text":      "#e0d7f5",
    "muted":     "#5a4f72",
    "ok":        "#39ff14",
    "warn":      "#ffaa00",
    "crit":      "#ff003c",
    "cpu_bar":   "#cc00ff",
    "mem_bar":   "#00ffe0",
    "header_bg": "#1a0028",
    "sel_bg":    "#2a0a3a",
}

console = Console()

ALERT_THRESHOLD = 80.0
_alerts: list[dict] = []
_alerts_lock = threading.Lock()

# ─────────────────────────────────────────────
#  DOCKER STATS
# ─────────────────────────────────────────────

def bytes_to_human(n: float) -> str:
    """Convert bytes to human-readable string. Clamps negatives to 0."""
    n = max(0.0, n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:6.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"

def calc_cpu_percent(stats: dict) -> float:
    try:
        cpu_delta    = (stats["cpu_stats"]["cpu_usage"]["total_usage"]
                      - stats["precpu_stats"]["cpu_usage"]["total_usage"])
        system_delta = (stats["cpu_stats"]["system_cpu_usage"]
                      - stats["precpu_stats"]["system_cpu_usage"])
        num_cpus     = (stats["cpu_stats"].get("online_cpus")
                      or len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])))
        if system_delta > 0 and cpu_delta >= 0 and num_cpus > 0:
            return (cpu_delta / system_delta) * num_cpus * 100.0
    except (KeyError, ZeroDivisionError, TypeError):
        pass
    return 0.0

def calc_mem(stats: dict) -> tuple[float, float, float]:
    """Returns (used_bytes, limit_bytes, percent). Clamps used to >= 0."""
    try:
        mem   = stats["memory_stats"]
        cache = mem.get("stats", {}).get("cache", 0)
        used  = max(0.0, mem["usage"] - cache)   # FIX: clamp negative
        limit = mem["limit"]
        pct   = (used / limit * 100.0) if limit > 0 else 0.0
        return used, limit, pct
    except (KeyError, TypeError):
        return 0.0, 0.0, 0.0

def calc_net(stats: dict) -> tuple[float, float]:
    try:
        nets = stats.get("networks") or {}
        rx = sum(v.get("rx_bytes", 0) for v in nets.values())
        tx = sum(v.get("tx_bytes", 0) for v in nets.values())
        return float(rx), float(tx)
    except (AttributeError, TypeError):
        return 0.0, 0.0

def calc_blk(stats: dict) -> tuple[float, float]:
    try:
        blk_list = stats["blkio_stats"].get("io_service_bytes_recursive") or []
        r = sum(e["value"] for e in blk_list if e.get("op") == "read")
        w = sum(e["value"] for e in blk_list if e.get("op") == "write")
        return float(r), float(w)
    except (KeyError, TypeError):
        return 0.0, 0.0

def get_all_stats(client: docker.DockerClient) -> list[dict]:
    """Fetch stats for all running containers concurrently."""
    try:
        containers = client.containers.list()
    except docker.errors.APIError as e:
        # Docker daemon hiccup — return empty, dashboard will show stale data
        return []

    results: list[dict] = []
    results_lock = threading.Lock()   # FIX: separate lock from _alerts_lock

    def fetch(c):
        try:
            raw  = c.stats(stream=False)
            cpu  = calc_cpu_percent(raw)
            mu, ml, mp = calc_mem(raw)
            rx, tx = calc_net(raw)
            br, bw = calc_blk(raw)
            entry = {
                "id":       c.short_id,
                "name":     c.name,
                "status":   c.status,
                "image":    (c.image.tags[0] if c.image.tags else c.image.short_id),
                "cpu_pct":  min(cpu, 999.9),   # cap runaway values
                "mem_used": mu,
                "mem_lim":  ml,
                "mem_pct":  min(mp, 100.0),
                "net_rx":   rx,
                "net_tx":   tx,
                "blk_r":    br,
                "blk_w":    bw,
                "ts":       datetime.now().isoformat(),
            }
            with results_lock:
                results.append(entry)
            # FIX: fire alerts AFTER releasing results_lock to avoid
            # inconsistent lock ordering with _alerts_lock
            if cpu > ALERT_THRESHOLD:
                _fire_alert(c.name, "CPU", cpu)
            if mp > ALERT_THRESHOLD:
                _fire_alert(c.name, "MEM", mp)
        except docker.errors.NotFound:
            pass   # container removed between list() and stats()
        except Exception:
            pass

    threads = [threading.Thread(target=fetch, args=(c,), daemon=True)
               for c in containers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results

def _fire_alert(container: str, kind: str, value: float):
    with _alerts_lock:
        # dedupe: skip if same container+kind already pending within 30s
        now = datetime.now()
        for a in reversed(_alerts[-5:]):
            if a["container"] == container and a["kind"] == kind:
                break
        else:
            _alerts.append({
                "ts":        now.strftime("%H:%M:%S"),
                "container": container,
                "kind":      kind,
                "value":     value,
            })
        if len(_alerts) > 50:
            _alerts.pop(0)

# ─────────────────────────────────────────────
#  SORT
# ─────────────────────────────────────────────
SORT_KEYS = {
    "cpu":  "cpu_pct",
    "mem":  "mem_pct",
    "net":  "net_rx",
    "blk":  "blk_r",
    "name": "name",
}

def sort_stats(data: list[dict], sort_by: str) -> list[dict]:
    key = SORT_KEYS.get(sort_by, "cpu_pct")
    rev = sort_by != "name"
    # FIX: use type-safe defaults — "" for str keys, 0.0 for numeric
    default = "" if sort_by == "name" else 0.0
    return sorted(data, key=lambda x: x.get(key, default), reverse=rev)

# ─────────────────────────────────────────────
#  RENDERING
# ─────────────────────────────────────────────

def pct_bar(pct: float, color: str, width: int = 10) -> Text:
    pct = max(0.0, min(pct, 100.0))   # clamp to [0, 100]
    if pct >= 90:
        color = THEME["crit"]
    elif pct >= 70:
        color = THEME["warn"]
    filled = int((pct / 100.0) * width)
    bar = "█" * filled + "░" * (width - filled)
    t = Text()
    t.append(f"{bar} ", style=f"bold {color}")
    t.append(f"{pct:5.1f}%", style=f"bold {color}")
    return t

def status_dot(status: str) -> Text:
    t = Text()
    color = (THEME["ok"]   if status == "running" else
             THEME["warn"] if status == "paused"  else
             THEME["crit"])
    t.append("● ", style=f"bold {color}")
    t.append(status, style=THEME["muted"])
    return t

def make_header(sort_by: str, alert_count: int, total: int, refresh: float,
                scroll_offset: int, visible_rows: int,
                docker_ok: bool) -> Panel:
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    title = Text()
    title.append("⚡ DOCK", style=f"bold {THEME['accent']}")
    title.append("WATCH", style=f"bold {THEME['accent2']}")
    title.append("  //  ", style=THEME["muted"])
    title.append(now, style=THEME["accent3"])

    meta = Text()
    if not docker_ok:
        meta.append("⚠ Docker unreachable — retrying…", style=f"bold {THEME['crit']}")
    else:
        meta.append(f"containers:{total}  ", style=f"bold {THEME['text']}")
        meta.append(f"sort:{sort_by}  ",     style=f"bold {THEME['accent3']}")
        meta.append(f"refresh:{refresh}s  ", style=THEME["muted"])
        if total > visible_rows:
            end = min(scroll_offset + visible_rows, total)
            meta.append(f"rows:{scroll_offset+1}-{end}/{total}  ",
                        style=THEME["accent3"])
        if alert_count:
            meta.append(f"⚠  ALERTS:{alert_count}", style=f"bold {THEME['crit']}")
        else:
            meta.append("alerts:0", style=THEME["muted"])

    return Panel(
        Align.center(Text.assemble(title, "\n", meta)),
        border_style=THEME["accent"] if docker_ok else THEME["crit"],
        style=f"on {THEME['header_bg']}",
        padding=(0, 2),
    )

def make_table(data: list[dict], sort_by: str, scroll_offset: int,
               visible_rows: int, selected: int) -> Table:
    t = Table(
        box=box.SIMPLE_HEAD,
        border_style=THEME["border"],
        header_style=f"bold {THEME['accent']}",
        style=THEME["text"],
        show_edge=True,
        expand=True,
        padding=(0, 1),
    )
    t.add_column("●",              width=12)
    t.add_column("CONTAINER",      min_width=16, style=f"bold {THEME['accent3']}")
    t.add_column("ID",             width=10,     style=THEME["muted"])
    t.add_column("IMAGE",          min_width=14, style=THEME["muted"], overflow="fold")
    t.add_column("CPU %",          min_width=20)
    t.add_column("MEM %",          min_width=20)
    t.add_column("MEM USED/LIMIT", min_width=18, justify="right")
    t.add_column("NET ↓/↑",        min_width=20, justify="right")
    t.add_column("BLK R/W",        min_width=18, justify="right")

    if not data:
        t.add_row(
            Text("", style=THEME["muted"]),
            Text("no containers running", style=THEME["muted"]),
            *[""] * 7,
        )
        return t

    visible = data[scroll_offset: scroll_offset + visible_rows]

    for i, row in enumerate(visible):
        abs_idx  = scroll_offset + i
        is_sel   = abs_idx == selected
        row_style = f"on {THEME['sel_bg']}" if is_sel else ""

        sel_prefix = Text()
        if is_sel:
            sel_prefix.append("▶ ", style=f"bold {THEME['accent']}")
        sel_prefix.append_text(status_dot(row["status"]))

        mem_label = Text()
        mem_label.append(bytes_to_human(row["mem_used"]), style=THEME["text"])
        mem_label.append(" / ", style=THEME["muted"])
        mem_label.append(bytes_to_human(row["mem_lim"]), style=THEME["muted"])

        net_label = Text()
        net_label.append(f"↓{bytes_to_human(row['net_rx'])}", style=THEME["ok"])
        net_label.append(" / ", style=THEME["muted"])
        net_label.append(f"↑{bytes_to_human(row['net_tx'])}", style=THEME["warn"])

        blk_label = Text()
        blk_label.append(f"R:{bytes_to_human(row['blk_r'])}", style=THEME["accent3"])
        blk_label.append(" / ", style=THEME["muted"])
        blk_label.append(f"W:{bytes_to_human(row['blk_w'])}", style=THEME["warn"])

        img = row["image"]
        if len(img) > 26:
            img = img[:23] + "…"

        t.add_row(
            sel_prefix,
            Text(row["name"], style=f"bold {THEME['accent3']}"),
            Text(row["id"],   style=THEME["muted"]),
            Text(img,         style=THEME["muted"]),
            pct_bar(row["cpu_pct"], THEME["cpu_bar"]),
            pct_bar(row["mem_pct"], THEME["mem_bar"]),
            mem_label,
            net_label,
            blk_label,
            style=row_style,
        )

    # scrollbar
    if len(data) > visible_rows:
        pct = scroll_offset / max(len(data) - visible_rows, 1)
        bar_len = 24
        pos = int(pct * (bar_len - 1))
        sc = Text()
        sc.append("─" * pos,              style=THEME["muted"])
        sc.append("◆",                    style=THEME["accent"])
        sc.append("─" * (bar_len - pos),  style=THEME["muted"])
        t.caption = sc

    return t

def make_alerts_panel() -> Panel:
    with _alerts_lock:
        recent = list(_alerts[-6:])
    if not recent:
        body = Text("  no alerts", style=THEME["muted"])
    else:
        body = Text()
        for a in reversed(recent):
            body.append(f"  {a['ts']} ", style=THEME["muted"])
            body.append(f"[{a['kind']}] ", style=f"bold {THEME['crit']}")
            body.append(f"{a['container']} ", style=f"bold {THEME['accent3']}")
            body.append(f"> {a['value']:.1f}%\n", style=THEME["warn"])
    return Panel(body, title=f"[bold {THEME['crit']}]⚠  ALERTS[/]",
                 border_style=THEME["accent2"], padding=(0, 1))

def make_help_bar(export_msg: str = "", export_timer: float = 0.0) -> Panel:
    if export_msg and (time.time() - export_timer < 3):
        t = Text(f"  ✔ {export_msg}", style=f"bold {THEME['ok']}", justify="center")
        return Panel(t, border_style=THEME["ok"], padding=(0, 0))
    t = Text(justify="center")
    for key, label in [
        ("↑↓", "scroll"), ("c", "CPU"), ("m", "MEM"), ("n", "NET"),
        ("b", "BLK"),     ("e", "export"), ("a", "clr alerts"), ("q", "quit"),
    ]:
        t.append(f" [{key}] ", style=f"bold {THEME['accent']}")
        t.append(f"{label} ", style=THEME["muted"])
    return Panel(t, border_style=THEME["border"], padding=(0, 0))

# ─────────────────────────────────────────────
#  EXPORT
# ─────────────────────────────────────────────

def export_csv(data: list[dict], path: str = "topdock_export.csv") -> str | None:
    if not data:
        return None
    keys = ["ts","name","id","image","status","cpu_pct","mem_pct",
            "mem_used","mem_lim","net_rx","net_tx","blk_r","blk_w"]
    try:
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
        return path
    except OSError as e:
        return None

def export_json(data: list[dict], path: str = "topdock_export.json") -> str | None:
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return path
    except OSError:
        return None

# ─────────────────────────────────────────────
#  LIVE DASHBOARD
# ─────────────────────────────────────────────

# approximate fixed UI rows: header(4) + alerts(9) + help(3) + table borders(2)
_FIXED_UI_ROWS = 18

def run_dashboard(client: docker.DockerClient, refresh: float,
                  sort_by: str, alert_threshold: float) -> None:
    global ALERT_THRESHOLD
    ALERT_THRESHOLD = alert_threshold

    # ── shared state (all access must hold `state_lock`) ─────────────────
    state_lock   = threading.Lock()
    current_sort = sort_by
    last_data:   list[dict] = []
    scroll_offset = 0
    selected      = 0
    export_msg    = ""
    export_timer  = 0.0
    docker_ok     = True
    quit_event    = threading.Event()

    def _vis_rows() -> int:
        return max(1, console.size.height - _FIXED_UI_ROWS)

    def _clamp() -> None:
        """Clamp selected/scroll_offset to valid range. Must hold state_lock."""
        nonlocal scroll_offset, selected
        total = len(last_data)
        if total == 0:
            selected = 0
            scroll_offset = 0
            return
        selected = max(0, min(selected, total - 1))
        vis = _vis_rows()
        if selected < scroll_offset:
            scroll_offset = selected
        elif selected >= scroll_offset + vis:
            scroll_offset = selected - vis + 1
        scroll_offset = max(0, min(scroll_offset, max(0, total - vis)))

    # ── input thread ──────────────────────────────────────────────────────
    def input_loop() -> None:
        nonlocal current_sort, last_data, scroll_offset, selected
        nonlocal export_msg, export_timer

        if sys.platform == "win32":
            _input_loop_windows()
            return
        _input_loop_unix()

    def _scroll(delta: int) -> None:
        """Must be called while holding state_lock."""
        nonlocal selected
        selected = max(0, min(selected + delta, len(last_data) - 1))
        _clamp()

    def _handle_key(ch: str) -> None:
        """Must be called while holding state_lock."""
        nonlocal current_sort, last_data, export_msg, export_timer
        if ch == "q":
            quit_event.set()
        elif ch == "c":
            current_sort = "cpu"
            last_data = sort_stats(last_data, current_sort)
        elif ch == "m":
            current_sort = "mem"
            last_data = sort_stats(last_data, current_sort)
        elif ch == "n":
            current_sort = "net"
            last_data = sort_stats(last_data, current_sort)
        elif ch == "b":
            current_sort = "blk"
            last_data = sort_stats(last_data, current_sort)
        elif ch == "e":
            snap = list(last_data)
            # release lock during IO
            state_lock.release()
            try:
                p   = export_csv(snap)
                export_json(snap)
                msg = f"Exported → {p} + .json" if p else "Export failed (no data)"
            finally:
                state_lock.acquire()
            export_msg   = msg
            export_timer = time.time()
        elif ch == "a":
            with _alerts_lock:
                _alerts.clear()

    def _input_loop_unix() -> None:
        import termios, tty, select as sel_mod
        fd  = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not quit_event.is_set():
                ready = sel_mod.select([sys.stdin], [], [], 0.1)[0]
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    # FIX: read up to 4 bytes for full escape sequence (e.g. ESC[5~)
                    seq = ""
                    deadline = time.time() + 0.05
                    while time.time() < deadline:
                        if sel_mod.select([sys.stdin], [], [], 0.01)[0]:
                            seq += sys.stdin.read(1)
                            if seq and seq[-1].isalpha() or seq.endswith("~"):
                                break
                        else:
                            break
                    with state_lock:
                        if   seq in ("[A", "OA"):    _scroll(-1)
                        elif seq in ("[B", "OB"):    _scroll(1)
                        elif seq in ("[5~", "[5"):   _scroll(-10)
                        elif seq in ("[6~", "[6"):   _scroll(10)
                    continue
                with state_lock:
                    _handle_key(ch.lower())
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _input_loop_windows() -> None:
        import msvcrt
        while not quit_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    ch2 = msvcrt.getwch()
                    with state_lock:
                        if   ch2 == "H":  _scroll(-1)
                        elif ch2 == "P":  _scroll(1)
                        elif ch2 == "I":  _scroll(-10)   # PgUp
                        elif ch2 == "Q":  _scroll(10)    # PgDn
                else:
                    with state_lock:
                        _handle_key(ch.lower())
            else:
                time.sleep(0.05)

    # ── layout builder ────────────────────────────────────────────────────
    def build() -> Layout:
        with state_lock:
            data    = list(last_data)
            sel     = selected
            offset  = scroll_offset
            sort    = current_sort
            dok     = docker_ok
            emsg    = export_msg
            etimer  = export_timer
            _clamp()

        vis   = _vis_rows()
        nalerts = len(_alerts)
        # FIX: alert panel size is dynamic, not hardcoded
        alert_size = min(nalerts + 3, 9) if nalerts > 0 else 3

        layout = Layout()
        layout.split_column(
            Layout(make_header(sort, nalerts, len(data), refresh,
                               offset, vis, dok), size=4),
            Layout(Panel(make_table(data, sort, offset, vis, sel),
                         border_style=THEME["border"],
                         style=f"on {THEME['bg']}",
                         padding=(0, 0)), name="main"),
            Layout(make_alerts_panel(), size=alert_size),
            Layout(make_help_bar(emsg, etimer), size=3),
        )
        return layout

    # ── fetch loop (runs on main thread between Live updates) ─────────────
    inp_thread = threading.Thread(target=input_loop, daemon=True)
    inp_thread.start()

    with Live(build(), refresh_per_second=4, screen=True, console=console) as live:
        next_fetch = 0.0
        while not quit_event.is_set():
            now = time.time()
            if now >= next_fetch:
                try:
                    new_data = sort_stats(get_all_stats(client), sort_by)
                    with state_lock:
                        docker_ok  = True
                        last_data  = new_data
                        # preserve sort that may have changed interactively
                        sort_by    = current_sort
                        _clamp()
                except Exception:
                    with state_lock:
                        docker_ok = False
                next_fetch = now + refresh
            live.update(build())
            time.sleep(0.1)

    quit_event.set()
    inp_thread.join(timeout=2)

# ─────────────────────────────────────────────
#  SNAPSHOT
# ─────────────────────────────────────────────

def run_snapshot(client: docker.DockerClient, sort_by: str, fmt: str) -> None:
    console.print(f"[{THEME['muted']}]Fetching stats…[/]")
    data = sort_stats(get_all_stats(client), sort_by)
    if not data:
        console.print(f"[bold {THEME['warn']}]No running containers found.[/]")
        return
    if fmt == "json":
        console.print_json(json.dumps(data, indent=2, default=str))
    elif fmt == "csv":
        path = export_csv(data)
        if path:
            console.print(f"[bold {THEME['ok']}]✔ Exported to {path}[/]")
        else:
            console.print(f"[bold {THEME['crit']}]✗ Export failed.[/]")
    else:
        table = make_table(data, sort_by, 0, len(data), -1)
        console.print(Panel(table,
            title=f"[bold {THEME['accent']}]⚡ TOPDOCK SNAPSHOT[/]",
            border_style=THEME["accent"]))

# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="topdock",
        description="⚡ TopDock — Docker Stats Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  topdock                        # live dashboard, sort by CPU
  topdock --sort mem             # sort by memory
  topdock --refresh 5            # refresh every 5 seconds
  topdock --alert 90             # alert threshold 90%%
  topdock --snapshot             # one-shot table output
  topdock --snapshot --format json
  topdock --host tcp://192.168.1.10:2375  # remote docker host
        """,
    )
    p.add_argument("--version", "-V", action="version", version=f"topdock {__version__}")
    p.add_argument("--sort",    "-s", choices=["cpu","mem","net","blk","name"], default="cpu",
                   metavar="FIELD", help="Sort column: cpu|mem|net|blk|name  (default: cpu)")
    p.add_argument("--refresh", "-r", type=float, default=2.0,
                   metavar="SEC",   help="Stats refresh interval in seconds  (default: 2)")
    p.add_argument("--alert",   "-a", type=float, default=80.0,
                   metavar="PCT",   help="Alert threshold %%  (default: 80)")
    p.add_argument("--snapshot",      action="store_true",
                   help="Print stats once and exit (no live UI)")
    p.add_argument("--format",  "-f", choices=["table","json","csv"], default="table",
                   help="Output format for --snapshot  (default: table)")
    p.add_argument("--host",          default=None,
                   metavar="URL",   help="Docker host URL  (default: local socket)")
    return p.parse_args()

def main() -> None:
    args = parse_args()

    # validate ranges
    if args.refresh < 0.5:
        console.print(f"[bold {THEME['warn']}]--refresh must be >= 0.5s[/]")
        sys.exit(1)
    if not (0 < args.alert <= 100):
        console.print(f"[bold {THEME['warn']}]--alert must be between 1 and 100[/]")
        sys.exit(1)

    try:
        client = (docker.DockerClient(base_url=args.host)
                  if args.host else docker.from_env())
        client.ping()
    except docker.errors.DockerException as e:
        console.print(f"[bold {THEME['crit']}]✗ Cannot connect to Docker:[/] {e}")
        console.print(f"[{THEME['muted']}]Is Docker running? Try: sudo systemctl start docker[/]")
        sys.exit(1)

    if args.snapshot:
        run_snapshot(client, args.sort, args.format)
    else:
        try:
            run_dashboard(client, args.refresh, args.sort, args.alert)
        except KeyboardInterrupt:
            pass
        finally:
            console.print(f"\n[bold {THEME['accent']}]⚡ TopDock terminated.[/]")

if __name__ == "__main__":
    main()

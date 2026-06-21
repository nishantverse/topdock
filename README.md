# ⚡ TopDock

A real-time Docker container stats dashboard for your terminal — CPU, memory, network, and block I/O at a glance.

![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776ab?style=flat-square)
![License MIT](https://img.shields.io/badge/license-MIT-0DB82F?style=flat-square)
![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-0078D4?style=flat-square)

---

## Install

```bash
# recommended
pipx install topdock

# or
pip install topdock

# or directly from source
pipx install git+https://github.com/yourusername/topdock.git
```

**Requires:** Python 3.10+, Docker

---

## Usage

```bash
topdock                                 # live dashboard
topdock --sort mem                      # sort by memory
topdock --refresh 5 --alert 90          # custom refresh and alert threshold
topdock --snapshot --format json        # one-shot JSON output
topdock --host tcp://192.168.1.10:2375  # remote Docker host
topdock --version
```

---

## Keyboard Controls

| Key              | Action            |
|------------------|-------------------|
| `↑` / `↓`       | Scroll rows       |
| `PgUp` / `PgDn` | Scroll 10 rows    |
| `c`              | Sort by CPU       |
| `m`              | Sort by Memory    |
| `n`              | Sort by Network   |
| `b`              | Sort by Block I/O |
| `e`              | Export CSV + JSON |
| `a`              | Clear alerts      |
| `q`              | Quit              |

---

## Uninstall

```bash
pipx uninstall topdock
# or
pip uninstall topdock
```

---

## License

MIT

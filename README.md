**English** | [简体中文](README.zh-CN.md)

# omni

> A **stdlib-only** local **indexing / cataloging / auditing** CLI.
> It consolidates files scattered across several machines and network shares into one
> fully text-searchable local index, then layers a lightweight "knowledge layer" on top
> so that the things you clip along the way turn into maintainable pages.

Single-file implementation, runs on Python 3.9+, and **depends on no third-party packages**
(SMB mounting, full-text search, web clipping, knowledge-base sync, and scheduled auditing
are all included). Built for developers who have a few computers plus a NAS at home or in
the office and want to settle "where do my files live, and which document contains that
sentence" once and for all.

---

## What problem does it solve?

- Files are spread across several hosts, several SMB shares and a pile of backup disks.
  Finding anything means relying on memory and walking the tree with `find`.
- You want to search by **document content** ("was that quote 8% or 8.5%?"), not by filename.
- You want a periodic answer to: is the tunnel up, is a disk almost full, is the backup
  falling behind again, has the index gone stale?
- The pages and clippings you save keep piling up, and you need a light
  "inbox → organize" flow rather than yet another cloud drive.

omni packs the entire index into **one SQLite file** (FTS5 full text) and gives you a
uniform `find` / `recent` / `du` / `audit` / `report` command set — and it
**never modifies your original files**.

---

## Safety model (important — read this first)

**S1 — omni never deletes, moves, or modifies any user file.**

- It only writes inside the configured directories (under `~/wg/` by default): the database,
  the asset inventory, audit reports, knowledge pages, state and logs.
- `omni save` (the inbox) writes only *copies* of a page's body or of a file into `wiki/raw/`;
  the source file stays read-only forever.
- "Junk directories / duplicate files / stale backups" found by an audit are
  **reported, never removed** — you copy the command out and run it yourself.
- Destructive operations (`omni db rebuild`) require an explicit `--yes`.
- That is why it is safe to leave running on a schedule: it will not touch your data behind
  your back.

---

## Installation

> 📖 **Installing on several computers? Read [INSTALL.md](INSTALL.md)** — the complete guide:
> environment checks, share mounting (SMB/NFS/local disk), multi-machine `MOUNTS` setup,
> scheduled automation, and troubleshooting. This section is only the quick start.

Requires Python 3.9+ (3.10+ recommended; SQLite must have FTS5 enabled — the macOS system
build and the official Python distributions both qualify).

```sh
git clone https://github.com/jiangdong17/omni.git ~/wg
cd ~/wg
chmod +x omni.sh
ln -s "$PWD/omni.sh" /usr/local/bin/omni     # or add this directory to PATH
omni --version
```

The first run will tell you the config is missing — create it in the next step.

### Configuration

Copy the example config and adapt it to your own mounts:

```sh
cp omnirc.py.example omnirc.py
cp AGENTS.md.example AGENTS.md        # optional: working rules for AI tools
$EDITOR omnirc.py
```

At minimum, edit `MOUNTS`: one line per host per share. Once it points at your directories:

```sh
omni doctor      # self-check: config / mounts / database / interpreter / dependencies
omni index       # build the index (full on first run, incremental afterwards)
omni find keyword
```

> The default working root is `~/wg`. Point it elsewhere with the `OMNI_HOME` environment
> variable: `OMNI_HOME=~/omni omni doctor`.

---

## Languages

The user interface is localised. Resolution order:

1. `--lang en|zh` (place it **before** the subcommand: `omni --lang en find report`)
2. the `OMNI_LANG` environment variable
3. `LC_ALL` / `LC_MESSAGES` / `LANG` — a `zh*` locale selects Chinese
4. otherwise **English**

```sh
omni --lang en find report          # one-off
export OMNI_LANG=zh                 # per shell
omni --lang zh --help               # the help text is localised too
```

Translations live in **`omni_i18n.py`**. The key is the Chinese source string used in
`omni.py`; the value is the target-language text. A missing key falls back to the Chinese
source, so a partial translation can never break the program.

**Adding a language:** copy the `CATALOG["en"]` block, add it under your language code,
and translate the **values only** — never the keys, and never drop a printf placeholder
(`%s` / `%d` / `%%`).

```sh
# verify no key is missing and no placeholder was lost
python3 - <<'EOF'
import json, re, ast, sys
sys.path.insert(0, ".")
from omni_i18n import CATALOG
keys = {n.args[0].value for n in ast.walk(ast.parse(open("omni.py", encoding="utf-8").read()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "T"
        and n.args and isinstance(n.args[0], ast.Constant)}
for lang, cat in CATALOG.items():
    PCT = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]")
    print(lang, "missing:", len(keys - set(cat)),
          "| placeholder mismatch:",
          sum(1 for k, v in cat.items() if sorted(PCT.findall(k)) != sorted(PCT.findall(v))))
EOF
```

---

## Command reference

| Command | Purpose |
| --- | --- |
| `omni doctor` | Self-check: config, mounts, database, interpreter, dependencies |
| `omni mounts` | List mounts + online status + last scan time |
| `omni selftest` | Core-logic self-test (11 assertions, never touches your data) |
| `omni index [--full\|--content\|--bg\|--status]` | Build/update the index (incremental by default; `--content` extracts bodies, `--bg` runs in background) |
| `omni find <term> [--pack]` | Search; ≥3 chars goes to full text, ≤2 chars to filenames; `--pack` bundles results + snippets + excerpts |
| `omni recent [--days N]` | Recently changed files |
| `omni du [--top N]` | Space usage ranking (by top-level directory) |
| `omni open <n>` | Reveal the n-th `find` result in Finder |
| `omni save <url\|file>` | Inbox: save a page/file into `wiki/raw/` (source stays read-only) |
| `omni inv init\|sync\|stats\|list` | L1 asset cards (profile key directories, back-filled with index stats) |
| `omni sync push\|pull\|status\|mark` | L3 two-way sync with a knowledge base (e.g. ima) |
| `omni audit [--push]` | L4 proactive audit (11 checks + auto-commit of the knowledge layer) |
| `omni report` | List / open audit reports |
| `omni git log\|status\|commit` | Knowledge-layer versioning |
| `omni ima list\|ls\|pull\|push` | Direct ima OpenAPI access (optional) |
| `omni db stats\|vacuum\|rebuild` | Database maintenance (`rebuild` needs `--yes`) |

Global options: `--version`, `--lang en|zh`, `-h`.

---

## Design trade-offs (the pits we fell into)

These are conclusions hardened by actually running against 800k+ files. Worth reading
before you change the code:

- **FTS5 trigram inflates Chinese text by roughly 50×.** The content index therefore has to
  cap length (`CONTENT_KEEP_CHARS`, 20K characters per file by default) and restrict the
  extension whitelist, or the database spirals out of control.
- **Pitfall of the external-content mode:** with `content='files'`, the FTS table must contain
  every column of the FTS index, otherwise `snippet()` fails with `no such column`. The
  indexing process writes `files.content` directly and triggers keep FTS in sync.
- **A dead mount drags everything down.** For an SMB mount whose peer is powered off,
  `statvfs` / `os.walk` enter an **uninterruptible kernel sleep** (not even a signal kills it).
  So every probe runs in a **child process + `start_new_session` + timeout `killpg`**; when a
  host is unreachable, the disk/backup/junk/index checks are skipped entirely.
- **Background jobs must fully detach from the session:** `stdin=DEVNULL` plus a new session,
  or a managed interpreter reports `Bad file descriptor`. `nohup &` gets reaped together with
  the session, so `index --bg` detaches internally instead.
- **The index lock must verify that the process is alive** (judging by mtime alone lets a
  stale lock from a dead process block you for an hour).
- **Give large shares their own scan limit.** One measured Windows C: drive with 310k files
  needs ~1625s, so the 600s default limit is guaranteed to break (resumable, but repeatedly).
  Set `max_scan_s` for that mount.

---

## Directory layout (created at runtime, all under `OMNI_HOME`)

```
~/wg/
├── omni.py            # main program (single file, stdlib only)
├── omni_i18n.py       # translation catalog (key = Chinese source string)
├── omnirc.py          # configuration (single source of truth)
├── omni.sh            # launcher script
├── README.md          # this file
├── INSTALL.md         # ★ installation & multi-machine setup guide
├── CHANGELOG.md       # release notes
├── omni.db            # SQLite index (the only data file)
├── inventory/         # L1 asset cards
├── reports/           # audit reports
├── wiki/
│   ├── _index.md      # knowledge-layer navigation
│   └── raw/           # inbox (where `omni save` lands)
├── AGENTS.md          # working-rule declaration for AI tools
└── .omni/             # state / logs / locks / backups
```

---

## License

MIT — see [LICENSE](LICENSE). Release notes: [CHANGELOG.md](CHANGELOG.md).

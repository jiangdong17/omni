**English** | [简体中文](INSTALL.zh-CN.md)

# omni — Installation & Multi-Machine Setup Guide

> For first-time users. Read from Chapter 0 to the end and you'll have it installed on one
> machine, and the files scattered across 2–5 computers + a NAS consolidated into a single
> full-text-searchable index.
>
> Python 3.9+ is all you need throughout — no third-party packages.

---

## Chapter 0 · First, Understand What omni Is — and What It Is Not

This chapter matters. **90% of configuration confusion comes from misunderstanding omni's boundaries.**

### What omni does

```
       ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
       │ Computer A  │   │ Computer B  │   │    NAS      │
       │ (Windows)   │   │ (Linux)     │   │   (SMB)     │
       └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
              │                 │                 │
              └──────── SMB / NFS / LAN ─────────┘
                                │
                    ┌───────────▼───────────┐
                    │  "Index host": a Mac  │   ← omni runs here
                    │                       │
                    │  ~/mnt/laptop-c  ←mount┤
                    │  ~/mnt/nas-home  ←mount┤
                    │  ~/mnt/nas-photo ←mount┘
                    │         ↓
                    │   omni index  →  omni.db (one SQLite file)
                    │         ↓
                    │   omni find <keyword>  →  instantly tells you where the file is
                    └───────────────────────┘
```

omni **only does three things**:

1. **Traverse** directories already mounted on this machine (or on the local disk);
2. Write "path / size / mtime / optional body text" into **a single SQLite file**;
3. Let you query it with `find` / `recent` / `du` / `audit`.

### What omni does NOT do (important)

| It does **not** do this | Explanation |
|---|---|
| ❌ No syncing / moving files | It **never** modifies your files (hard invariant S1); it's just an indexer |
| ❌ Not responsible for making computers reach each other | Network reachability and share exposure are **your job** (SMB / NFS / LAN) |
| ❌ Provides no cloud service | Data lives 100% in the local `omni.db`, uploaded nowhere |
| ❌ Does not merge indexes across machines | One index per machine; each machine has its own DB (see Chapter 8) |

### So what "configuring several computers" really means

> **Configuring several computers = ① make each machine's directories reachable on the index host → ② write them into the config one by one → ③ build the index**

Chapter 3 solves ①, Chapter 4 solves ②, Chapter 5 solves ③.

---

## Chapter 1 · Requirements

### 1.1 Minimum requirements

| Item | Requirement |
|---|---|
| OS | **macOS or Linux** (see 1.4 for the note on Windows) |
| Python | **3.9 or later** |
| SQLite | Must have **FTS5** enabled and support the **trigram** tokenizer |
| Disk | `omni.db` is roughly "number of indexed files × 1–3 KB", plus the content index (see 5.5) |
| Network | The index host can reach the other machines' shares (SMB/NFS) |

### 1.2 Check Python and FTS5 (★ you must pass this first)

```sh
python3 --version          # requires ≥ 3.9
python3 - <<'PY'
import sqlite3
print("SQLite:", sqlite3.sqlite_version)
c = sqlite3.connect(":memory:")
c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
c.execute("INSERT INTO t VALUES('hello full text search')")
ok = c.execute("SELECT count(*) FROM t WHERE t MATCH '\"full text search\"'").fetchone()[0] == 1
print("FTS5 + trigram:", "available ✅" if ok else "unavailable ❌")
PY
```

If it prints `FTS5 + trigram: unavailable ❌`, your SQLite is too old or was compiled without FTS5:

- **macOS's bundled Python 3.9.6** usually already works; if not, install the official Python 3.12+ (the python.org installer ships a complete SQLite).
- **Debian/Ubuntu**: `sudo apt install python3 libsqlite3-0` (the system Python generally already includes FTS5).
- **Alpine**: requires `sqlite` compiled with FTS5; consider switching to the official Python image.

> omni itself re-checks this in `omni doctor`, so you can just run that after installing to confirm.

### 1.3 Supported systems

| System | As index host | Notes |
|---|---|---|
| macOS | ✅ Recommended | `mount_smbfs` + Keychain for passwords, smoothest experience |
| Linux | ✅ | `mount.cifs` / `mount.nfs`, auto-mounted via `fstab` or systemd |
| Windows | ⚠️ See below | Not recommended as an index host |

### 1.4 About Windows (please read)

omni depends on POSIX behavior: using the `mount` command to determine mount status, and `open` to launch Finder / the file manager.
Therefore it **cannot serve as an index host on Windows**.

But Windows **is very well suited as "the data source being indexed"**: its shared directories can be mounted
over SMB by a Mac/Linux index host and indexed there. Recommended topology:

```
Windows machine (exposes shares only) →  SMB  →  Mac index host (runs omni)  →  search / audit
```

---

## Chapter 2 · Installation (on the index host)

### 2.1 Choose the "index host"

Pick a machine that is **on for long periods** and can reach the other machines' shares. Usually your main
laptop, or an always-on Mac mini / small Linux box.

> Why do you need "one" machine instead of installing it on every machine? See Chapter 8.

### 2.2 Get the code

```sh
# Option 1: git
git clone https://github.com/jiangdong17/omni.git ~/wg
cd ~/wg

# Option 2: offline tarball (copy the tar.gz to the target machine)
tar -xzf omni-v1.1.0.tar.gz
mv omni-repo ~/wg && cd ~/wg

chmod +x omni.sh
```

**Why is it installed in `~/wg` by default?** omni uses the `OMNI_HOME` environment variable to locate itself
(default `~/wg`); the config, database, reports, and logs all live under that directory. To change the
location, see 2.3.

### 2.3 Custom root directory (optional)

If you don't want `~/wg`, set this in your shell config:

```sh
# ~/.zshrc or ~/.bashrc
export OMNI_HOME="$HOME/omni"
```

From then on every command reads/writes `$OMNI_HOME`, and the config filename is fixed as `$OMNI_HOME/omnirc.py`.

### 2.4 Create the config

```sh
cp omnirc.py.example omnirc.py
cp AGENTS.md.example AGENTS.md      # optional: working rules for AI tools
mkdir -p ~/mnt                      # mount-point root (default MNT, see Chapter 4)
$EDITOR omnirc.py                   # no need to change it yet; Chapter 4 comes later
```

### 2.5 Put it on your PATH

```sh
# Option 1: symlink (recommended)
ln -s "$PWD/omni.sh" /usr/local/bin/omni      # may need sudo

# Option 2: add to PATH
echo 'export PATH="$HOME/wg:$PATH"' >> ~/.zshrc && exec zsh
```

Verify:

```sh
omni --version      # → omni 1.1.0
```

### 2.6 First self-check

```sh
omni doctor     # overall check: environment + config + mounts + database
omni selftest   # core-logic self-test (11 assertions, doesn't touch your data)
```

If `selftest` prints `all 11 assertions passed`, the program is well compatible with your Python.

At this point `doctor` will show "0 mount points" — that's normal, because nothing is configured yet. Continue to Chapters 3 and 4.

### 2.7 Language (optional)

omni's interface follows your system locale by default: a `LANG` / `LC_ALL` starting with `zh`
selects Chinese, otherwise it uses **English**. To force a language:

```sh
omni --lang en --help              # one-off; --lang must come before the subcommand
export OMNI_LANG=en                # persistent for the current shell
```

| Priority | Source | Notes |
| --- | --- | --- |
| 1 (highest) | `--lang en\|zh` | place it before the subcommand |
| 2 | `OMNI_LANG` | environment variable; `export` it to make it stick |
| 3 | `LC_ALL` / `LC_MESSAGES` / `LANG` | `zh*` → Chinese |
| 4 (fallback) | none | English |

All translations live in `omni_i18n.py` (key = the Chinese source string in `omni.py`).
**A missing key falls back to the Chinese source, so an incomplete translation can never
make the program fail.** To add a language, see the Languages section of [README.md](README.md).

> When both language editions are present: [README.md](README.md) and this file are the
> English versions; [README.zh-CN.md](README.zh-CN.md) and
> [INSTALL.zh-CN.md](INSTALL.zh-CN.md) are the Chinese ones.

---

## Chapter 3 · Making Other Computers' Directories Visible (the core of multi-machine)

omni indexes the **local filesystem**. So the first step is: mount the other machines' shares under
`~/mnt/<label>/` on the index host.

> ★ **Naming rule**: the mount directory name must equal the `label` in the config.
> For example, `label="laptop-c"` → it must be mounted at `~/mnt/laptop-c`. This is the only way omni finds it.

### 3.1 First, expose shares on the machines to be indexed

**Windows** (as a data source): share the directories you want, and make sure the account can read/write.

- Using the admin shares `C$ / D$ / E$` is easiest, but it requires an account with a **non-empty password**;
- Windows' default policy forbids empty-password accounts from logging on over the network (SMB/RDP/SSH will all fail),
  so it's best to create a dedicated remote-access account and set a password.

**Linux**: install Samba and configure `[share]` sections in `smb.conf`; or use NFS (see 3.5).

**macOS**: System Settings → General → Sharing → File Sharing, then add a directory.

### 3.2 macOS mounting SMB (index host is a Mac)

```sh
mkdir -p ~/mnt/laptop-c
mount_smbfs //user@192.168.1.20/C\$ ~/mnt/laptop-c
```

- It prompts for a password the first time; **storing it in the Keychain is recommended** (create a "Network password" item in Keychain Access),
  after which `mount_smbfs` picks it up automatically and you never write the password in plaintext.
- When the share name contains Chinese characters or spaces, it **must be URL-encoded**, otherwise you get `URL parsing failed`:

```sh
# share name "我的文档" → %E6%88%91%E7%9A%84%E6%96%87%E6%A1%A3
mount_smbfs "//user@host/%E6%88%91%E7%9A%84%E6%96%87%E6%A1%A3" ~/mnt/nas-docs
```

- Unmount: `umount ~/mnt/laptop-c` (if it hangs, use `diskutil unmount force ~/mnt/laptop-c`).

### 3.3 Linux mounting SMB (index host is Linux)

```sh
sudo apt install cifs-utils
mkdir -p ~/mnt/laptop-c

# keep credentials in a separate file (mode 600), so the password doesn't hit the command line / process table
cat > ~/.smbcred-laptop <<'EOF'
username=youruser
password=yourpass
EOF
chmod 600 ~/.smbcred-laptop

sudo mount -t cifs //192.168.1.20/C$ ~/mnt/laptop-c \
  -o credentials=$HOME/.smbcred-laptop,uid=$(id -u),gid=$(id -g),iocharset=utf8
```

### 3.4 Make mounts recover automatically after a reboot

A manual `mount` is gone after a reboot. Three ways to automate it:

**Linux — write it into `/etc/fstab`**

```
//192.168.1.20/C$  /home/you/mnt/laptop-c  cifs  credentials=/home/you/.smbcred-laptop,uid=1000,gid=1000,iocharset=utf8,_netdev,nofail  0  0
```

`_netdev` (wait for the network) + `nofail` (don't block boot if it can't mount) are the key.

**macOS — a LaunchAgent that mounts at login**

Create `~/Library/LaunchAgents/local.omni.mount.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.omni.mount</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>sleep 30; sh $HOME/wg/mount-all.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/omni-mount.log</string>
  <key>StandardErrorPath</key><string>/tmp/omni-mount.err</string>
</dict>
</plist>
```

Pair it with `~/wg/mount-all.sh` (write your own; call `mount_smbfs` line by line, checking first whether it's already mounted):

```sh
#!/bin/sh
# idempotent mount: skip ones already mounted
m() { mount | grep -q " on $2 " || mount_smbfs "$1" "$2"; }
mkdir -p ~/mnt/laptop-c ~/mnt/nas-home
m "//user@192.168.1.20/C\$" ~/mnt/laptop-c
m "//user@192.168.1.31/home" ~/mnt/nas-home
```

Load it: `launchctl load ~/Library/LaunchAgents/local.omni.mount.plist`

> ⚠️ macOS TCC privacy protection: **processes launched by launchd cannot read `~/Desktop`, `~/Documents`, or `~/Downloads`**.
> So always put scripts and configs in ordinary directories like `~/wg`, never on the Desktop.

### 3.5 Another route: NFS

Lighter-weight between Linux/NAS machines:

```sh
# server /etc/exports:  /volume1/data  192.168.1.0/24(rw,sync,no_subtree_check)
sudo mount -t nfs 192.168.1.31:/volume1/data ~/mnt/nas-data
```

### 3.6 The simplest route: local disk / external drive

If you can copy a machine's data to the index host yourself (or it's already an external drive),
just attach it to the index host and mount it at `~/mnt/<label>` — no network share needed at all:

```sh
mkdir -p ~/mnt/backup-disk
# after the external drive is mounted, symlink or bind-mount it in
sudo mount --bind /Volumes/Backup ~/mnt/backup-disk     # macOS/Linux
```

### 3.7 Mount checks

```sh
ls ~/mnt/                     # you should see the label directories
mount | grep mnt              # confirm they're really mounted (not empty directories)
```

> ★ **The empty-directory trap**: when an SMB mount fails, `~/mnt/xxx` is an **empty directory** rather than an error.
> omni's `doctor` reports it as "not mounted" — don't let the empty directory fool you.

---

## Chapter 4 · Configuring MOUNTS (writing in each machine)

Edit the `MOUNTS` list in `~/wg/omnirc.py`. Each entry corresponds to one shared directory.

### 4.1 Field reference

| Field | Required | Description |
|---|---|---|
| `label` | ✅ | Unique identifier. **Must match the `~/mnt/<label>` directory name**. Appears in search results, cards, and reports |
| `host` | ✅ | Logical hostname, used for grouping and "is the host reachable" checks |
| `ip` | ✅ | The host's address, used for TCP 445 reachability probes during audit |
| `share` | ✅ | Share name (recorded only, for your own bookkeeping) |
| `content` | | `True` = do **body** extraction for this share (slow, bloats the DB); `False` (default) = index only filename/size/mtime |
| `max_scan_s` | | Per-mount hard scan limit (seconds). If omitted, the global `SCAN_HARD_LIMIT_S` (default 600) is used |
| `max_depth` | | Recursion depth limit for content indexing, default 12 |

### 4.2 `content` on or off? (the decision that affects the experience most)

| Directory type | Recommendation | Reason |
|---|---|---|
| Document directories (contracts, notes, reports) | ✅ On | This is exactly where "full-text search" earns its keep |
| Code repositories | ✅ On | Searching function names/comments is very useful |
| Photos / videos / music | ❌ Off | Media extensions are already in the exclusion list; enabling it is wasted effort |
| System drive / the whole C: drive | ❌ Off | Huge file count (a real Windows C: drive measured at 310k files); enabling body text drags on for a long time |

### 4.3 A complete example (3 machines + NAS)

```python
MNT = "~/mnt"

MOUNTS = [
    # ── Laptop (Windows, over LAN SMB) ──
    dict(label="laptop-c",  host="laptop", ip="192.168.1.20", share="C$", content=False,
         max_scan_s=2400),          # the system drive has a huge number of files; raise its scan limit separately
    dict(label="laptop-d",  host="laptop", ip="192.168.1.20", share="D$", content=True),

    # ── Desktop (Linux) ──
    dict(label="desktop-d", host="desktop", ip="192.168.1.21", share="data", content=True,
         max_depth=8),

    # ── NAS ──
    dict(label="nas-home",  host="nas", ip="192.168.1.31", share="home",  content=True),
    dict(label="nas-photo", host="nas", ip="192.168.1.31", share="photo", content=False),
    dict(label="nas-video", host="nas", ip="192.168.1.31", share="video", content=False),
]
```

Also don't forget the audit probe targets (`AUDIT.tunnel_targets`), so omni knows which machines to ping:

```python
AUDIT = {
    "tunnel_targets": [("laptop", "192.168.1.20", 445), ("desktop", "192.168.1.21", 445),
                       ("nas", "192.168.1.31", 445)],
    ...
}
```

### 4.4 Validate the config

```sh
omni mounts     # list each label + mount status + last scan time
omni doctor     # "N mount points, M / N mounted" should show no ⚠
```

---

## Chapter 5 · Building the Index

### 5.1 First full run

```sh
omni index            # the first run is a full index automatically; every later call is incremental
```

Small datasets (tens of thousands of files) take tens of seconds; large ones (hundreds of thousands to a million files) may take from a dozen minutes to half an hour.

### 5.2 Run in the background + watch progress

Don't tie up your terminal for a big index:

```sh
omni index --bg       # run in the background, returns immediately
omni index --status   # watch progress (current directory, files scanned, elapsed time)
```

### 5.3 Content indexing (body-text search)

```sh
omni index --content            # extract body text for mounts with content=True in the config
omni index --content --limit 5000   # at most 5000 files per mount point (a trial run first)
omni index laptop-d             # process only the specified mount point
```

Body extraction supports: `.md .txt .csv .docx .xlsx .doc .rtf .odt .html .xml`, etc. (see `CONTENT_EXT`).

### 5.4 Incremental and full re-scan

```sh
omni index            # incremental: only process changed files
omni index --full     # full re-scan (use when change detection is unreliable)
```

### 5.5 Performance and size reference (measured values)

| Scenario | Measured |
|---|---|
| A single Windows C: drive, 310k files | ~**1625 seconds** (so the default 600s limit isn't enough; you need `max_scan_s=2400`) |
| Full 836k entries (multi-machine + NAS) | ~**20 minutes** |
| `omni.db` size | 836k entries + partial body text ≈ **3.5 GB** |

**Levers to control size** (all in `omnirc.py`):

```python
CONTENT_EXT        = {...}    # only extract body text for these extensions (narrow it)
CONTENT_KEEP_CHARS = 20*1024  # keep only the first 20K characters of each file
CONTENT_MAX_BYTES  = 2*1024*1024  # don't extract body text from files over 2MB
```

> ⚠️ FTS5's **trigram tokenizer inflates Chinese text by about 50×**, so `CONTENT_KEEP_CHARS`
> must never be loosened. If the DB really does balloon, rebuild it with `omni db rebuild` (requires `--yes`).

---

## Chapter 6 · Daily Use

### 6.1 Search

```sh
omni find quote                 # ≥3 characters → full-text search
omni find C++                   # special characters won't crash it
omni find contract --ext .docx .pdf  # only these types
omni find meeting --mount nas-home  # only within a certain mount point
omni find proposal --recent 7d       # only modified in the last 7 days
omni find proposal --pack            # package results + summaries + body excerpts into md (to feed an AI as context)
```

> Rule: **query terms ≤ 2 characters** use filename matching (`LIKE`); **≥ 3 characters** use the full-text index (`MATCH`).
> This is to work around trigram's ineffectiveness on short terms.

### 6.2 Other common commands

```sh
omni recent 3d          # files changed in the last 3 days
omni du --top 20        # usage ranking (by top-level directory)
omni open 3             # open the 3rd item of the last find result in Finder / the file manager
omni report             # view/open the audit report
```

### 6.3 Asset cards (keeping a separate record for important directories)

```sh
omni inv init           # generate cards from KEY_DIRS in the config
omni inv sync           # backfill cards with measured index data (size / file count)
omni inv list           # list all cards
```

> Each card has a "## Manual Judgement" block that omni **never writes** — it's left for humans only.

### 6.4 Audit (proactive health check)

```sh
omni audit              # 11 checks: host reachability / mounts / disk / backup lag / junk / duplicates / index freshness…
```

It **only reports, never deletes on your behalf**. Junk directories, duplicate files, and lagging backups it finds are only output as commands for you to decide on.

### 6.5 Knowledge layer (optional)

```sh
omni save https://example.com/article    # save a web page into the inbox wiki/raw/
omni save ~/Downloads/some-file.pdf      # a copy of the file goes into the inbox (source file stays read-only)
omni git log                             # version history of the knowledge layer
```

---

## Chapter 7 · Automation (the key to long-term operation)

Installed and working, but **running automatically every day** is omni's full form.

### 7.1 Auto-mount on boot/login

See 3.4. Key points: `nofail` / idempotent / wait for the network.

### 7.2 Daily automatic "index + audit"

**macOS — LaunchAgent**

`~/Library/LaunchAgents/local.omni.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.omni.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>$HOME/wg/omni.sh index --bg &amp;&amp; $HOME/wg/omni.sh audit</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/omni.log</string>
  <key>StandardErrorPath</key><string>/tmp/omni.err</string>
</dict>
</plist>
```

```sh
launchctl load ~/Library/LaunchAgents/local.omni.daily.plist
```

First run `omni.sh index --bg && omni.sh audit` manually to confirm the paths and permissions are fine.

**Linux — cron**

```cron
# crontab -e
0 9 * * *  cd $HOME/wg && ./omni.sh index --bg >> $HOME/wg/.omni/daily.log 2>&1
15 9 * * * cd $HOME/wg && ./omni.sh audit    >> $HOME/wg/.omni/daily.log 2>&1
```

### 7.3 Don't let it hang on a peer being powered off

This is the most common pitfall in multi-machine setups: **after a machine is powered off, its SMB mount point makes `statvfs` / `os.walk`
enter an uninterruptible kernel sleep (even Ctrl-C can't kill it).**

omni already handles this:

- All probing of mount points happens in a **child process**, with a timeout, and `killpg` on timeout;
- During audit it first does a TCP reachability probe, and if the **host is unreachable** it skips that machine's disk/backup/junk/index checks.

The only thing you need to do: **fill in `AUDIT.tunnel_targets` correctly**, so omni can tell whether a host is alive.

---

## Chapter 8 · Choosing a Multi-Machine Topology

| Option | Approach | Pros | Cons |
|---|---|---|---|
| **A. Centralized** (recommended) | Install omni on one always-on machine, mount the other machines' directories over SMB/NFS and index them | One index queries everything; centralized config; fast queries | The index host must stay on; mounts depend on the network |
| **B. Each on its own** | Install omni on each machine and index locally | No network dependency; fully independent | You have to query 3 times; data isn't aggregated (omni doesn't support cross-DB merging) |
| **C. On the NAS** | Install omni on a NAS that can run Linux (Docker works too) | A NAS is naturally always-on and closest to the data | Requires NAS support; initial setup is fiddly |

Choose A when: you have a structure of "one main machine + a few occasionally-powered work machines."
Choose C when: the NAS is powerful enough and you want fully unattended operation.

> omni does **not** support merging several machines' indexes into one DB. If you really must aggregate (e.g. option B),
> you can only build each index separately and handle it yourself at the application layer (for example, stitching together the results of `find --json`).

---

## Chapter 9 · Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `omni mounts` shows not mounted, but `ls` sees the directory | **The empty-directory trap**: a failed mount leaves an empty directory | Re-run `mount_smbfs`; check the password/network; confirm with `mount \| grep mnt` |
| `mount_smbfs: URL parsing failed` | The share name contains Chinese/spaces and isn't URL-encoded | Encode it with `%XX` (see 3.2) |
| SMB auth fails though the password is right | A Windows empty-password account is barred from network logon / wrong account name | Create an account with a password; diagnose with `smbutil view //user@host` |
| The index hangs and Ctrl-C does nothing | A mount point is a **dead mount** whose peer is powered off | This is kernel behavior; omni already works around it with a child process + timeout; unmount the dead mount first: `diskutil unmount force ~/mnt/xxx` |
| `omni doctor` reports FTS5 unavailable | Python's SQLite was built without FTS5 | Switch to the official Python 3.12+ (see 1.2) |
| Chinese search finds nothing | That file wasn't **content-indexed** (`content=False`, or not in `CONTENT_EXT`) | After changing the config, run `omni index --content <label>` |
| The DB is too big (>4 GB) | The content-indexing scope is configured too broadly | Narrow `CONTENT_EXT`, lower `CONTENT_KEEP_CHARS`, then `omni db rebuild --yes` |
| A big directory never finishes scanning, keeps aborting | Exceeds the per-point scan limit | Set `max_scan_s` for that mount point (e.g. `2400`) |
| Scheduled tasks don't run | macOS TCC or a path issue | Don't put configs/scripts in `~/Desktop`; run it manually once to confirm it works |
| Paths in `omni find` results won't open | The mount point isn't mounted | Check with `omni mounts` first |

---

## Chapter 10 · Upgrading and Uninstalling

### Upgrading

```sh
cd ~/wg
git pull            # or unpack the new version over it (keeping your own omnirc.py)
omni selftest       # confirm the new version is healthy
omni index          # adapts automatically if the DB schema changed
```

> `omnirc.py` is your config and **won't be overwritten by git** (it's in `.gitignore`), so `git pull` without worry.

### Uninstalling

omni installs no system services and writes no system directories; uninstalling is just deleting the directory:

```sh
# 1) stop the scheduled tasks (if configured)
launchctl unload ~/Library/LaunchAgents/local.omni.daily.plist   # macOS
# delete the corresponding lines in crontab -e                     # Linux

# 2) unmount the mount points
diskutil unmount ~/mnt/laptop-c      # macOS
sudo umount ~/mnt/laptop-c           # Linux

# 3) delete the directory
rm -rf ~/wg            # ★ note: this deletes your config and omni.db along with it
rm -f /usr/local/bin/omni
```

> ⚠️ Besides the program, `~/wg` holds your config, index, and reports. Think twice before deleting.
> Your **original files** were never touched by omni from start to finish, so deleting omni won't affect any data.

---

## Appendix A · Config Field Quick Reference

```python
MNT = "~/mnt"                 # mount-point root; actual path = MNT + "/" + label

MOUNTS = [
  dict(
    label="laptop-d",         # required; must match the ~/mnt/<label> directory name
    host="laptop",            # required; logical hostname
    ip="192.168.1.20",        # required; used for audit liveness probes
    share="D$",               # required; share name (for bookkeeping)
    content=True,             # whether to extract body text
    max_scan_s=2400,          # optional; per-point scan limit (seconds)
    max_depth=10,             # optional; content-index depth limit, default 12
  ),
]

DB_PATH       = "~/wg/omni.db"
INVENTORY_DIR = "~/wg/inventory"
REPORT_DIR    = "~/wg/reports"
LOG_PATH      = "~/wg/.omni/omni.log"
STATE_DIR     = "~/wg/.omni"
WIKI_DIR      = "~/wg/wiki"
WIKI_RAW      = "~/wg/wiki/raw"
AGENTS_PATH   = "~/wg/AGENTS.md"

SCAN_HARD_LIMIT_S   = 600     # hard per-mount scan limit
SCAN_MIN_INTERVAL_H = 6       # minimum interval between auto-scans
SEARCH_LIMIT        = 50      # default number of results from find

CONTENT_EXT        = {...}    # extension whitelist for body extraction
CONTENT_KEEP_CHARS = 20*1024  # characters kept per file
CONTENT_MAX_BYTES  = 2*1024*1024

KEY_DIRS   = [...]            # L1 asset cards
SYNC_RULES = [...]            # L3 sync rules

AUDIT = {
  "tunnel_targets": [("laptop", "192.168.1.20", 445)],
  "disk_warn_pct": 10,
  "stale_backup": [...],
  "junk_paths": [...],
  "index_stale_days": 7,
  "webhook_wecom": "",
}
```

---

## Appendix B · Command Quick Reference

| Command | Purpose |
|---|---|
| `omni doctor` | Self-check: interpreter / SQLite+FTS5 / config / mounts / database |
| `omni mounts [--json]` | List mount points, status, last scan |
| `omni selftest` | Core-logic self-test (11 assertions) |
| `omni index [--full\|--content\|--limit N\|--status\|--bg] [mount points…]` | Build/update the index |
| `omni find <term> [--mount…] [--ext…] [--recent 7d] [--limit N] [--json] [--pack]` | Search |
| `omni recent [7d\|24h]` | Recently changed |
| `omni du [--top N]` | Usage ranking |
| `omni open <index>` | Open one of the find results |
| `omni inv init\|sync\|stats\|list` | Asset cards |
| `omni save <url\|file>` | Save to the inbox (source read-only) |
| `omni git log\|status\|commit` | Knowledge-layer versions |
| `omni sync push\|pull\|status\|mark` | Two-way sync with a knowledge base (e.g. ima) |
| `omni audit [--push]` | Audit (11 checks) |
| `omni report` | Audit report |
| `omni db stats\|vacuum\|rebuild` | Database maintenance (`rebuild` requires `--yes`) |
| `omni ima list\|ls\|pull\|push` | Direct ima official OpenAPI access (optional) |
| `omni --lang en\|zh <subcommand>` | Select the output language (or use `OMNI_LANG`, see 2.7) |

---

## Finally: Remember Three Things

1. **omni never changes your files** — it only writes the index and reports under `$OMNI_HOME`, so leave it running long-term with peace of mind.
2. **Multi-machine config = mount + fill in the label** — mount at `~/mnt/<label>`, then write `<label>` into `MOUNTS`.
3. **Content indexing is a trade-off** — done right it's a superpower; done wrong it's a DB of several GB. Decide by the nature of each directory.

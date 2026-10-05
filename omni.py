#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""omni — 本地索引与编目工具（stdlib only，兼容 Python 3.9+）

用法: omni <command> [options]        omni --help

安全硬约束 S1：omni 绝不删/移/改用户文件，只写 ~/wg/{inventory,reports,wiki,.omni}/ 与 omni.db。
save 收集箱只往 wiki/raw 写副本（网页抓取/文件复制），源文件永远只读。
清理建议只输出命令不代执行；破坏性命令（db rebuild）必须 --yes。
"""
import argparse, hashlib, json, os, re, signal, socket, sqlite3, subprocess, sys, time
import zipfile
from fnmatch import fnmatch

VERSION = "1.1.0"
WG = os.path.expanduser(os.environ.get("OMNI_HOME", "~/wg"))

# ============================================================ 基础设施
def load_cfg():
    import importlib.util
    p = os.path.join(WG, "omnirc.py")
    spec = importlib.util.spec_from_file_location("omnirc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def log(msg):
    cfg = load_cfg.__dict__.get("_cfg")
    path = os.path.expanduser(getattr(cfg, "LOG_PATH", "~/wg/.omni/omni.log")) if cfg else \
        os.path.join(WG, ".omni/omni.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))

def state_dir():
    return os.path.expanduser("~/wg/.omni")

def state_file(name):
    return os.path.join(state_dir(), name)

def read_state(name, default=None):
    try:
        with open(state_file(name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_state(name, obj):
    os.makedirs(state_dir(), exist_ok=True)
    tmp = state_file(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, state_file(name))

def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%.0f %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0

def age_h(ts):
    if not ts:
        return "从未"
    d = time.time() - ts
    if d < 3600:   return "%d 分钟前" % (d / 60)
    if d < 86400:  return "%.1f 小时前" % (d / 3600)
    return "%.1f 天前" % (d / 86400)

def is_mounted(path):
    out = subprocess.run(["mount"], capture_output=True, text=True).stdout
    return (" on %s " % os.path.realpath(path)) in out or (" on %s " % path) in out

def resolve_mounts(cfg, labels=None):
    """返回挂载点字典列表（含 mountpoint 绝对路径）。"""
    out = []
    for i, m in enumerate(cfg.MOUNTS):
        if labels and m["label"] not in labels:
            continue
        d = dict(m)
        d["mountpoint"] = os.path.join(os.path.expanduser(cfg.MNT), m["label"])
        d["content_idx"] = 1 if m.get("content") else 0
        d["max_depth"] = m.get("max_depth", 12)
        d["enabled"] = 1
        out.append(d)
    return out

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mounts (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE, host TEXT NOT NULL, ip TEXT, kind TEXT NOT NULL,
    mountpoint TEXT NOT NULL, share TEXT,
    content_idx INTEGER DEFAULT 0, max_depth INTEGER DEFAULT 12,
    enabled INTEGER DEFAULT 1, last_scan INTEGER DEFAULT 0,
    last_count INTEGER DEFAULT 0, last_error TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    mount_id INTEGER NOT NULL REFERENCES mounts(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL, name TEXT NOT NULL, ext TEXT,
    size INTEGER DEFAULT 0, mtime INTEGER DEFAULT 0, is_dir INTEGER DEFAULT 0,
    depth INTEGER DEFAULT 0, content_len INTEGER DEFAULT 0, content_sha TEXT,
    content TEXT DEFAULT '', scanned_at INTEGER NOT NULL,
    UNIQUE(mount_id, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime DESC);
CREATE INDEX IF NOT EXISTS idx_files_ext   ON files(ext);
CREATE INDEX IF NOT EXISTS idx_files_dir   ON files(mount_id, is_dir);
CREATE INDEX IF NOT EXISTS idx_files_name  ON files(name);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    name, rel_path, content,
    tokenize = 'trigram',
    content  = 'files',
    content_rowid = 'id'
);
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name, rel_path, content)
    VALUES (new.id, new.name, new.rel_path, new.content);
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, rel_path, content)
    VALUES ('delete', old.id, old.name, old.rel_path, old.content);
END;
CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE OF name, rel_path, content ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, rel_path, content)
    VALUES ('delete', old.id, old.name, old.rel_path, old.content);
    INSERT INTO files_fts(rowid, name, rel_path, content)
    VALUES (new.id, new.name, new.rel_path, new.content);
END;
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, card_path TEXT NOT NULL, kind TEXT, host TEXT,
    mount_label TEXT, rel_path TEXT, purpose TEXT, owner TEXT, tags TEXT,
    health TEXT, review_by TEXT, related TEXT,
    file_count INTEGER DEFAULT 0, total_size INTEGER DEFAULT 0,
    newest_mtime INTEGER DEFAULT 0, synced_at INTEGER
);
CREATE TABLE IF NOT EXISTS kb_items (
    id INTEGER PRIMARY KEY, kb_id TEXT NOT NULL, kb_name TEXT, title TEXT NOT NULL,
    media_type TEXT, size INTEGER DEFAULT 0, fetched_at INTEGER NOT NULL,
    UNIQUE(kb_id, title)
);
CREATE TABLE IF NOT EXISTS push_log (
    id INTEGER PRIMARY KEY, local_path TEXT NOT NULL, content_sha TEXT,
    kb_id TEXT, kb_name TEXT, pushed_at INTEGER NOT NULL, status TEXT, detail TEXT,
    UNIQUE(local_path, content_sha, kb_id)
);
CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, ts INTEGER NOT NULL,
    check_name TEXT NOT NULL, severity TEXT NOT NULL, summary TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_run ON audits(run_id);
"""

def connect(cfg, create=True):
    path = os.path.expanduser(cfg.DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if create:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    return conn

def sync_mounts_table(conn, mounts):
    for m in mounts:
        conn.execute("""INSERT INTO mounts(label,host,ip,kind,mountpoint,share,content_idx,max_depth,enabled)
                        VALUES(?,?,?,?,?,?,?,?,1)
                        ON CONFLICT(label) DO UPDATE SET
                          host=excluded.host, ip=excluded.ip, mountpoint=excluded.mountpoint,
                          share=excluded.share, content_idx=excluded.content_idx,
                          max_depth=excluded.max_depth""",
                     (m["label"], m["host"], m.get("ip"), "smb", m["mountpoint"],
                      m.get("share"), m.get("content_idx", 0), m.get("max_depth", 12)))
    conn.commit()

def get_mount_row(conn, label):
    return conn.execute("SELECT * FROM mounts WHERE label=?", (label,)).fetchone()

# ============================================================ P0 doctor / mounts / selftest
def cmd_doctor(cfg, args):
    ok = warn = fail = 0
    print("omni %s · 自检" % VERSION)
    print("-" * 62)
    # 解释器 / SQLite
    sv = "%d.%d.%d" % sys.version_info[:3]
    print("解释器      %s  (%s)" % (sv, sys.executable))
    sq = sqlite3.sqlite_version
    tri = False
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        c.execute("INSERT INTO t VALUES('hello 全文检索')")
        tri = c.execute("SELECT count(*) FROM t WHERE t MATCH '\"全文检索\"'").fetchone()[0] == 1
    except Exception:
        pass
    print("SQLite      %s   FTS5+trigram: %s" % (sq, "可用" if tri else "不可用"))
    ok += 1
    # 配置 / 挂载
    mounts = resolve_mounts(cfg)
    print("配置        %d 个挂载点（内容索引 %d 个）" %
          (len(mounts), sum(1 for m in mounts if m["content_idx"])))
    mounted = [m for m in mounts if is_mounted(m["mountpoint"])]
    print("挂载        %d / %d 已挂载" % (len(mounted), len(mounts)))
    if len(mounted) < len(mounts):
        warn += 1
        for m in mounts:
            if not is_mounted(m["mountpoint"]):
                print("  ⚠ 未挂载: %s (%s)" % (m["label"], m["mountpoint"]))
        print("  修复: sh ~/wg/smb.sh mount all")
    else:
        ok += 1
    # DB
    dbp = os.path.expanduser(cfg.DB_PATH)
    if os.path.exists(dbp):
        size = os.path.getsize(dbp)
        conn = connect(cfg, create=False)
        try:
            n = conn.execute("SELECT count(*) FROM files").fetchone()[0]
            print("数据库      %s · %s · %s 条文件" % (dbp, human(size), n))
        except Exception:
            print("数据库      %s · %s · (需重建)" % (dbp, human(size)))
            warn += 1
        conn.close()
        if size > 4 << 30:
            print("  ❗ DB 超过 4 GB，内容索引范围可能配错（omni db rebuild 后调小）")
            fail += 1
        elif size > 2 << 30:
            print("  ⚠ DB 较大（%s）。83.6 万条目 + 全文属正常；如需瘦身：omnirc 收紧 CONTENT_EXT" % human(size))
            warn += 1
    else:
        print("数据库      尚未建立（首次运行 omni index 自动创建）")
    # 网络可达
    for m in mounts:
        ip = m.get("ip")
        if not ip or (m["label"], ip) in getattr(cfg, "_pinged", []):
            continue
    hosts = sorted({m.get("ip") for m in mounts if m.get("ip")})
    for ip in hosts:
        s = socket.socket(); s.settimeout(2.0)
        try:
            s.connect((ip, 445)); st = "SMB 可达"
            ok += 1
        except Exception:
            st = "不可达"; warn += 1
        finally:
            s.close()
        print("网络        %-14s %s" % (ip, st))
    print("-" * 62)
    print("结论: ok=%d warn=%d fail=%d" % (ok, warn, fail))
    return 0 if (warn == 0 and fail == 0) else (3 if fail else 1)

def cmd_mounts(cfg, args):
    conn = connect(cfg)
    sync_mounts_table(conn, resolve_mounts(cfg))
    rows = conn.execute("SELECT * FROM mounts ORDER BY id").fetchall()
    print("%-16s %-9s %-14s %-8s %s" % ("label", "host", "mountpoint", "状态", "上次扫描"))
    print("-" * 86)
    for r in rows:
        mp = is_mounted(r["mountpoint"])
        st = "已挂载" if mp else "未挂载"
        if mp and r["last_error"]:
            st += "(%s)" % r["last_error"]
        print("%-16s %-9s %-14s %-8s %s · %s 条" %
              (r["label"], r["host"], r["mountpoint"].replace(os.path.expanduser("~"), "~"),
               st, age_h(r["last_scan"]), r["last_count"] or 0))
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
    return 0

def cmd_selftest(cfg, args):
    """轻量自测：不依赖挂载点，验证核心逻辑。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO mounts(id,label,host,kind,mountpoint) VALUES(1,'t','h','smb','/tmp')")
    conn.execute("""INSERT INTO files(mount_id,rel_path,name,ext,size,mtime,is_dir,depth,
                    content,content_len,scanned_at)
                    VALUES(1,'示例/脚本.py','脚本.py','.py',10,1,0,1,
                    '这是一份示例文档，用于验证全文检索与摘要功能。',20,1)""")
    # 双路检索
    r1 = [dict(x) for x in search(conn, "示例")]
    assert r1 and r1[0]["hit"] == "like", "2 字词必须走 LIKE"
    r2 = [dict(x) for x in search(conn, "示例文档")]
    assert r2 and r2[0]["hit"] == "match", "MATCH 检索失败"
    assert r2[0]["snip"], "snippet 应能取到内容"
    r3 = [dict(x) for x in search(conn, "C++")]     # 特殊字符不崩
    assert isinstance(r3, list)
    # 内容解析
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.docx")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml",
                   "<w:body><w:p><w:r><w:t>示例文档报告</w:t></w:r></w:p></w:body>")
    assert "示例文档报告" in docx_text(p), "docx 解析失败"
    assert fts_escape('a"b(c)') == '"a b c"', "fts_escape 失败"
    # 知识层 P2/P3
    h = html_to_md("<h1>标题</h1><p>段落 <b>加粗</b></p><li>项一</li><script>x()</script>")
    assert "# 标题" in h and "段落 加粗" in h and "项一" in h and "x()" not in h, "html_to_md 失败"
    card2 = os.path.join(d, "old.md")
    with open(card2, "w", encoding="utf-8") as f:
        f.write("---\nid: t\n---\n\n# T\n<!-- OMNI:AUTO:BEGIN -->\na\n<!-- OMNI:AUTO:END -->\n")
    ensure_card_meta(card2)
    t2 = open(card2, encoding="utf-8").read()
    assert "status: 待人工确认" in t2 and "sources: []" in t2 and MANUAL_SECTION_TITLE in t2
    ensure_card_meta(card2)
    assert t2 == open(card2, encoding="utf-8").read(), "ensure_card_meta 必须幂等"
    # AUTO 段
    card = os.path.join(d, "c.md")
    with open(card, "w", encoding="utf-8") as f:
        f.write("# T\n<!-- OMNI:AUTO:BEGIN -->\nold\n<!-- OMNI:AUTO:END -->\n\n人工内容\n")
    write_auto_segment(card, "new-auto")
    txt = open(card, encoding="utf-8").read()
    assert "new-auto" in txt and "old" not in txt and "人工内容" in txt
    print("selftest: 11 项断言全部通过")
    return 0

# ============================================================ 扫描器
def should_exclude(e, cfg):
    n = e.name
    if n in cfg.EXCLUDE_NAME_EXACT:
        return True
    for p in cfg.EXCLUDE_NAME_PREFIX:
        if n.startswith(p):
            return True
    try:
        if e.is_dir(follow_symlinks=False):
            return n in cfg.EXCLUDE_DIRS
    except OSError:
        return True
    return os.path.splitext(n)[1].lower() in cfg.EXCLUDE_EXT

def scan_mount(conn, m, cfg, full=False):
    """扫一个挂载点。独立事务 + 独立时间戳 = 可断点续扫。"""
    t0 = time.time()
    if not is_mounted(m["mountpoint"]):
        conn.execute("UPDATE mounts SET last_error='not mounted' WHERE label=?", (m["label"],))
        conn.commit()
        return dict(label=m["label"], error="not mounted", count=0, elapsed=0)
    row = get_mount_row(conn, m["label"])
    since = 0 if full else (row["last_scan"] or 0)
    start_ts = int(t0)
    n, skipped = 0, 0
    stack = [(m["mountpoint"], 0)]
    batch = []
    maxdepth = m.get("max_depth", 12)
    timed_out = False

    def flush(rows):
        if not rows:
            return
        conn.executemany("""INSERT INTO files(mount_id,rel_path,name,ext,size,mtime,is_dir,depth,scanned_at)
                            VALUES(?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(mount_id,rel_path) DO UPDATE SET
                              name=excluded.name, ext=excluded.ext, size=excluded.size,
                              mtime=excluded.mtime, is_dir=excluded.is_dir,
                              depth=excluded.depth, scanned_at=excluded.scanned_at""",
                         rows)
        conn.commit()

    while stack:
        if time.time() - t0 > m.get("max_scan_s", cfg.SCAN_HARD_LIMIT_S):
            timed_out = True
            break
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if should_exclude(e, cfg):
                        skipped += 1
                        continue
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    rel = os.path.relpath(e.path, m["mountpoint"])
                    isdir = e.is_dir(follow_symlinks=False)
                    if not full and not isdir and st.st_mtime <= since:
                        continue
                    batch.append((row["id"], rel, e.name,
                                  os.path.splitext(e.name)[1].lower() if not isdir else None,
                                  st.st_size if not isdir else 0,
                                  int(st.st_mtime), 1 if isdir else 0, depth, start_ts))
                    n += 1
                    if isdir and depth < maxdepth:
                        stack.append((e.path, depth + 1))
                    if len(batch) >= 2000:
                        flush(batch); batch = []
        except PermissionError:
            skipped += 1
        except OSError as ex:
            log("scandir fail %s: %s" % (d, ex))
    flush(batch)

    elapsed = time.time() - t0
    if not timed_out and full:
        conn.execute("DELETE FROM files WHERE mount_id=? AND scanned_at<?", (row["id"], start_ts))
    conn.execute("""UPDATE mounts SET last_scan=?, last_count=?, last_error=?
                    WHERE label=?""",
                 (int(t0) if not timed_out else row["last_scan"],
                  (row["last_count"] or 0) + n if timed_out else n,
                  "timeout(断点续扫)" if timed_out else None, m["label"]))
    conn.commit()
    res = dict(label=m["label"], count=n, skipped=skipped, elapsed=round(elapsed, 1),
               timeout=timed_out)
    st = read_state("scan-state.json", {})
    st[m["label"]] = dict(done=not timed_out, count=n, elapsed=elapsed,
                          at=int(t0), error=None if not timed_out else "timeout")
    write_state("scan-state.json", st)
    return res

def cmd_index(cfg, args):
    conn = connect(cfg)
    labels = set(args.mounts or [])
    mounts = resolve_mounts(cfg, labels or None)
    sync_mounts_table(conn, resolve_mounts(cfg))
    lock = state_file("index.lock")
    if os.path.exists(lock):
        stale = True
        try:
            pid = int(open(lock).read().strip() or "0")
            if pid:
                os.kill(pid, 0)          # 还活着 → 真的在跑
                stale = False
        except (ValueError, ProcessLookupError, PermissionError):
            stale = True                 # pid 不存在 = 残留死锁
        except OSError:
            stale = False
        if not stale:
            print("已有索引进程在跑（pid %s）。查看进度: omni index --status" % pid)
            return 1
        try:
            os.remove(lock)
            log("清除残留索引锁: %s" % lock)
        except OSError:
            pass
    if args.bg:
        env = dict(os.environ)
        os.makedirs(state_dir(), exist_ok=True)
        child = [sys.executable, os.path.abspath(__file__), "index"] + \
                (["--full"] if args.full else []) + \
                (["--content"] if args.content else []) + \
                (["--limit", str(args.limit)] if getattr(args, "limit", None) else []) + \
                (list(labels) if labels else []) + ["--foreground"]
        with open(state_file("index.out"), "ab") as out:
            subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT,
                             start_new_session=True, env=env)
        print("已在后台启动索引。进度: omni index --status   日志: ~/wg/.omni/index.out")
        return 0
    open(lock, "w").write(str(os.getpid()))
    try:
        if args.content:
            return _index_content(cfg, conn, labels, args.limit)
        results = []
        dhosts = dead_hosts(cfg)
        for m in mounts:
            mp = m["mountpoint"]
            if not is_mounted(mp):
                print("跳过 %s（未挂载）" % m["label"]); continue
            if mount_is_dead(m, dhosts):
                print("跳过 %s（主机 %s 不可达——死挂载点扫描会卡死）" % (m["label"], m.get("host")))
                continue
            if not probe_dir_readable(mp):
                print("跳过 %s（挂载点无响应）" % m["label"]); continue
            t0 = time.time()
            r = scan_mount(conn, m, cfg, full=args.full)
            flag = " ⚠超时断点" if r.get("timeout") else ""
            print("%-16s %6d 条 (%.1fs)%s" % (r["label"], r["count"], r["elapsed"], flag))
            results.append(r)
        done = sum(1 for r in results if not r.get("timeout"))
        print("完成 %d/%d 个挂载点" % (done, len(results)))
        if args.content is False and not args.full:
            pass
        return 0
    finally:
        try: os.remove(lock)
        except OSError: pass

def _index_content(cfg, conn, labels, limit=None):
    exts = tuple(sorted(cfg.CONTENT_EXT))
    mounts = resolve_mounts(cfg, labels or None)
    dhosts = dead_hosts(cfg)
    total = 0
    for m in mounts:
        if not m.get("content_idx"):
            continue
        if not is_mounted(m["mountpoint"]):
            print("跳过 %s（未挂载）" % m["label"]); continue
        if mount_is_dead(m, dhosts):
            print("跳过 %s（主机不可达）" % m["label"]); continue
        row = get_mount_row(conn, m["label"])
        if not row:
            continue
        ph = ",".join("?" * len(exts))
        q = ("SELECT id, rel_path, ext, size FROM files WHERE mount_id=? AND is_dir=0 "
             "AND content_len=0 AND size<=? AND ext IN (%s) ORDER BY mtime DESC" % ph)
        params = [row["id"], cfg.CONTENT_MAX_BYTES] + list(exts)
        if limit:
            q += " LIMIT %d" % limit
        n = 0
        for r in conn.execute(q, params).fetchall():
            fp = os.path.join(m["mountpoint"], r["rel_path"])
            txt = extract_text(fp, r["ext"] or "", cfg)
            if not txt:
                conn.execute("UPDATE files SET content_len=-1 WHERE id=?", (r["id"],))
                continue
            txt = txt[:cfg.CONTENT_KEEP_CHARS]
            sha = hashlib.sha1(txt.encode("utf-8", "ignore")).hexdigest()[:16]
            conn.execute("UPDATE files SET content_len=?, content_sha=?, content=? WHERE id=?",
                         (len(txt), sha, txt, r["id"]))
            n += 1
            if n % 50 == 0:
                conn.commit()
                st = read_state("scan-state.json", {})
                st[m["label"]] = dict(done=False, content=n, at=int(time.time()))
                write_state("scan-state.json", st)
        conn.commit()
        print("%-16s 内容索引 %d 个文件" % (m["label"], n))
        total += n
    print("内容索引完成，共 %d 个文件" % total)
    return 0

# ============================================================ 内容解析
def read_text_guess_encoding(path, max_bytes):
    with open(path, "rb") as f:
        raw = f.read(max_bytes)
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "ignore")

def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
    return re.sub(r"\n{2,}", "\n", re.sub(r"<[^>]+>", "", xml)).strip()

def xlsx_text(path):
    """xlsx：读共享字符串表（ss.xml），零依赖。"""
    out = []
    with zipfile.ZipFile(path) as z:
        try:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
        except KeyError:
            return ""
        for m in re.finditer(r"<t[^>]*>(.*?)</t>", xml, re.S):
            out.append(re.sub(r"<[^>]+>", "", m.group(1)))
    return "\n".join(out)

def extract_text(path, ext, cfg):
    try:
        if ext in cfg.TEXT_PLAIN_EXT:
            return read_text_guess_encoding(path, cfg.CONTENT_MAX_BYTES)
        if ext == ".docx":
            return docx_text(path)
        if ext == ".xlsx":
            return xlsx_text(path)
        if ext in (".doc", ".rtf", ".odt", ".html", ".htm"):
            r = subprocess.run(["textutil", "-convert", "txt", "-stdout", path],
                               capture_output=True, timeout=cfg.CONTENT_TIMEOUT_S)
            return r.stdout.decode("utf-8", "ignore").strip() if r.returncode == 0 else None
    except subprocess.TimeoutExpired:
        log("parse timeout: %s" % path)
        return None
    except Exception as e:
        log("parse fail %s: %s" % (path, e))
        return None
    return None

# ============================================================ 检索
def fts_escape(q):
    for ch in '"*():^-':
        q = q.replace(ch, " ")
    return '"%s"' % q.strip() if q.strip() else '""'

def rank(rows, q, limit):
    ql = q.lower()
    doc_exts = {".md", ".txt", ".doc", ".docx", ".pdf", ".xlsx", ".csv", ".rtf", ".odt"}
    now = time.time()
    for r in rows:
        name = (r["name"] or "").lower()
        s = 0
        if name == ql:                      s += 100
        elif name.startswith(ql):           s += 50
        elif ql in name:                    s += 30
        if r.get("hit") == "match":         s += 15
        if (r["ext"] or "") in doc_exts:    s += 10
        if now - (r["mtime"] or 0) < 30 * 86400: s += 10
        if (r["depth"] or 0) > 6:           s -= 5
        if (r["size"] or 0) > 100 << 20:    s -= 5
        r["score"] = s
    rows.sort(key=lambda x: (-x["score"], -(x["mtime"] or 0)))
    return rows[:limit]

def search(conn, query, mounts=None, exts=None, limit=50, newer_than=0):
    q = query.strip()
    results = {}
    if len(q) >= 3:
        try:
            sql = ("SELECT f.id, f.name, f.rel_path, m.label AS mount, f.ext, f.size, "
                   "f.mtime, f.is_dir, f.depth, f.content_len, "
                   "snippet(files_fts, 2, '[', ']', '…', 12) AS snip "
                   "FROM files_fts JOIN files f ON f.id = files_fts.rowid "
                   "JOIN mounts m ON m.id = f.mount_id WHERE files_fts MATCH ?")
            for r in conn.execute(sql, (fts_escape(q),)):
                results[r["id"]] = dict(r, hit="match")
        except sqlite3.OperationalError:
            pass
    like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
    sql = ("SELECT f.id, f.name, f.rel_path, m.label AS mount, f.ext, f.size, f.mtime, "
           "f.is_dir, f.depth, f.content_len, '' AS snip "
           "FROM files f JOIN mounts m ON m.id=f.mount_id "
           "WHERE (f.name LIKE ? ESCAPE '\\' OR f.rel_path LIKE ? ESCAPE '\\')")
    params = [like, like]
    if exts:
        sql += " AND f.ext IN (%s)" % ",".join("?" * len(exts))
        params += list(exts)
    if newer_than:
        sql += " AND f.mtime >= ?"
        params.append(newer_than)
    for r in conn.execute(sql, params):
        if r["id"] not in results:
            results[r["id"]] = dict(r, hit="like")
    if exts and len(q) >= 3:
        results = {k: v for k, v in results.items() if v.get("ext") in exts}
    if newer_than:
        results = {k: v for k, v in results.items() if (v.get("mtime") or 0) >= newer_than}
    if mounts:
        results = {k: v for k, v in results.items() if v.get("mount") in mounts}
    return rank(list(results.values()), q, limit)

def parse_span(s):
    s = (s or "").strip().lower()
    m = re.match(r"^(\d+)\s*(m|h|d|w|min|minute|hour|day|week)s?$", s)
    if not m:
        raise ValueError("时间跨度写法: 30m / 12h / 7d / 4w")
    n, u = int(m.group(1)), m.group(2)
    mult = dict(min=60, m=60, h=3600, d=86400, w=604800)[u]
    return n * mult

# ============================================================ 知识层（收集箱/打包/卡片升级/版本）
MANUAL_SECTION_TITLE = "## 人工判断（此区人写，omni 永不修改）"
MANUAL_SECTION_BODY = ("\n" + MANUAL_SECTION_TITLE +
                       "\n\n<!-- omni 不写这里。你的判断、纠偏、补充写在此线以下 -->\n\n- \n")

def ensure_card_meta(path):
    """老卡片升级：frontmatter 补 status/sources；正文补「人工判断」区。幂等。"""
    src = open(path, encoding="utf-8").read()
    orig = src
    m = re.match(r"^---\n(.*?)\n---\n", src, re.S)
    if m:
        fm = m.group(1)
        add = []
        if not re.search(r"^status:", fm, re.M):
            add.append("status: 待人工确认")
        if not re.search(r"^sources:", fm, re.M):
            add.append("sources: []")
        if add:
            src = "---\n" + fm + "\n" + "\n".join(add) + "\n---\n" + src[m.end():]
    if MANUAL_SECTION_TITLE not in src:
        src = src.rstrip("\n") + "\n" + MANUAL_SECTION_BODY
    if src != orig:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)

def _strip_tags(s):
    return re.sub(r"(?s)<[^>]+>", "", s)

def html_to_md(html):
    """极简 HTML→Markdown（stdlib，够用于文章剪藏）。"""
    s = html
    s = re.sub(r"(?is)<(script|style|noscript|svg|iframe|form)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?s)<!--.*?-->", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    for i in (1, 2, 3, 4, 5, 6):
        s = re.sub(r"(?is)<h%d[^>]*>(.*?)</h%d>" % (i, i),
                   lambda m, i=i: "\n" + "#" * i + " " +
                   _strip_tags(m.group(1)).strip() + "\n", s)
    s = re.sub(r"(?is)<li[^>]*>(.*?)</li>",
               lambda m: "- " + _strip_tags(m.group(1)).strip() + "\n", s)
    s = re.sub(r"(?is)<a[^>]*href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>",
               lambda m: "[%s](%s)" % (_strip_tags(m.group(2)).strip(), m.group(1)), s)
    s = re.sub(r"(?is)<(p|div|section|article|blockquote|tr|table|ul|ol)[^>]*>", "\n", s)
    s = re.sub(r"(?is)</(p|div|section|article|blockquote|tr|table|ul|ol|h\d)>", "\n", s)
    s = _strip_tags(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def fetch_url(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def cmd_save(cfg, args):
    """收集箱：网页/文件存入 ~/wg/wiki/raw/（只写 wiki 目录，源文件只读 —— S1）。"""
    raw_dir = os.path.expanduser(getattr(cfg, "WIKI_RAW", "~/wg/wiki/raw"))
    os.makedirs(raw_dir, exist_ok=True)
    if args.list:
        fs = sorted(os.listdir(raw_dir))
        if not fs:
            print("收集箱为空: %s" % raw_dir)
            return 0
        for fn in fs:
            p = os.path.join(raw_dir, fn)
            st = os.stat(p)
            print("%8s  %s  %s" % (human(st.st_size),
                  time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)), fn))
        print("\n共 %d 件 —— 攒批后可按 ~/wg/AGENTS.md 规则整理成知识页" % len(fs))
        return 0
    src = args.src
    if not src:
        print("用法: omni save <url|文件路径> [--title 标题]   或 omni save --list")
        return 2
    today = time.strftime("%Y%m%d")
    if re.match(r"^https?://", src, re.I):
        text = fetch_url(src).decode("utf-8", "replace")
        title = args.title or ""
        if not title:
            mtitle = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
            if mtitle:
                title = _strip_tags(mtitle.group(1)).strip()
        title = title or src.split("//")[-1].split("/")[0]
        body = html_to_md(text)
        body = body.split("轻点两下取消在看")[0].rstrip()  # 裁掉微信尾部交互噪声
        fn = "%s-%s.md" % (today, slug(title)[:60])
        p = os.path.join(raw_dir, fn)
        fm = ("---\nstatus: raw\ntype: clip\ntitle: %s\nsource: %s\nsaved_at: %s\n---\n"
              % (title, src, time.strftime("%Y-%m-%d %H:%M")))
        with open(p, "w", encoding="utf-8") as f:
            f.write(fm + "\n# " + title + "\n\n" + body + "\n")
        print("已收藏: %s（%s）" % (p, human(len(body.encode("utf-8")))))
        return 0
    fp = os.path.expanduser(src)
    if not os.path.isfile(fp):
        print("文件不存在: %s" % fp)
        return 2
    p = os.path.join(raw_dir, "%s-%s" % (today, os.path.basename(fp)))
    with open(fp, "rb") as a, open(p, "wb") as b:
        b.write(a.read())
    print("已收藏副本: %s（源文件未动）" % p)
    return 0

def git_auto_commit(cfg, msg):
    """只提交知识层路径（inventory/reports/wiki/AGENTS.md），不碰 ~/wg 其他内容。"""
    paths = ["inventory", "reports", "wiki", "AGENTS.md"]
    try:
        r = subprocess.run(["git", "-C", WG, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, timeout=10)
        if r.returncode != 0:
            return
        subprocess.run(["git", "-C", WG, "add", "--"] + paths,
                       capture_output=True, timeout=30)
        r2 = subprocess.run(["git", "-C", WG, "diff", "--cached", "--quiet"],
                            capture_output=True, timeout=30)
        if r2.returncode == 0:
            return  # 无变更
        subprocess.run(["git", "-C", WG, "-c", "user.name=omni", "-c", "user.email=omni@local",
                        "commit", "-m", msg, "--"] + paths,
                       capture_output=True, timeout=30)
        log("git commit: %s" % msg)
    except Exception as e:
        log("git auto commit 失败: %s" % e)

def cmd_git(cfg, args):
    if args.action == "commit":
        git_auto_commit(cfg, "omni git commit %s" % time.strftime("%Y-%m-%d %H:%M"))
        print("已提交（无变更则跳过）")
        return 0
    if args.action == "log":
        subprocess.run(["git", "-C", WG, "log", "--oneline", "-15", "--",
                        "inventory", "reports", "wiki", "AGENTS.md"])
        return 0
    subprocess.run(["git", "-C", WG, "status", "--short", "--",
                    "inventory", "reports", "wiki", "AGENTS.md"])
    return 0

def find_pack(cfg, conn, query, rows):
    """检索结果打包成 md —— 补「用得上」环：直接喂给 AI 会话当上下文。"""
    d = os.path.join(state_dir(), "packs")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "pack-%s.md" % time.strftime("%Y%m%d-%H%M%S"))
    lines = ["# 检索打包 · %s" % time.strftime("%Y-%m-%d %H:%M"), "",
             "- 查询词: `%s`" % query,
             "- 命中: %d 条" % len(rows),
             "- 用途: AI 会话上下文（只读引用，来源可回溯）", ""]
    for i, r in enumerate(rows, 1):
        p = os.path.join(os.path.expanduser(cfg.MNT), r["mount"], r["rel_path"])
        lines += ["## %d. %s" % (i, r["name"]),
                  "- 路径: `%s`" % p,
                  "- 体积: %s · 修改: %s" % (human(r["size"]),
                      time.strftime("%Y-%m-%d", time.localtime(r["mtime"])) if r["mtime"] else "—")]
        if r.get("snip"):
            lines.append("- 摘要: %s" % r["snip"].replace("\n", " ")[:200])
        if (r.get("content_len") or 0) > 0:
            row = conn.execute("SELECT content FROM files WHERE id=?", (r["id"],)).fetchone()
            if row and row["content"]:
                lines += ["", "```", row["content"][:1500], "```"]
        lines.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out

# ============================================================ ima OpenAPI 集成（官方 https://ima.qq.com/agent-interface）
IMA_BASE = "https://ima.qq.com"
IMA_MEDIA_TYPE = {1: "PDF", 2: "网页", 3: "Word", 4: "PPT", 5: "Excel",
                  6: "公众号", 7: "Markdown", 9: "图片", 11: "笔记", 99: "文件夹"}
_ima_last_call = [0.0]

def ima_creds():
    """凭证优先级：环境变量 → ~/.config/ima/{client_id,api_key}（官方技能包约定路径）。"""
    cid = os.environ.get("IMA_OPENAPI_CLIENTID", "")
    key = os.environ.get("IMA_OPENAPI_APIKEY", "")
    if not cid:
        p = os.path.expanduser("~/.config/ima/client_id")
        if os.path.exists(p):
            cid = open(p).read().strip()
    if not key:
        p = os.path.expanduser("~/.config/ima/api_key")
        if os.path.exists(p):
            key = open(p).read().strip()
    return cid, key

def ima_post(endpoint, body, timeout=30):
    """ima OpenAPI 统一 POST。三 header 认证 + 2 QPS 限速（官方建议）。"""
    import urllib.request
    cid, key = ima_creds()
    if not cid or not key:
        raise RuntimeError("缺少 ima 凭证：访问 https://ima.qq.com/agent-interface 获取 "
                           "Client ID + API Key，写入 ~/.config/ima/client_id 和 api_key "
                           "（各一行），或 export IMA_OPENAPI_CLIENTID / IMA_OPENAPI_APIKEY")
    gap = 0.5 - (time.time() - _ima_last_call[0])
    if gap > 0:
        time.sleep(gap)
    _ima_last_call[0] = time.time()
    req = urllib.request.Request(
        IMA_BASE + "/" + endpoint, data=json.dumps(body).encode("utf-8"),
        headers={"ima-openapi-clientid": cid, "ima-openapi-apikey": key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    code = data.get("code", data.get("retcode"))
    if code != 0:
        raise RuntimeError("ima API code=%s msg=%s" % (code, data.get("msg", data.get("errmsg"))))
    return data.get("data", {})

def ima_resolve_kb(ref):
    """按名称或 ID 解析知识库 → (kb_id, kb_name)。"""
    r = ima_post("openapi/wiki/v1/search_knowledge_base", {"query": "", "cursor": "", "limit": 50})
    for kb in r.get("info_list", []):
        if kb.get("kb_id") == ref or kb.get("kb_name") == ref:
            return kb["kb_id"], kb.get("kb_name", ref)
    r2 = ima_post("openapi/wiki/v1/search_knowledge_base", {"query": ref, "cursor": "", "limit": 20})
    for kb in r2.get("info_list", []):
        if kb.get("kb_name") == ref:
            return kb["kb_id"], kb["kb_name"]
    raise RuntimeError("找不到知识库 %r —— omni ima list 看全部" % ref)

def cmd_ima(cfg, args):
    try:
        return _ima_main(cfg, args)
    except RuntimeError as e:
        print("失败: %s" % e)
        if "200002" in str(e):
            print("→ API Key 已过期，去 https://ima.qq.com/agent-interface 删除后重新获取")
        return 2

def _ima_main(cfg, args):
    conn = connect(cfg)
    if args.action == "setup":
        cid, key = ima_creds()
        if not cid or not key:
            print("未配置凭证。步骤：")
            print("  1. 电脑版 ima 登录后访问 https://ima.qq.com/agent-interface")
            print("  2. 点「获取 API Key」（只显示一次，立即保存）")
            print("  3. mkdir -p ~/.config/ima && echo <ClientID> > ~/.config/ima/client_id")
            print("     && echo <APIKey> > ~/.config/ima/api_key && chmod 700 ~/.config/ima")
            return 1
        try:
            r = ima_post("openapi/wiki/v1/search_knowledge_base", {"query": "", "cursor": "", "limit": 1})
            print("凭证有效，连接正常（返回 %d 个示例知识库）" % len(r.get("info_list", [])))
            return 0
        except RuntimeError as e:
            print("连接失败: %s" % e)
            if "200002" in str(e):
                print("→ API Key 已过期，去 agent-interface 删除后重新获取")
            return 2
    if args.action == "list":
        r = ima_post("openapi/wiki/v1/search_knowledge_base", {"query": "", "cursor": "", "limit": 50})
        for kb in r.get("info_list", []):
            print("%-28s %s · %s 条" % (kb.get("kb_name", "?"), kb.get("kb_id"),
                                        kb.get("content_count", "?")))
        return 0
    if args.action == "ls":
        kb_id, kb_name = ima_resolve_kb(args.kb)
        items, cursor = [], ""
        for _ in range(20):  # 最多 1000 条
            r = ima_post("openapi/wiki/v1/get_knowledge_list",
                         {"cursor": cursor, "limit": 50, "knowledge_base_id": kb_id})
            items += r.get("knowledge_list", [])
            if r.get("is_end", True) or not r.get("next_cursor"):
                break
            cursor = r["next_cursor"]
        for it in items:
            print("%-8s %s" % (IMA_MEDIA_TYPE.get(it.get("media_type"), "?"),
                               it.get("title", "?")))
        now = int(time.time())
        for it in items:
            conn.execute("""INSERT INTO kb_items(kb_id,kb_name,title,media_type,size,fetched_at)
                            VALUES(?,?,?,?,?,?)
                            ON CONFLICT(kb_id,title) DO UPDATE SET
                              media_type=excluded.media_type, size=excluded.size,
                              fetched_at=excluded.fetched_at""",
                         (kb_id, kb_name, it.get("title", ""), it.get("media_type"),
                          it.get("file_size") or 0, now))
        conn.commit()
        print("\n%s: %d 条（已入 kb_items 表，供 find/审计用）" % (kb_name, len(items)))
        return 0
    if args.action == "pull":
        kb_id, kb_name = ima_resolve_kb(args.kb)
        items, cursor = [], ""
        for _ in range(40):
            r = ima_post("openapi/wiki/v1/get_knowledge_list",
                         {"cursor": cursor, "limit": 50, "knowledge_base_id": kb_id})
            items += r.get("knowledge_list", [])
            if r.get("is_end", True) or not r.get("next_cursor"):
                break
            cursor = r["next_cursor"]
        now = int(time.time())
        for it in items:
            conn.execute("""INSERT INTO kb_items(kb_id,kb_name,title,media_type,size,fetched_at)
                            VALUES(?,?,?,?,?,?)
                            ON CONFLICT(kb_id,title) DO UPDATE SET
                              media_type=excluded.media_type, size=excluded.size,
                              fetched_at=excluded.fetched_at""",
                         (kb_id, kb_name, it.get("title", ""), it.get("media_type"),
                          it.get("file_size") or 0, now))
        conn.commit()
        # 生成 ima 卡片（与 sync pull 同款）
        inv_dir = os.path.expanduser(cfg.INVENTORY_DIR)
        os.makedirs(inv_dir, exist_ok=True)
        auto = "条目数 %d\n\n最近条目：\n%s" % (len(items),
            "\n".join("- [%s] %s" % (IMA_MEDIA_TYPE.get(i.get("media_type"), "?"),
                      i.get("title", "")) for i in items[:30]))
        fm = dict(id="ima-" + slug(kb_name), kind="dataset", host="ima", owner="",
                  purpose="ima 知识库回流（OpenAPI）", tags=["ima", kb_name], health="ok",
                  review_by="", status="待人工确认", sources=[], related=[])
        make_card(os.path.join(inv_dir, "ima-%s.md" % slug(kb_name)),
                  fm, "ima · %s" % kb_name, auto)
        print("pull: %s 共 %d 条入 kb_items + 卡片已更新" % (kb_name, len(items)))
        return 0
    if args.action == "push":
        fp = os.path.expanduser(args.file)
        if not os.path.isfile(fp):
            print("文件不存在: %s" % fp)
            return 2
        if not args.kb:
            print("用法: omni ima push <文件> --kb <知识库名或ID> [--note]")
            return 2
        kb_id, kb_name = ima_resolve_kb(args.kb)
        name = os.path.basename(fp)
        title = os.path.splitext(name)[0]
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        with open(fp, "rb") as f:
            sha = hashlib.sha1(f.read()).hexdigest()[:16]
        if ext in ("md", "markdown") or args.note:
            # 笔记通道：免 COS，纯 stdlib（官方 skill 约定：.md → 笔记；.txt 应走文件通道 media_type=13）
            content = open(fp, encoding="utf-8", errors="replace").read()
            r = ima_post("openapi/note/v1/import_doc",
                         {"content_format": 1, "content": content, "title": title})
            note_id = r.get("note_id", "")
            r2 = ima_post("openapi/wiki/v1/add_knowledge",
                          {"media_type": 11, "note_info": {"content_id": note_id},
                           "title": title, "knowledge_base_id": kb_id})
            media_id = r2.get("media_id", note_id)
            print("已推（笔记通道）: %s → %s\nnote_id=%s media_id=%s" % (fp, kb_name, note_id, media_id))
        else:
            # 文件通道：create_media → COS 直传 → add_knowledge（需 qcloud_cos）
            try:
                from qcloud_cos import CosConfig, CosS3Client
            except ImportError:
                print("文件通道需要腾讯云 COS SDK（md/txt 请走 --note 笔记通道，免依赖）:")
                print("  pip install cos-python-sdk-v5 安装到 omni 的 venv 后重试")
                return 2
            ct_map = {"pdf": ("application/pdf", 1), "doc": ("application/msword", 3),
                      "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", 3),
                      "ppt": ("application/vnd.ms-powerpoint", 4),
                      "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", 4),
                      "xls": ("application/vnd.ms-excel", 5),
                      "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 5),
                      "csv": ("text/csv", 5),
                      "txt": ("text/plain", 13), "xmind": ("application/x-xmind", 14),
                      "mp3": ("audio/mpeg", 15), "m4a": ("audio/x-m4a", 15),
                      "wav": ("audio/wav", 15), "html": ("text/html", 20),
                      "epub": ("application/epub+zip", 21),
                      "png": ("image/png", 9), "jpg": ("image/jpeg", 9), "jpeg": ("image/jpeg", 9),
                      "webp": ("image/webp", 9)}
            content_type, media_type = ct_map.get(ext, ("application/octet-stream", 7))
            size = os.path.getsize(fp)
            ima_post("openapi/wiki/v1/check_repeated_names",
                     {"params": [{"name": name, "media_type": media_type}],
                      "knowledge_base_id": kb_id, "folder_id": ""})
            r = ima_post("openapi/wiki/v1/create_media",
                         {"file_name": name, "file_size": size, "content_type": content_type,
                          "knowledge_base_id": kb_id, "file_ext": ext})
            cos = r.get("cos_credential", {})
            client = CosS3Client(CosConfig(Region=cos["region"], SecretId=cos["secret_id"],
                                           SecretKey=cos["secret_key"], Token=cos.get("token", "")))
            with open(fp, "rb") as f:
                resp = client.put_object(Bucket=cos["bucket_name"], Key=cos["cos_key"],
                                         Body=f, ContentType=content_type)
            if not resp.get("ETag"):
                print("COS 上传失败: %s" % resp)
                return 2
            r2 = ima_post("openapi/wiki/v1/add_knowledge",
                          {"media_type": media_type, "media_id": r["media_id"], "title": title,
                           "knowledge_base_id": kb_id, "folder_id": "",
                           "file_info": {"cos_key": cos["cos_key"], "file_size": size,
                                         "file_name": name}})
            media_id = r2.get("media_id", r["media_id"])
            print("已推（文件通道）: %s → %s\nmedia_id=%s" % (fp, kb_name, media_id))
        conn.execute("INSERT OR IGNORE INTO push_log(local_path,content_sha,kb_id,kb_name,"
                     "pushed_at,status,detail) VALUES(?,?,?,?,?,?,?)",
                     (fp, sha, kb_id, kb_name, int(time.time()), "ok", str(media_id)))
        conn.commit()
        return 0
    return 2

def cmd_find(cfg, args):
    conn = connect(cfg, create=False)
    newer = parse_span(args.recent) if args.recent else 0
    exts = [e if e.startswith(".") else "." + e for e in (args.ext or [])]
    rows = search(conn, args.query, mounts=args.mount, exts=exts or None,
                  limit=args.limit, newer_than=newer)
    if getattr(args, "pack", False):
        out = find_pack(cfg, conn, args.query, rows)
        print("已打包 %d 条 → %s" % (len(rows), out))
        print("把该文件直接发给 AI 会话当上下文即可")
        return 0
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    if not rows:
        print("（无结果）")
        return 0
    for i, r in enumerate(rows, 1):
        p = os.path.join(os.path.expanduser(cfg.MNT), r["mount"], r["rel_path"])
        tag = {"match": "文", "like": "名"}.get(r["hit"], "?")
        extra = " [含内容]" if (r.get("content_len") or 0) > 0 else ""
        print("%3d. [%s] %-14s %s%s" % (i, tag, r["mount"], r["name"], extra))
        print("        %s · %s · %s" % (human(r["size"]), age_h(r["mtime"]), p))
        if r.get("snip"):
            print("        %s" % r["snip"].replace("\n", " ")[:160])
    print("\n共 %d 条 · 打开: omni open <序号>" % len(rows))
    write_state("last-find.json", [dict(r, path=p) for r, p in
                [(r, os.path.join(os.path.expanduser(cfg.MNT), r["mount"], r["rel_path"]))
                 for r in rows]])
    return 0

def cmd_recent(cfg, args):
    conn = connect(cfg, create=False)
    span = parse_span(args.span or "7d")
    since = time.time() - span
    sql = ("SELECT f.name, f.rel_path, m.label AS mount, f.size, f.mtime, f.is_dir "
           "FROM files f JOIN mounts m ON m.id=f.mount_id "
           "WHERE f.mtime>=? AND f.is_dir=0 ORDER BY f.mtime DESC LIMIT ?")
    rows = conn.execute(sql, (since, args.limit)).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
        return 0
    print("最近 %s 变动（%d 条）" % (args.span or "7d", len(rows)))
    for r in rows:
        print("  %s  %-14s %s" % (time.strftime("%m-%d %H:%M", time.localtime(r["mtime"])),
                                  r["mount"], r["rel_path"]))
    return 0

def cmd_du(cfg, args):
    conn = connect(cfg, create=False)
    sql = ("SELECT m.label, substr(f.rel_path,1,instr(f.rel_path||'/','/')-1) AS top, "
           "SUM(f.size) AS sz, COUNT(*) AS n FROM files f JOIN mounts m ON m.id=f.mount_id "
           "WHERE f.is_dir=0 GROUP BY m.label, top ORDER BY sz DESC LIMIT ?")
    print("%-16s %-28s %10s %8s" % ("挂载点", "一级目录", "体积", "文件数"))
    for r in conn.execute(sql, (args.top,)):
        print("%-16s %-28s %10s %8d" % (r["label"], r["top"] or "(根)", human(r["sz"]), r["n"]))
    return 0

def cmd_open(cfg, args):
    st = read_state("last-find.json", [])
    key = args.target
    path = None
    if key.isdigit() and st:
        i = int(key) - 1
        if 0 <= i < len(st):
            path = st[i].get("path")
    if not path:
        conn = connect(cfg, create=False)
        r = conn.execute("SELECT m.mountpoint, f.rel_path FROM files f "
                         "JOIN mounts m ON m.id=f.mount_id WHERE f.id=?", (key,)).fetchone()
        if r:
            path = os.path.join(r["mountpoint"], r["rel_path"])
    if not path or not os.path.exists(path):
        print("找不到: %s" % key)
        return 2
    subprocess.run(["open", "-R", path])
    print("已在 Finder 显示: %s" % path)
    return 0

# ============================================================ P4 资产卡片
AUTO_BEGIN, AUTO_END = "<!-- OMNI:AUTO:BEGIN -->", "<!-- OMNI:AUTO:END -->"

def slug(s):
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s.strip()).strip("-")
    return s.lower() or "x"

def write_auto_segment(path, new_auto):
    src = open(path, encoding="utf-8").read()
    i, j = src.index(AUTO_BEGIN), src.index(AUTO_END)
    out = src[:i] + AUTO_BEGIN + "\n" + new_auto.strip() + "\n" + src[j:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)

def dir_stats(conn, mount_id, rel_prefix=""):
    q = ("SELECT COUNT(*) AS n, SUM(size) AS sz, MAX(mtime) AS newest FROM files "
         "WHERE mount_id=? AND is_dir=0")
    params = [mount_id]
    if rel_prefix:
        q += " AND (rel_path LIKE ? ESCAPE '\\')"
        params.append(rel_prefix.lstrip("/").rstrip("/") + "/%")
    r = conn.execute(q, params).fetchone()
    return r["n"] or 0, r["sz"] or 0, r["newest"] or 0

def card_body_auto(n, sz, newest, extra=""):
    lines = ["| 项 | 值 |", "|---|---|",
             "| 文件数 | %d |" % n,
             "| 总体积 | %s |" % human(sz),
             "| 最新修改 | %s |" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(newest))
                                   if newest else "—")]
    if extra:
        lines.append(extra)
    return "\n".join(lines)

def make_card(path, fm, title, auto_text, manual=""):
    fm_lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            fm_lines.append("%s: [%s]" % (k, ", ".join(v)))
        else:
            fm_lines.append("%s: %s" % (k, v))
    fm_lines.append("generated: %s" % time.strftime("%Y-%m-%dT%H:%M:%S"))
    fm_lines.append("---")
    body = "\n".join(fm_lines) + "\n\n# " + title + "\n\n" + AUTO_BEGIN + "\n" + \
        auto_text.strip() + "\n" + AUTO_END + "\n" + MANUAL_SECTION_BODY
    if manual:
        body += "\n" + manual.strip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)

def cmd_inv(cfg, args):
    conn = connect(cfg)
    inv_dir = os.path.expanduser(cfg.INVENTORY_DIR)
    os.makedirs(inv_dir, exist_ok=True)
    if args.action == "init":
        n = 0
        for m in resolve_mounts(cfg):
            p = os.path.join(inv_dir, "%s.md" % m["label"])
            if os.path.exists(p):
                continue
            fm = dict(id=m["label"], kind="share", host=m["host"], mount=m["label"],
                      owner="", purpose="", tags=[m["host"]], health="ok", review_by="",
                      status="待人工确认", sources=[], related=[])
            title = "%s · %s" % (m["host"], m["label"])
            make_card(p, fm, title, "_（首次 inv sync 后填充统计）_")
            n += 1
        for k in getattr(cfg, "KEY_DIRS", []):
            p = os.path.join(inv_dir, "%s.md" % k["id"])
            if os.path.exists(p):
                continue
            fm = dict(id=k["id"], kind="dir", host="", mount=k["mount"], path=k["rel"],
                      owner=k["owner"], purpose=k["purpose"], tags=k["tags"],
                      health=k["health"], review_by="", status="待人工确认",
                      sources=[], related=[])
            make_card(p, fm, "%s · %s" % (k["mount"], k["rel"].strip("/")),
                      "_（首次 inv sync 后填充统计）_")
            n += 1
        idx = os.path.join(inv_dir, "_index.md")
        if not os.path.exists(idx):
            with open(idx, "w", encoding="utf-8") as f:
                f.write("# omni 资产索引\n\n由 `omni inv init` 生成，`omni inv sync` 更新统计。\n\n"
                        "## 挂载点\n\n| 卡片 | 主机 | 说明 |\n|---|---|---|\n")
            print("init: 新建 %d 张卡片" % n)
        else:
            print("init: 新建 %d 张卡片（已存在的未动）" % n)
        return 0

    if args.action == "sync":
        n = 0
        cards = [f for f in sorted(os.listdir(inv_dir)) if f.endswith(".md") and f != "_index.md"]
        for fn in cards:
            p = os.path.join(inv_dir, fn)
            src = open(p, encoding="utf-8").read()
            if AUTO_BEGIN not in src or AUTO_END not in src:
                continue
            fm = parse_frontmatter(src)
            ensure_card_meta(p)
            mount = fm.get("mount", "")
            rel = fm.get("path", "")
            row = conn.execute("SELECT * FROM mounts WHERE label=?", (mount,)).fetchone()
            if not row:
                continue
            cnt, sz, newest = dir_stats(conn, row["id"], rel if fm.get("kind") == "dir" else "")
            write_auto_segment(p, card_body_auto(cnt, sz, newest))
            conn.execute("""INSERT INTO assets(id,card_path,kind,host,mount_label,rel_path,
                            purpose,owner,tags,health,review_by,related,file_count,total_size,
                            newest_mtime,synced_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(id) DO UPDATE SET
                              file_count=excluded.file_count, total_size=excluded.total_size,
                              newest_mtime=excluded.newest_mtime, synced_at=excluded.synced_at""",
                         (fm.get("id", slug(fn)), p, fm.get("kind"), fm.get("host"),
                          mount, rel, fm.get("purpose"), fm.get("owner"),
                          json.dumps(fm.get("tags", []), ensure_ascii=False),
                          fm.get("health", "ok"), fm.get("review_by"),
                          json.dumps(fm.get("related", [])),
                          cnt, sz, newest, int(time.time())))
            n += 1
        conn.commit()
        # 更新 _index.md 的表格（AUTO 段）
        idx = os.path.join(inv_dir, "_index.md")
        rows = conn.execute("SELECT * FROM assets ORDER BY host, mount_label").fetchall()
        tb = ["<!-- OMNI:AUTO:BEGIN -->", "| 卡片 | 主机 | 挂载点 | 文件数 | 体积 | 最新修改 |",
              "|---|---|---|---|---|---|"]
        for r in rows:
            tb.append("| [%s](%s) | %s | %s | %d | %s | %s |" % (
                r["id"], os.path.basename(r["card_path"]), r["host"] or "", r["mount_label"] or "",
                r["file_count"], human(r["total_size"]),
                time.strftime("%Y-%m-%d", time.localtime(r["newest_mtime"])) if r["newest_mtime"] else "—"))
        tb.append("<!-- OMNI:AUTO:END -->")
        if os.path.exists(idx):
            src = open(idx, encoding="utf-8").read()
            if AUTO_BEGIN in src and AUTO_END in src:
                write_auto_segment(idx, "\n".join(tb[2:-1]))
            else:
                with open(idx, "a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(tb) + "\n")
        print("sync: 更新 %d 张卡片" % n)
        return 0

    if args.action == "stats":
        rows = conn.execute("SELECT * FROM assets ORDER BY total_size DESC").fetchall()
        print("%-24s %-9s %-14s %8s %10s  %s" % ("id", "kind", "mount", "文件数", "体积", "最新修改"))
        for r in rows:
            print("%-24s %-9s %-14s %8d %10s  %s" % (
                r["id"], r["kind"] or "", r["mount_label"] or "", r["file_count"],
                human(r["total_size"]),
                time.strftime("%Y-%m-%d", time.localtime(r["newest_mtime"])) if r["newest_mtime"] else "—"))
        return 0

    if args.action == "list":
        rows = conn.execute("SELECT * FROM assets").fetchall()
        out = []
        for r in rows:
            tags = json.loads(r["tags"] or "[]")
            if args.tag and args.tag not in tags:
                continue
            if args.host and (r["host"] or "") != args.host:
                continue
            if args.health and (r["health"] or "") != args.health:
                continue
            if args.stale:
                span = parse_span(args.stale)
                if r["newest_mtime"] and time.time() - r["newest_mtime"] < span:
                    continue
                if not r["newest_mtime"]:
                    continue
            out.append(r)
        for r in out:
            print("%-24s %-14s %s" % (r["id"], r["mount_label"] or "",
                                      r["card_path"].replace(os.path.expanduser("~"), "~")))
        print("共 %d 张" % len(out))
        return 0
    return 2

def parse_frontmatter(src):
    if not src.startswith("---"):
        return {}
    end = src.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in src[3:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k.strip()] = v
    return fm

# ============================================================ P5 / P6 sync + audit
def iter_rule_files(rule, cfg):
    base = os.path.expanduser(rule["glob"].split("**")[0].rstrip("/"))
    exts = tuple(rule.get("exts", ()))
    if not os.path.isdir(base):
        return
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in cfg.EXCLUDE_DIRS
                   and not d.startswith(".")]
        for fn in files:
            if fn.startswith(("~$", ".~lock.", "._")):
                continue
            if exts and os.path.splitext(fn)[1].lower() not in exts:
                continue
            yield os.path.join(root, fn)

def cmd_sync(cfg, args):
    conn = connect(cfg)
    if args.action == "push":
        if args.top and not args.dry_run:
            print("--top 只配合 --dry-run 使用（先出精选清单，确认后再真推）")
            return 2
        cands = []
        n_total = n_new = n_skip = 0
        for i, rule in enumerate(getattr(cfg, "SYNC_RULES", [])):
            if args.rule is not None and i != args.rule:
                continue
            for fp in iter_rule_files(rule, cfg):
                n_total += 1
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                if st.st_size > rule.get("max_mb", 50) << 20:
                    n_skip += 1
                    continue
                sha = None
                if st.st_size <= 2 << 20:
                    try:
                        with open(fp, "rb") as f:
                            sha = hashlib.sha1(f.read()).hexdigest()[:16]
                    except OSError:
                        continue
                hit = conn.execute("SELECT 1 FROM push_log WHERE local_path=? AND "
                                   "content_sha IS ? AND kb_id=? AND status IN ('ok','queued')",
                                   (fp, sha, rule["kb"])).fetchone()
                if hit:
                    n_skip += 1
                    continue
                if args.dry_run:
                    if args.top:
                        cands.append((rule["kb"], fp, st.st_size, st.st_mtime))
                    else:
                        print("[将推] %-24s %s (%s)" % (rule["kb"], fp, human(st.st_size)))
                else:
                    qdir = os.path.join(state_dir(), "push-queue", slug(rule["kb"]))
                    os.makedirs(qdir, exist_ok=True)
                    dst = os.path.join(qdir, "%s_%s" % (sha or "noid", os.path.basename(fp)))
                    try:
                        with open(fp, "rb") as a, open(dst, "wb") as b:
                            b.write(a.read())
                    except OSError as e:
                        conn.execute("INSERT OR IGNORE INTO push_log(local_path,content_sha,kb_id,"
                                     "kb_name,pushed_at,status,detail) VALUES(?,?,?,?,?,?,?)",
                                     (fp, sha, rule["kb"], rule["kb"], int(time.time()),
                                      "fail", str(e)))
                        continue
                    conn.execute("INSERT OR IGNORE INTO push_log(local_path,content_sha,kb_id,"
                                 "kb_name,pushed_at,status,detail) VALUES(?,?,?,?,?,?,?)",
                                 (fp, sha, rule["kb"], rule["kb"], int(time.time()),
                                  "queued", dst))
                n_new += 1
        conn.commit()
        if args.dry_run and args.top:
            now = time.time()
            key_ids = {k["id"]: 20 for k in getattr(cfg, "KEY_DIRS", [])}
            def _score(c):
                kb, fp, sz, mt = c
                s = max(0.0, 30 - (now - mt) / 86400.0)
                s += max(0.0, 10 - sz / (1 << 20))
                for kid, bonus in key_ids.items():
                    if kid in fp:
                        s += bonus
                return s
            cands.sort(key=_score, reverse=True)
            picked = cands[:args.top]
            print("[精选 TOP %d / 共 %d 候选]（别一次搬完旧料 —— 分批推）" % (len(picked), len(cands)))
            for kb, fp, sz, mt in picked:
                print("[将推] %-24s %s (%s, %s)" % (kb, fp, human(sz), age_h(mt)))
            rp = os.path.expanduser(cfg.REPORT_DIR)
            os.makedirs(rp, exist_ok=True)
            mp = os.path.join(rp, "push-pick-%s.md" % time.strftime("%Y-%m-%d"))
            with open(mp, "w", encoding="utf-8") as f:
                f.write("# ima 推送精选清单 · %s\n\n共 %d 候选，精选 %d。\n"
                        "确认后逐条入队（去掉 --dry-run）或按此优先级分批。\n\n"
                        % (time.strftime("%Y-%m-%d %H:%M"), len(cands), len(picked)))
                f.writelines("- [ ] `%s`（%s, %s → %s）\n" % (fp, human(sz), age_h(mt), kb)
                             for kb, fp, sz, mt in picked)
            print("精选清单: %s" % mp)
            return 0
        verb = "将推" if args.dry_run else "已入队"
        print("push: 扫到 %d 个候选，%s %d 个，跳过(已推/超大) %d 个"
              % (n_total, verb, n_new, n_skip))
        if not args.dry_run and n_new:
            print("队列: ~/wg/.omni/push-queue/ —— 两条消费路径：")
            print("  a) omni ima push <文件> --kb <知识库>   （官方 OpenAPI 直推，推荐）")
            print("  b) ima-file-upload 链路（create_media→COS→add_knowledge），成功后: omni sync mark <file> ok")
        return 0
    if args.action == "mark":
        # omni sync mark <queued_path> ok|fail
        qpath = os.path.expanduser(args.file)
        row = conn.execute("SELECT * FROM push_log WHERE detail=?", (qpath,)).fetchone()
        if not row:
            print("push_log 中无此队列文件"); return 2
        conn.execute("UPDATE push_log SET status=?, pushed_at=? WHERE id=?",
                     (args.status, int(time.time()), row["id"]))
        conn.commit()
        if args.status == "ok":
            try: os.remove(qpath)
            except OSError: pass
        print("已标记 %s" % args.status)
        return 0
    if args.action == "pull":
        dump = state_file("ima-dump.json")
        if not os.path.exists(dump):
            print("没有 %s —— 由 ima 侧导出（knowledge 列表 JSON 数组："
                  "kb_id/kb_name/title/media_type/size），放到该路径后重跑" % dump)
            return 1
        items = json.load(open(dump, encoding="utf-8"))
        now = int(time.time())
        for it in items:
            conn.execute("""INSERT INTO kb_items(kb_id,kb_name,title,media_type,size,fetched_at)
                            VALUES(?,?,?,?,?,?)
                            ON CONFLICT(kb_id,title) DO UPDATE SET
                              media_type=excluded.media_type, size=excluded.size,
                              fetched_at=excluded.fetched_at""",
                         (it.get("kb_id", ""), it.get("kb_name", ""), it.get("title", ""),
                          it.get("media_type"), it.get("size", 0), now))
        conn.commit()
        inv_dir = os.path.expanduser(cfg.INVENTORY_DIR)
        os.makedirs(inv_dir, exist_ok=True)
        kbs = {}
        for it in items:
            kbs.setdefault(it.get("kb_name") or it.get("kb_id") or "kb", []).append(it)
        for kb, lst in kbs.items():
            p = os.path.join(inv_dir, "ima-%s.md" % slug(kb))
            types = {}
            for it in lst:
                types[it.get("media_type") or "?"] = types.get(it.get("media_type") or "?", 0) + 1
            auto = "条目数 %d · 类型: %s\n\n最近条目：\n%s" % (
                len(lst),
                ", ".join("%s×%d" % kv for kv in sorted(types.items(), key=lambda x: -x[1])),
                "\n".join("- %s" % it.get("title", "") for it in lst[:30]))
            fm = dict(id="ima-" + slug(kb), kind="dataset", host="ima", owner="",
                      purpose="ima 知识库回流", tags=["ima", kb], health="ok",
                      review_by="", related=[])
            make_card(p, fm, "ima · %s" % kb, auto)
            print("生成卡片: %s (%d 条)" % (p, len(lst)))
        print("pull: %d 条 ima 条目入库" % len(items))
        return 0
    if args.action == "status":
        nq = conn.execute("SELECT count(*) FROM push_log WHERE status='queued'").fetchone()[0]
        nok = conn.execute("SELECT count(*) FROM push_log WHERE status='ok'").fetchone()[0]
        nfail = conn.execute("SELECT count(*) FROM push_log WHERE status='fail'").fetchone()[0]
        nkb = conn.execute("SELECT count(*) FROM kb_items").fetchone()[0]
        print("push: queued=%d ok=%d fail=%d   ima 条目=%d" % (nq, nok, nfail, nkb))
        return 0
    return 2

def tcp_ok(ip, port, timeout=2.5):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((ip, port)); return True
    except OSError:
        return False
    finally:
        s.close()

def _probe_subprocess(snippet, path, timeout=8):
    """在独立进程里做可能阻塞的文件系统操作。
    ★ 死挂载点（对端休眠/关机）的 I/O 等待是内核不可中断睡眠，信号杀不掉，
    必须在子进程里做，超时后连带进程组一起放弃，避免拖死整个巡视/扫描。"""
    try:
        p = subprocess.Popen([sys.executable, "-c", snippet, path],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = p.communicate(timeout=timeout)
        return out.decode("utf-8", "replace").strip() if p.returncode == 0 else None
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except OSError:
            pass
        try:
            p.communicate(timeout=2)
        except Exception:
            pass
        return None

def guarded_statvfs(path, timeout=6):
    """带超时的 statvfs → (剩余字节, 占用百分比) 或 None（无响应）。"""
    out = _probe_subprocess(
        "import os,sys;fr=os.statvfs(sys.argv[1]);print('%d %d %d' % "
        "(fr.f_bavail, fr.f_frsize, fr.f_blocks))", path, timeout)
    if not out:
        return None
    try:
        a, b, c = [int(x) for x in out.split()]
    except ValueError:
        return None
    return a * b, 100.0 * (1 - a / float(c or 1))

def probe_dir_readable(path, timeout=8):
    """挂载点根目录能否读取 → True / False（超时或无响应）。"""
    out = _probe_subprocess(
        "import os,sys;next(os.scandir(sys.argv[1]),None);print('ok')", path, timeout)
    return out == "ok"

def dead_hosts(cfg):
    """探测 AUDIT.tunnel_targets 里不可达的主机名（小写集合）。"""
    dead = set()
    for host, ip, port in cfg.AUDIT.get("tunnel_targets", []):
        if not tcp_ok(ip, port):
            dead.add((host or "").lower())
    return dead

def mount_is_dead(m, dead):
    return (m.get("host") or "").lower() in dead

def newest_mtime_under(path, hard=600):
    t0, newest = time.time(), 0
    for root, dirs, files in os.walk(path):
        if time.time() - t0 > hard:
            break
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            try:
                m = os.stat(os.path.join(root, fn)).st_mtime
                if m > newest:
                    newest = m
            except OSError:
                pass
    return newest

def cmd_audit(cfg, args):
    conn = connect(cfg)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    results = []          # (check, severity, summary, detail)

    def add(check, sev, summary, detail=""):
        results.append((check, sev, summary, detail))

    A = cfg.AUDIT
    # 1 tunnel
    downs = []
    for host, ip, port in A["tunnel_targets"]:
        if not tcp_ok(ip, port):
            downs.append("%s(%s)" % (host, ip))
    add("tunnel", "fail" if downs else "ok",
        "不可达: %s" % ",".join(downs) if downs else "全部 SMB 目标均可达",
        "探测端口 445，超时 2.5s")
    # 2 mounts
    mounts = resolve_mounts(cfg)
    missing = [m["label"] for m in mounts if not is_mounted(m["mountpoint"])]
    n_ok = len(mounts) - len(missing)
    add("mounts", "fail" if missing else "ok",
        ("%d / %d，缺: %s" % (n_ok, len(mounts), ",".join(missing))) if missing
        else "%d / %d 完整" % (n_ok, len(mounts)),
        "修复: sh ~/wg/smb.sh mount all" if missing else "")
    # 3 disk（★ 主机不可达的挂载点跳过：死挂载点的 statvfs 会不可中断卡死）
    dead = dead_hosts(cfg)
    dead_mps = [os.path.abspath(m["mountpoint"]) for m in mounts if mount_is_dead(m, dead)]
    def under_dead(path):
        ap = os.path.abspath(os.path.expanduser(path))
        return any(ap == d or ap.startswith(d + os.sep) for d in dead_mps)
    disk_rows, disk_skipped = [], []
    for m in mounts:
        if not is_mounted(m["mountpoint"]):
            continue
        if mount_is_dead(m, dead):
            disk_skipped.append(m["label"]); continue
        r = guarded_statvfs(m["mountpoint"])
        if r is None:
            disk_skipped.append("%s(无响应)" % m["label"]); continue
        avail, pct = r
        disk_rows.append((m["label"], pct, avail))
    low = [d for d in disk_rows if d[1] > 100 - A["disk_warn_pct"]]
    add("disk", "warn" if low else "ok",
        ("剩余不足 %d%%: %s" % (A["disk_warn_pct"],
         ", ".join("%s(剩%.0f%%)" % (d[0], 100 - d[1]) for d in low))) if low
        else "全部可测挂载点空间充足",
        "; ".join("%s %.0f%%" % (d[0], d[1]) for d in disk_rows) +
        ("　跳过: %s" % ",".join(disk_skipped) if disk_skipped else ""))
    # 4 stale_backup
    for rule in A.get("stale_backup", []):
        src = os.path.expanduser(rule["src"])
        if under_dead(src):
            add("stale_backup", "info", "%s: 跳过（所属主机不可达）" % rule["label"],
                "死挂载点上遍历会卡住，等主机上线后再查"); continue
        if not os.path.isdir(src):
            add("stale_backup", "info", "%s: 源不存在 %s" % (rule["label"], src)); continue
        smt = newest_mtime_under(src, hard=120)
        dsts = []
        for g in (rule.get("dst_glob") or "").split(","):
            g = os.path.expanduser(g.strip())
            base = os.path.dirname(g) if "*" in g else g
            if os.path.isdir(base):
                dsts.append(base)
        dmt = max([newest_mtime_under(d, hard=120) for d in dsts] or [0]) if dsts else 0
        lag = (smt - dmt) / 86400.0 if (smt and dmt) else None
        if lag is None:
            add("stale_backup", "info", "%s: 无可比对的目标副本" % rule["label"],
                "src 最新 %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(smt)) if smt else "")
        elif lag > rule["lag_days"]:
            add("stale_backup", "warn",
                "%s 备份滞后 %.1f 天" % (rule["label"], lag),
                "源最新 %s；目标最新 %s；建议 rsync 同步" %
                (time.strftime("%Y-%m-%d %H:%M", time.localtime(smt)),
                 time.strftime("%Y-%m-%d %H:%M", time.localtime(dmt))))
        else:
            add("stale_backup", "ok", "%s 备份及时（滞后 %.1f 天）" % (rule["label"], lag))
    # 5 junk
    junk_lines = []
    for jp in A.get("junk_paths", []):
        p = os.path.expanduser(jp)
        if under_dead(p):
            junk_lines.append("%s（跳过：主机不可达）" % jp); continue
        if not os.path.isdir(p):
            continue
        t0, total = time.time(), 0
        for root, dirs, files in os.walk(p):
            if time.time() - t0 > 60:
                break
            for fn in files:
                try: total += os.stat(os.path.join(root, fn)).st_size
                except OSError: pass
        if total:
            junk_lines.append("%s %s" % (jp, human(total)))
    add("junk", "info", "可清理: %s" % " · ".join(junk_lines) if junk_lines else "无垃圾目录",
        "（只提示，不代删 —— S1）")
    # 6 index_freshness
    stale = []
    for r in conn.execute("SELECT label,last_scan,last_count FROM mounts WHERE enabled=1"):
        if not r["last_scan"] or time.time() - r["last_scan"] > A["index_stale_days"] * 86400:
            stale.append(r["label"])
    total_files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    add("index_freshness", "info" if stale else "ok",
        ("索引过期: %s（跑 omni index）" % ",".join(stale)) if stale
        else "索引新鲜 · 共 %s 条" % total_files)
    # 7 degraded_recent
    week = time.time() - 7 * 86400
    fails = conn.execute("SELECT local_path,detail FROM push_log WHERE status='fail' "
                         "AND pushed_at>=? LIMIT 10", (week,)).fetchall()
    errs = conn.execute("SELECT label,last_error FROM mounts WHERE last_error IS NOT NULL "
                        "AND last_error!=''").fetchall()
    deg = ["push 失败: %s" % f["local_path"] for f in fails] + \
          ["%s: %s" % (e["label"], e["last_error"]) for e in errs]
    add("degraded_recent", "warn" if deg else "ok",
        "; ".join(deg) if deg else "近 7 天无降级事件")
    # 8 dup_files（知识层：重复大文件只报不删 —— S1）
    dups = conn.execute("""SELECT f.name, f.size, COUNT(*) AS n,
        GROUP_CONCAT(m.label) AS ms FROM files f JOIN mounts m ON m.id=f.mount_id
        WHERE f.is_dir=0 AND f.size > 10485760
        GROUP BY f.name, f.size HAVING n > 1 ORDER BY f.size DESC LIMIT 8""").fetchall()
    if dups:
        det = "; ".join("%s ×%d（%s/份，%s）" % (d["name"], d["n"], human(d["size"]),
                       d["ms"]) for d in dups)
        add("dup_files", "info", "疑似重复大文件 %d 组（只提示，不代删）" % len(dups), det)
    else:
        add("dup_files", "ok", "无 >10MB 同名同体积重复")
    # 9 cards_drift（卡片与配置漂移）
    inv_dir = os.path.expanduser(cfg.INVENTORY_DIR)
    cards = [f[:-3] for f in os.listdir(inv_dir)
             if f.endswith(".md") and f != "_index.md"] if os.path.isdir(inv_dir) else []
    if not cards:
        add("cards_drift", "info", "无资产卡片（跑 omni inv init）")
    else:
        known = {r["label"] for r in conn.execute("SELECT label FROM mounts")}
        kb_ids = {k["id"] for k in getattr(cfg, "KEY_DIRS", [])}
        ima_ids = {c[4:] for c in cards if c.startswith("ima-")}
        drift = [c for c in cards if c not in known and c not in kb_ids and c not in ima_ids]
        add("cards_drift", "warn" if drift else "ok",
            ("卡片与配置漂移: %s" % ",".join(drift)) if drift
            else "%d 张卡片与配置一致" % len(cards))
    # 10 index_coverage（配置挂载点是否全部入库）
    db_labels = {r["label"] for r in conn.execute("SELECT label FROM mounts")}
    cfg_labels = {m["label"] for m in mounts}
    miss = cfg_labels - db_labels
    add("index_coverage", "warn" if miss else "ok",
        ("配置中挂载点未入库: %s" % ",".join(miss)) if miss
        else "配置挂载点全部已入库")
    # 11 agents_rules（AI 工作规则声明）
    agents_p = os.path.expanduser(getattr(cfg, "AGENTS_PATH", "~/wg/AGENTS.md"))
    add("agents_rules", "ok" if os.path.isfile(agents_p) else "warn",
        "AGENTS.md 就位" if os.path.isfile(agents_p) else "缺少 ~/wg/AGENTS.md",
        "声明式规则让所有 AI 工具读同一套边界" if os.path.isfile(agents_p)
        else "建一份：raw 只读 / 先查重 / 留来源 / 冲突不覆盖")

    # 落库 + 报告
    for check, sev, summary, detail in results:
        conn.execute("INSERT INTO audits(run_id,ts,check_name,severity,summary,detail) "
                     "VALUES(?,?,?,?,?,?)", (run_id, int(time.time()), check, sev, summary, detail))
    conn.commit()
    nfail = sum(1 for r in results if r[1] == "fail")
    nwarn = sum(1 for r in results if r[1] == "warn")
    ninfo = sum(1 for r in results if r[1] == "info")
    lines = ["# omni 巡视报告 · %s" % time.strftime("%Y-%m-%d %H:%M"), "",
             "**结论：%d 项需关注**（fail %d / warn %d / info %d）" %
             (nfail + nwarn, nfail, nwarn, ninfo), ""]
    if nfail or nwarn:
        lines += ["## ❗ 需关注"]
        for check, sev, summary, detail in results:
            if sev in ("fail", "warn"):
                lines.append("- **[%s] %s**" % (sev, summary))
                if detail:
                    lines.append("  ↳ %s" % detail)
        lines.append("")
    lines += ["## ✅ 正常"]
    for check, sev, summary, detail in results:
        if sev == "ok":
            lines.append("- %s %s" % (summary, ("↳ " + detail) if detail else ""))
    lines += ["", "## ℹ️ 提示"]
    for check, sev, summary, detail in results:
        if sev == "info":
            lines.append("- %s %s" % (summary, ("↳ " + detail) if detail else ""))
    lines.append("")
    rp = os.path.expanduser(cfg.REPORT_DIR)
    os.makedirs(rp, exist_ok=True)
    out = os.path.join(rp, "daily-%s.md" % time.strftime("%Y-%m-%d"))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("报告: %s" % out)
    for check, sev, summary, detail in results:
        print("  [%-4s] %s" % (sev, summary))
    git_auto_commit(cfg, "audit %s: fail=%d warn=%d" % (time.strftime("%Y-%m-%d %H:%M"), nfail, nwarn))
    # 防噪音推送：仅 fail，或「首次出现」的 warn
    if args.push and cfg.AUDIT.get("webhook_wecom"):
        prev = conn.execute("""SELECT DISTINCT summary FROM audits
                               WHERE severity='warn' AND ts < ? AND run_id != ?""",
                            (time.time() - 86400, run_id)).fetchall()
        seen = {p["summary"] for p in prev}
        fresh = [r for r in results if r[1] == "fail" or
                 (r[1] == "warn" and r[2] not in seen)]
        if fresh:
            push_wecom(cfg.AUDIT["webhook_wecom"],
                       "omni 巡视：%s" % "；".join(r[2] for r in fresh))
    return 3 if nfail else (1 if nwarn else 0)

def push_wecom(url, text):
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(
            {"msgtype": "text", "text": {"content": text}}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        log("wecom pushed")
    except Exception as e:
        log("wecom push fail: %s" % e)

def cmd_report(cfg, args):
    rp = os.path.expanduser(cfg.REPORT_DIR)
    if not os.path.isdir(rp):
        print("（还没有报告，先跑 omni audit）"); return 1
    files = sorted(os.listdir(rp))[-args.days:]
    for fn in files:
        print(os.path.join(rp, fn))
    if args.open and files:
        subprocess.run(["open", os.path.join(rp, files[-1])])
    return 0

# ============================================================ db
def cmd_db(cfg, args):
    conn = connect(cfg, create=False)
    if args.action == "stats":
        dbp = os.path.expanduser(cfg.DB_PATH)
        for suffix in ("", "-wal", "-shm"):
            p = dbp + suffix
            if os.path.exists(p):
                print("%-10s %s" % (os.path.basename(dbp) + suffix, human(os.path.getsize(p))))
        for t in ("files", "kb_items", "push_log", "audits", "assets"):
            try:
                n = conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
                print("%-10s %d 行" % (t, n))
            except sqlite3.OperationalError:
                pass
        return 0
    if args.action == "vacuum":
        conn.execute("VACUUM")
        print("vacuum 完成")
        return 0
    if args.action == "rebuild":
        if not args.yes:
            print("rebuild 会删除索引缓存并重扫（不影响用户文件）。确认请加 --yes")
            return 2
        dbp = os.path.expanduser(cfg.DB_PATH)
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            p = dbp + suffix
            if os.path.exists(p):
                os.remove(p)
        print("已删除索引缓存。重跑: omni index --bg --full")
        return 0
    return 2

# ============================================================ main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="omni", description=__doc__)
    ap.add_argument("--version", action="version", version="omni " + VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="自检：配置/挂载/DB/解释器/依赖")
    p.set_defaults(func=cmd_doctor, json=False)

    p = sub.add_parser("mounts", help="列出挂载点 + 状态 + 上次扫描")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_mounts)

    p = sub.add_parser("selftest", help="核心逻辑自测")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("index", help="建索引（默认增量；首次自动全量）")
    p.add_argument("--full", action="store_true", help="全量重扫")
    p.add_argument("--content", action="store_true", help="内容索引（白名单挂载点）")
    p.add_argument("--limit", type=int, default=None, help="内容索引每挂载点上限")
    p.add_argument("--status", action="store_true", help="看进度")
    p.add_argument("--bg", action="store_true", help="后台运行")
    p.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("mounts", nargs="*", help="只处理指定挂载点")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("find", help="检索（≥3 字走全文，≤2 字走文件名）")
    p.add_argument("query")
    p.add_argument("--mount", nargs="*")
    p.add_argument("--ext", nargs="*")
    p.add_argument("--recent", help="如 7d / 24h")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.add_argument("--pack", action="store_true", help="结果打包成 md，供 AI 会话当上下文")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("save", help="收集箱：网页/文件存入 wiki/raw（源只读）")
    p.add_argument("src", nargs="?")
    p.add_argument("extra", nargs="?", help=argparse.SUPPRESS)
    p.add_argument("--title")
    p.add_argument("--list", action="store_true")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("git", help="知识层版本：log/status/commit（audit 后自动提交）")
    p.add_argument("action", nargs="?", default="log", choices=["log", "status", "commit"])
    p.set_defaults(func=cmd_git)

    p = sub.add_parser("ima", help="ima 官方 OpenAPI 直连：list/ls/pull/push")
    p.add_argument("action", choices=["setup", "list", "ls", "pull", "push"])
    p.add_argument("file", nargs="?")
    p.add_argument("--kb", help="知识库名称或 ID")
    p.add_argument("--note", action="store_true", help="强制走笔记通道（md/txt 免 COS 依赖）")
    p.set_defaults(func=cmd_ima)

    p = sub.add_parser("recent", help="最近变动")
    p.add_argument("span", nargs="?", default="7d")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser("du", help="占用排行（按一级目录）")
    p.add_argument("--top", type=int, default=20)
    p.set_defaults(func=cmd_du)

    p = sub.add_parser("open", help="Finder 打开 find 结果")
    p.add_argument("target")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("inv", help="L1 资产卡片")
    p.add_argument("action", choices=["init", "sync", "stats", "list"])
    p.add_argument("--tag"); p.add_argument("--host"); p.add_argument("--health")
    p.add_argument("--stale")
    p.set_defaults(func=cmd_inv)

    p = sub.add_parser("sync", help="L3 与 ima 双向")
    p.add_argument("action", choices=["push", "pull", "status", "mark"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rule", type=int, default=None)
    p.add_argument("--top", type=int, default=None, help="精选前 N 个候选（配合 --dry-run）")
    p.add_argument("file", nargs="?")
    p.add_argument("status2", nargs="?")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("audit", help="L4 主动巡视")
    p.add_argument("--push", action="store_true", help="推企微（webhook 已配置时）")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("report", help="列出/打开巡视报告")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("db", help="数据库维护")
    p.add_argument("action", choices=["stats", "vacuum", "rebuild"])
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_db)

    args = ap.parse_args(argv)
    if getattr(args, "status", False) and args.cmd == "index":
        return index_status()
    if getattr(args, "foreground", False):
        args.bg = False
    cfg = load_cfg()
    return args.func(cfg, args)

def index_status():
    st = read_state("scan-state.json", {})
    if not st:
        print("（还没有扫描状态 —— 跑 omni index --bg）")
        return 0
    print("%-16s %-6s %8s %10s  %s" % ("挂载点", "完成", "条数", "耗时", "时间"))
    for label, s in sorted(st.items()):
        print("%-16s %-6s %8s %10.1fs  %s" % (
            label, "是" if s.get("done") else "否",
            s.get("count", s.get("content", 0)), s.get("elapsed", 0),
            age_h(s.get("at"))))
    lock = state_file("index.lock")
    if os.path.exists(lock):
        print("\n状态: 扫描进行中（pid %s）" % open(lock).read().strip())
    else:
        print("\n状态: 空闲")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

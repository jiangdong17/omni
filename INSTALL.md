# omni 安装与多机配置说明书

> 面向第一次使用的人。照着从第 0 章读到底，就能在一台机器上装好，并把散落在
> 2～5 台电脑 + NAS 上的文件汇成一份可全文检索的索引。
>
> 全程只需 Python 3.9+，不装任何第三方包。

---

## 第 0 章 · 先搞清楚 omni 是什么，不是什么

这一章很重要。**90% 的配置困惑都来自误解 omni 的边界。**

### omni 做什么

```
       ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
       │  电脑 A      │   │  电脑 B      │   │   NAS       │
       │  (Windows)  │   │  (Linux)    │   │  (SMB)      │
       └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
              │                 │                 │
              └──────── SMB / NFS / 局域网 ───────┘
                                │
                    ┌───────────▼───────────┐
                    │  「索引主机」一台 Mac   │   ← omni 装在这台
                    │                       │
                    │  ~/mnt/laptop-c  ←挂载─┤
                    │  ~/mnt/nas-home  ←挂载─┤
                    │  ~/mnt/nas-photo ←挂载─┘
                    │         ↓
                    │   omni index  →  omni.db（一个 SQLite 文件）
                    │         ↓
                    │   omni find 关键词  →  立刻告诉你文件在哪
                    └───────────────────────┘
```

omni **只做三件事**：

1. **遍历**已经挂载到本机（或在本地磁盘上）的目录；
2. 把「路径 / 大小 / 修改时间 / 可选正文」写进**一个 SQLite 文件**；
3. 让你用 `find` / `recent` / `du` / `audit` 去查。

### omni 不做什么（关键）

| 它**不**做 | 说明 |
|---|---|
| ❌ 不同步 / 不搬运文件 | 它**从不**修改你的文件（硬约束 S1），只是个索引器 |
| ❌ 不负责让电脑互通 | 网络互通、共享暴露是**你自己的事**（SMB / NFS / 局域网） |
| ❌ 不提供云服务 | 数据 100% 在本机 `omni.db` 里，不上传任何地方 |
| ❌ 不跨机器合并索引 | 一台机器一个索引；多机各有各的库（见第 8 章） |

### 所以「配置几台电脑」真正的含义

> **配置几台电脑 = ①让每台机器的目录在索引主机上能访问到 → ②把它们逐条写进配置 → ③建索引**

第 3 章解决 ①，第 4 章解决 ②，第 5 章解决 ③。

---

## 第 1 章 · 环境要求

### 1.1 最低要求

| 项 | 要求 |
|---|---|
| 操作系统 | **macOS 或 Linux**（见 1.4 关于 Windows 的说明） |
| Python | **3.9 或更高** |
| SQLite | 需启用 **FTS5** 且支持 **trigram** 分词器 |
| 磁盘 | `omni.db` 体积约为「被索引文件数 × 1～3 KB」，外加正文索引（见 5.5） |
| 网络 | 索引主机能访问其它机器的共享（SMB/NFS） |

### 1.2 检查 Python 与 FTS5（★ 必须先过这一关）

```sh
python3 --version          # 需 ≥ 3.9
python3 - <<'PY'
import sqlite3
print("SQLite:", sqlite3.sqlite_version)
c = sqlite3.connect(":memory:")
c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
c.execute("INSERT INTO t VALUES('hello 全文检索')")
ok = c.execute("SELECT count(*) FROM t WHERE t MATCH '\"全文检索\"'").fetchone()[0] == 1
print("FTS5 + trigram:", "可用 ✅" if ok else "不可用 ❌")
PY
```

若输出 `FTS5 + trigram: 不可用 ❌`，说明你的 SQLite 太老或编译时未开启 FTS5：

- **macOS 自带 Python 3.9.6** 通常已可用；若不行，装官方 Python 3.12+（python.org 安装包自带完整 SQLite）。
- **Debian/Ubuntu**：`sudo apt install python3 libsqlite3-0`（系统 Python 一般已含 FTS5）。
- **Alpine**：需要 `sqlite` 编译进 FTS5，建议改用官方 Python 镜像。

> omni 自己也会在 `omni doctor` 里再验一遍，装完可以直接跑它确认。

### 1.3 支持的系统

| 系统 | 索引主机 | 说明 |
|---|---|---|
| macOS | ✅ 推荐 | `mount_smbfs` + 钥匙串存密码，体验最顺 |
| Linux | ✅ | `mount.cifs` / `mount.nfs`，靠 `fstab` 或 systemd 自动挂载 |
| Windows | ⚠️ 见下 | 不建议作为索引主机 |

### 1.4 关于 Windows（请务必读）

omni 依赖 POSIX 行为：用 `mount` 命令判断挂载状态、用 `open` 打开访达/文件管理器。
因此在 **Windows 上无法作为索引主机**。

但 Windows **非常适合作为「被索引的数据源」**：它的共享目录可以被一台 Mac/Linux
的索引主机通过 SMB 挂进来索引。推荐拓扑：

```
Windows 电脑（只暴露共享） →  SMB  →  Mac 索引主机（跑 omni）  →  检索/巡视
```

---

## 第 2 章 · 安装（在索引主机上）

### 2.1 选定「索引主机」

指一台**长期开机**、且能访问其它机器共享的机器。通常是你的主力笔记本或一台
常开的 Mac mini / Linux 小主机。

> 为什么需要「一台」而不是每台都装？见第 8 章。

### 2.2 获取代码

```sh
# 方式一：git
git clone https://github.com/jiangdong17/omni.git ~/wg
cd ~/wg

# 方式二：离线包（把 tar.gz 拷到目标机）
tar -xzf omni-v1.1.0.tar.gz
mv omni-repo ~/wg && cd ~/wg

chmod +x omni.sh
```

**为什么默认装在 `~/wg`？** omni 用 `OMNI_HOME` 环境变量定位自己（默认 `~/wg`），
配置、数据库、报告、日志全部在这个目录下。想换个位置，见 2.3。

### 2.3 自定义根目录（可选）

不想用 `~/wg`，就在 shell 配置里设：

```sh
# ~/.zshrc 或 ~/.bashrc
export OMNI_HOME="$HOME/omni"
```

以后每个命令都会读写 `$OMNI_HOME`，配置文件名固定为 `$OMNI_HOME/omnirc.py`。

### 2.4 创建配置

```sh
cp omnirc.py.example omnirc.py
cp AGENTS.md.example AGENTS.md      # 可选：给 AI 工具的工作规则
mkdir -p ~/mnt                      # 挂载点根目录（默认 MNT，见第 4 章）
$EDITOR omnirc.py                   # 先不用改，第 4 章再动
```

### 2.5 装进 PATH

```sh
# 方式一：软链（推荐）
ln -s "$PWD/omni.sh" /usr/local/bin/omni      # 可能需要 sudo

# 方式二：加进 PATH
echo 'export PATH="$HOME/wg:$PATH"' >> ~/.zshrc && exec zsh
```

验证：

```sh
omni --version      # → omni 1.1.0
```

### 2.6 首次自检

```sh
omni doctor     # 环境 + 配置 + 挂载 + 数据库总检
omni selftest   # 核心逻辑自测（11 项断言，不碰你的数据）
```

`selftest` 打印 `11 项断言全部通过` 即表示程序与你的 Python 兼容良好。

此时 `doctor` 会显示「0 个挂载点」——正常，因为还没配。继续第 3、4 章。

---

## 第 3 章 · 让别的电脑的目录可见（多机核心）

omni 索引的是**本机文件系统**。所以第一步是：把其它机器的共享，挂到索引主机的
`~/mnt/<label>/` 下。

> ★ **命名规则**：挂载目录名必须等于配置里的 `label`。
> 例如 `label="laptop-c"` → 必须挂到 `~/mnt/laptop-c`。这是 omni 找到它的唯一方式。

### 3.1 先在被索引的机器上开共享

**Windows**（作为数据源）：把要共享的目录设为共享，并确保账号可读写。

- 用管理员共享 `C$ / D$ / E$` 最省事，但需要一个**非空密码**的账号；
- Windows 默认策略禁止空密码账号走网络登录（SMB/RDP/SSH 都会失败），
  建议单独建一个远程访问账号并设密码。

**Linux**：装 Samba 并配置 `smb.conf` 的 `[share]` 段；或用 NFS（见 3.5）。

**macOS**：系统设置 → 通用 → 共享 → 文件共享，添加目录。

### 3.2 macOS 挂 SMB（索引主机是 Mac）

```sh
mkdir -p ~/mnt/laptop-c
mount_smbfs //user@192.168.1.20/C\$ ~/mnt/laptop-c
```

- 首次会提示密码；**建议存进钥匙串**（在「钥匙串访问」里新建「网络密码」条目），
  之后 `mount_smbfs` 会自动取用，不必明文写密码。
- 共享名含中文或空格时**必须 URL 编码**，否则报 `URL parsing failed`：

```sh
# 共享名「我的文档」→ %E6%88%91%E7%9A%84%E6%96%87%E6%A1%A3
mount_smbfs "//user@host/%E6%88%91%E7%9A%84%E6%96%87%E6%A1%A3" ~/mnt/nas-docs
```

- 卸载：`umount ~/mnt/laptop-c`（卡住时用 `diskutil unmount force ~/mnt/laptop-c`）。

### 3.3 Linux 挂 SMB（索引主机是 Linux）

```sh
sudo apt install cifs-utils
mkdir -p ~/mnt/laptop-c

# 凭据放独立文件（权限 600），避免密码进命令行/进程表
cat > ~/.smbcred-laptop <<'EOF'
username=youruser
password=yourpass
EOF
chmod 600 ~/.smbcred-laptop

sudo mount -t cifs //192.168.1.20/C$ ~/mnt/laptop-c \
  -o credentials=$HOME/.smbcred-laptop,uid=$(id -u),gid=$(id -g),iocharset=utf8
```

### 3.4 让挂载在重启后自动恢复

手动 `mount` 重启就没了。三种自动化方式：

**Linux —— 写进 `/etc/fstab`**

```
//192.168.1.20/C$  /home/you/mnt/laptop-c  cifs  credentials=/home/you/.smbcred-laptop,uid=1000,gid=1000,iocharset=utf8,_netdev,nofail  0  0
```

`_netdev`（等网络就绪）+ `nofail`（挂不上也不阻塞开机）是关键。

**macOS —— LaunchAgent 登录时自动挂**

新建 `~/Library/LaunchAgents/local.omni.mount.plist`：

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

配上 `~/wg/mount-all.sh`（自己写，逐条 `mount_smbfs`，先判断有没有已挂）：

```sh
#!/bin/sh
# 幂等挂载：已挂的跳过
m() { mount | grep -q " on $2 " || mount_smbfs "$1" "$2"; }
mkdir -p ~/mnt/laptop-c ~/mnt/nas-home
m "//user@192.168.1.20/C\$" ~/mnt/laptop-c
m "//user@192.168.1.31/home" ~/mnt/nas-home
```

装载：`launchctl load ~/Library/LaunchAgents/local.omni.mount.plist`

> ⚠️ macOS 的 TCC 隐私保护：**launchd 启动的进程读不了 `~/Desktop`、`~/Documents`、`~/Downloads`**。
> 所以脚本和配置一定放 `~/wg` 这类普通目录，别放桌面。

### 3.5 另一条路：NFS

对 Linux/NAS 之间更轻量：

```sh
# 服务端 /etc/exports：  /volume1/data  192.168.1.0/24(rw,sync,no_subtree_check)
sudo mount -t nfs 192.168.1.31:/volume1/data ~/mnt/nas-data
```

### 3.6 最简单的一条路：本地盘 / 外接硬盘

如果某台机器的数据你自己就能拷到索引主机上（或本来就是外接盘），
直接把它接到索引主机、挂进 `~/mnt/<label>` 就行，不需要任何网络共享：

```sh
mkdir -p ~/mnt/backup-disk
# 外接盘挂载后，软链或 bind-mount 进去
sudo mount --bind /Volumes/Backup ~/mnt/backup-disk     # macOS/Linux
```

### 3.7 挂载检查

```sh
ls ~/mnt/                     # 应看到各 label 目录
mount | grep mnt              # 确认真的挂上了（不是空目录）
```

> ★ **空目录陷阱**：SMB 挂载失败时，`~/mnt/xxx` 会是一个**空目录**而不是报错。
> omni 的 `doctor` 会以「未挂载」提示，别被空目录骗过去。

---

## 第 4 章 · 配置 MOUNTS（把每台机器写进去）

编辑 `~/wg/omnirc.py` 的 `MOUNTS` 列表。每条对应一个共享目录。

### 4.1 字段详解

| 字段 | 必填 | 说明 |
|---|---|---|
| `label` | ✅ | 唯一标识。**必须与 `~/mnt/<label>` 目录名一致**。会出现在检索结果、卡片、报告里 |
| `host` | ✅ | 逻辑主机名，用于分组与「主机是否可达」判断 |
| `ip` | ✅ | 该主机的地址，用于巡视时做 TCP 445 可达性探测 |
| `share` | ✅ | 共享名（仅记录用，方便自己对账） |
| `content` | | `True` = 对该共享做**正文**抽取（慢、占库）；`False`（默认）= 只索引文件名/大小/时间 |
| `max_scan_s` | | 单挂载点扫描硬上限（秒）。不写则用全局 `SCAN_HARD_LIMIT_S`（默认 600） |
| `max_depth` | | 内容索引的递归深度上限，默认 12 |

### 4.2 `content` 开还是关？（最影响体验的一个决定）

| 目录类型 | 建议 | 理由 |
|---|---|---|
| 文档目录（合同、笔记、报告） | ✅ 开 | 这正是「正文检索」的价值所在 |
| 代码仓库 | ✅ 开 | 搜函数名/注释很有用 |
| 照片 / 视频 / 音乐 | ❌ 关 | 媒体扩展名本来就在排除表里，开了白费 |
| 系统盘 / 整个 C 盘 | ❌ 关 | 文件数巨大（实测某台 Windows C 盘 31 万文件），开正文会拖很久 |

### 4.3 一个完整示例（3 台机器 + NAS）

```python
MNT = "~/mnt"

MOUNTS = [
    # ── 笔记本（Windows，走局域网 SMB）──
    dict(label="laptop-c",  host="laptop", ip="192.168.1.20", share="C$", content=False,
         max_scan_s=2400),          # 系统盘文件极多，单独放宽扫描上限
    dict(label="laptop-d",  host="laptop", ip="192.168.1.20", share="D$", content=True),

    # ── 台式机（Linux）──
    dict(label="desktop-d", host="desktop", ip="192.168.1.21", share="data", content=True,
         max_depth=8),

    # ── NAS ──
    dict(label="nas-home",  host="nas", ip="192.168.1.31", share="home",  content=True),
    dict(label="nas-photo", host="nas", ip="192.168.1.31", share="photo", content=False),
    dict(label="nas-video", host="nas", ip="192.168.1.31", share="video", content=False),
]
```

同时别忘了巡视的探测目标（`AUDIT.tunnel_targets`），让 omni 知道去哪几台机器探活：

```python
AUDIT = {
    "tunnel_targets": [("laptop", "192.168.1.20", 445), ("desktop", "192.168.1.21", 445),
                       ("nas", "192.168.1.31", 445)],
    ...
}
```

### 4.4 校验配置

```sh
omni mounts     # 列出每个 label + 挂载状态 + 上次扫描时间
omni doctor     # 「N 个挂载点，M / N 已挂载」应无 ⚠
```

---

## 第 5 章 · 建索引

### 5.1 首次全量

```sh
omni index            # 首次自动全量；之后每次调用都是增量
```

小数据集（几万文件）几十秒；大数据集（几十万到百万文件）可能十几分钟到半小时。

### 5.2 后台跑 + 看进度

大索引别占着终端：

```sh
omni index --bg       # 后台运行，立即返回
omni index --status   # 看进度（当前目录、已扫描数、耗时）
```

### 5.3 内容索引（正文检索）

```sh
omni index --content            # 对配置里 content=True 的挂载点抽正文
omni index --content --limit 5000   # 每个挂载点最多收 5000 个文件（先试水）
omni index laptop-d             # 只处理指定的挂载点
```

正文抽取支持：`.md .txt .csv .docx .xlsx .doc .rtf .odt .html .xml` 等（见 `CONTENT_EXT`）。

### 5.4 增量与重扫

```sh
omni index            # 增量：只处理变化的文件
omni index --full     # 全量重扫（挂着文件变动检测不准时用）
```

### 5.5 性能与体积参考（实测值）

| 场景 | 实测 |
|---|---|
| 单一 Windows C 盘 31 万文件 | 约 **1625 秒**（所以默认 600s 上限不够，需 `max_scan_s=2400`） |
| 全量 83.6 万条目（多机 + NAS） | 约 **20 分钟** |
| `omni.db` 体积 | 83.6 万条目 + 部分正文 ≈ **3.5 GB** |

**控制体积的手段**（都在 `omnirc.py`）：

```python
CONTENT_EXT        = {...}    # 只对这些扩展名抽正文（收窄它）
CONTENT_KEEP_CHARS = 20*1024  # 每个文件只留前 20K 字符
CONTENT_MAX_BYTES  = 2*1024*1024  # 超过 2MB 的文件不抽正文
```

> ⚠️ FTS5 的 **trigram 分词器对中文会膨胀约 50 倍**，所以 `CONTENT_KEEP_CHARS`
> 千万不能放开。真把库撑大了，用 `omni db rebuild`（需 `--yes`）重建。

---

## 第 6 章 · 日常使用

### 6.1 检索

```sh
omni find 报价单                # ≥3 个字符 → 全文检索
omni find C++                   # 特殊字符不会崩
omni find 合同 --ext .docx .pdf  # 只找这些类型
omni find 会议 --mount nas-home  # 只在某个挂载点里找
omni find 方案 --recent 7d       # 只要最近 7 天改过的
omni find 方案 --pack            # 把结果+摘要+正文节选打包成 md（喂给 AI 当上下文）
```

> 规则：**查询词 ≤ 2 个字符**时走文件名匹配（`LIKE`），**≥ 3 个字符**时走全文索引（`MATCH`）。
> 这是为了绕开 trigram 对短词无效的限制。

### 6.2 其它常用

```sh
omni recent 3d          # 最近 3 天变动过的文件
omni du --top 20        # 占用排行（按一级目录）
omni open 3             # 用访达/文件管理器打开上次 find 结果的第 3 条
omni report             # 查看/打开巡视报告
```

### 6.3 资产卡片（把重要目录单独建档）

```sh
omni inv init           # 按配置里的 KEY_DIRS 生成卡片
omni inv sync           # 用索引实测数据（体积/文件数）回填卡片
omni inv list           # 列出所有卡片
```

> 卡片里有一个「## 人工判断」区块，omni **永远不写**，只留给人写。

### 6.4 巡视（主动体检）

```sh
omni audit              # 11 项检查：主机可达/挂载/磁盘/备份滞后/垃圾/重复/索引新鲜度…
```

它**只报告不代删**。发现的垃圾目录、重复文件、落后备份，都只输出命令让你自己决定。

### 6.5 知识层（可选）

```sh
omni save https://example.com/article    # 网页存进收集箱 wiki/raw/
omni save ~/Downloads/某文件.pdf         # 文件副本进收集箱（源文件只读）
omni git log                             # 知识层的版本历史
```

---

## 第 7 章 · 自动化（长期运行的关键）

装完能用，但**每天自动跑**才是 omni 的完整形态。

### 7.1 开机/登录自动挂载

见 3.4。要点：`nofail` / 幂等 / 等网络就绪。

### 7.2 每日自动「索引 + 巡视」

**macOS —— LaunchAgent**

`~/Library/LaunchAgents/local.omni.daily.plist`：

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

先手动跑一次 `omni.sh index --bg && omni.sh audit` 确认路径与权限没问题。

**Linux —— cron**

```cron
# crontab -e
0 9 * * *  cd $HOME/wg && ./omni.sh index --bg >> $HOME/wg/.omni/daily.log 2>&1
15 9 * * * cd $HOME/wg && ./omni.sh audit    >> $HOME/wg/.omni/daily.log 2>&1
```

### 7.3 让它别被「对端关机」拖死

这是多机场景最常见的坑：**某台机器关机后，它的 SMB 挂载点会让 `statvfs` / `os.walk`
进入内核不可中断睡眠（连 Ctrl-C 都杀不掉）。**

omni 已经处理了这一点：

- 所有对挂载点的探测都放在**子进程**里，带超时，超时就 `killpg`；
- 巡视时会先做 TCP 可达性探测，判定**主机不可达**就跳过该机器的磁盘/备份/垃圾/索引检查。

你要做的只有一件事：**把 `AUDIT.tunnel_targets` 填对**，让 omni 能判断主机死活。

---

## 第 8 章 · 多机拓扑怎么选

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. 中心式**（推荐） | 一台常开的机器装 omni，通过 SMB/NFS 把其它机器的目录挂进来索引 | 一个索引查全部；配置集中；查得快 | 索引主机得常开；挂载依赖网络 |
| **B. 各自为政** | 每台机器各装一个 omni，各索引本机 | 无网络依赖；各自独立 | 要查 3 次；数据不汇总（omni 不支持跨库合并） |
| **C. 落在 NAS** | 把 omni 装在能跑 Linux 的 NAS 上（Docker 亦可） | NAS 天然常开、离数据最近 | 需要 NAS 支持；首次配置麻烦 |

选 A 的场景：你有一条「主力机 + 若干偶尔开机的工作机」的结构。
选 C 的场景：NAS 性能足够且你希望完全无人值守。

> omni **不支持**把多个机器的索引合并成一个库。如果一定要汇总（如方案 B），
> 只能各自建立索引后，在应用层（比如把 `find --json` 的结果拼起来）自己处理。

---

## 第 9 章 · 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `omni mounts` 显示未挂载，但 `ls` 能看到目录 | **空目录陷阱**：挂载失败留下空目录 | 重新 `mount_smbfs`；检查密码/网络；`mount \| grep mnt` 确认 |
| `mount_smbfs: URL parsing failed` | 共享名含中文/空格，未 URL 编码 | 用 `%XX` 编码（见 3.2） |
| SMB 认证失败但密码对 | Windows 空密码账号禁止网络登录 / 账号名错 | 建一个带密码的账号；用 `smbutil view //user@host` 排查 |
| 索引卡住不动，Ctrl-C 无效 | 某个挂载点是对端已关机的**死挂载** | 这是内核行为，omic 已用子进程+超时规避；先卸载该死挂载点：`diskutil unmount force ~/mnt/xxx` |
| `omni doctor` 报 FTS5 不可用 | Python 的 SQLite 未编译 FTS5 | 换官方 Python 3.12+（见 1.2） |
| 中文搜不到 | 索引时该文件没做**内容索引**（`content=False` 或不在 `CONTENT_EXT`） | 改配置后 `omni index --content <label>` |
| 库太大（>4 GB） | 内容索引范围配得太宽 | 收窄 `CONTENT_EXT`，调小 `CONTENT_KEEP_CHARS`，然后 `omni db rebuild --yes` |
| 大目录扫不完、反复中断 | 超过单点扫描上限 | 给该挂载点配 `max_scan_s`（如 `2400`） |
| 定时任务不执行 | macOS TCC 或路径问题 | 配置/脚本别放 `~/Desktop`；先手动跑一遍确认能通 |
| `omni find` 结果里的路径打不开 | 挂载点没挂上 | 先 `omni mounts` 确认 |

---

## 第 10 章 · 升级与卸载

### 升级

```sh
cd ~/wg
git pull            # 或解压新版覆盖（保留你自己的 omnirc.py）
omni selftest       # 确认新版本正常
omni index          # 数据库结构如有变化会自适应
```

> `omnirc.py` 是你的配置，**不会被 git 覆盖**（已在 `.gitignore` 里），尽管放心 `git pull`。

### 卸载

omni 不装系统服务、不写系统目录，卸载就是删目录：

```sh
# 1) 停掉定时任务（如果配了）
launchctl unload ~/Library/LaunchAgents/local.omni.daily.plist   # macOS
# crontab -e 里删掉对应行                                        # Linux

# 2) 卸载挂载点
diskutil unmount ~/mnt/laptop-c      # macOS
sudo umount ~/mnt/laptop-c           # Linux

# 3) 删目录
rm -rf ~/wg            # ★ 注意：这会连你的配置和 omni.db 一起删掉
rm -f /usr/local/bin/omni
```

> ⚠️ `~/wg` 里除了程序，还有你的配置、索引、报告。删之前想清楚。
> 你的**原始文件**从头到尾没被 omni 碰过，所以删 omni 不会影响任何数据。

---

## 附录 A · 配置字段速查

```python
MNT = "~/mnt"                 # 挂载点根目录；实际路径 = MNT + "/" + label

MOUNTS = [
  dict(
    label="laptop-d",         # 必填；须与 ~/mnt/<label> 目录名一致
    host="laptop",            # 必填；逻辑主机名
    ip="192.168.1.20",        # 必填；巡视探活用
    share="D$",               # 必填；共享名（对账用）
    content=True,             # 是否抽正文
    max_scan_s=2400,          # 可选；单点扫描上限（秒）
    max_depth=10,             # 可选；内容索引深度上限，默认 12
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

SCAN_HARD_LIMIT_S   = 600     # 单挂载点扫描硬上限
SCAN_MIN_INTERVAL_H = 6       # 自动扫描最小间隔
SEARCH_LIMIT        = 50      # find 默认返回条数

CONTENT_EXT        = {...}    # 抽正文的扩展名白名单
CONTENT_KEEP_CHARS = 20*1024  # 每文件保留字符数
CONTENT_MAX_BYTES  = 2*1024*1024

KEY_DIRS   = [...]            # L1 资产卡片
SYNC_RULES = [...]            # L3 同步规则

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

## 附录 B · 命令速查

| 命令 | 作用 |
|---|---|
| `omni doctor` | 自检：解释器 / SQLite+FTS5 / 配置 / 挂载 / 数据库 |
| `omni mounts [--json]` | 列出挂载点、状态、上次扫描 |
| `omni selftest` | 核心逻辑自测（11 项断言） |
| `omni index [--full\|--content\|--limit N\|--status\|--bg] [挂载点…]` | 建/更新索引 |
| `omni find <词> [--mount…] [--ext…] [--recent 7d] [--limit N] [--json] [--pack]` | 检索 |
| `omni recent [7d\|24h]` | 最近变动 |
| `omni du [--top N]` | 占用排行 |
| `omni open <序号>` | 打开 find 结果中的某一条 |
| `omni inv init\|sync\|stats\|list` | 资产卡片 |
| `omni save <url\|文件>` | 存入收集箱（源只读） |
| `omni git log\|status\|commit` | 知识层版本 |
| `omni sync push\|pull\|status\|mark` | 与知识库（如 ima）双向同步 |
| `omni audit [--push]` | 巡视（11 项检查） |
| `omni report` | 巡视报告 |
| `omni db stats\|vacuum\|rebuild` | 数据库维护（`rebuild` 需 `--yes`） |
| `omni ima list\|ls\|pull\|push` | ima 官方 OpenAPI 直连（可选） |

---

## 最后：记住三条

1. **omni 从不改你的文件** —— 只写 `$OMNI_HOME` 下的索引与报告，放心长期挂着跑。
2. **多机配置 = 挂载 + 填 label** —— 挂到 `~/mnt/<label>`，再把 `<label>` 写进 `MOUNTS`。
3. **内容索引是取舍** —— 开对了是神器，开错了是几个 GB 的库。按目录性质决定。

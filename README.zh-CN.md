[English](README.md) | **简体中文**

# omni

> 一个纯标准库（stdlib-only）的**本地索引 / 编目 / 巡视**命令行工具。
> 把分散在多台机器、多个网络共享上的文件，汇成一份可全文检索的本地索引；
> 再叠加一层「知识层」把随手收集的资料沉淀成可维护的页面。

单文件实现，Python 3.9+ 即可运行，**不依赖任何第三方包**（含 SMB 挂载、全文检索、
网页抓取、知识库同步、定时巡视）。适合：家里/办公室若干台电脑 + 一台 NAS 的开发者，
想把「文件都在哪、某句话在哪份文档里」这件事一次性解决。

---

## 它解决什么问题

- 文件散落在多台主机、多个 SMB 共享、一堆备份盘里，找东西靠记忆和 `find` 遍历。
- 想按**正文内容**搜（"上次那份报价单里写的到底是 8% 还是 8.5%"），而不是按文件名。
- 定时想知道：隧道通了没、盘快满没、备份是不是又滞后了、索引是不是馊了。
- 随手存的网页/资料越攒越乱，需要一个「收集箱 → 整理」的轻流程，而不是又一个网盘。

omni 用**一个 SQLite 文件**装下全部索引（FTS5 全文），提供统一的 `find` / `recent` /
`du` / `audit` / `report` 命令，并且**永不修改你的原始文件**。

---

## 安全模型（重要，请先读）

**S1 —— omni 绝不删 / 移 / 改任何用户文件。**

- 只在配置的目录（默认 `~/wg/` 下）写入：数据库、资产清单、巡视报告、知识页、状态/日志。
- `omni save`（收集箱）只把网页正文或文件**副本**写进 `wiki/raw/`，源文件永远只读。
- 巡视发现的「垃圾目录 / 重复文件 / 落后备份」**只报告，不代删**，命令由你自己复制执行。
- 破坏性操作（`omni db rebuild`）必须显式 `--yes`。
- 因此它天然适合长期挂在定时任务里跑，不会在你不知情时动你的数据。

---

## 安装

> 📖 **要装多台电脑？请看 [INSTALL.zh-CN.md](INSTALL.zh-CN.md)** —— 完整说明书：环境检查、
> 共享挂载（SMB/NFS/本地盘）、多机 `MOUNTS` 配置、定时自动化、故障排查。本节只是快速上手。

要求：Python 3.9+（建议 3.10+，SQLite 需要启用 FTS5 —— macOS 自带与官方发行版均满足）。

```sh
git clone https://github.com/jiangdong17/omni.git ~/wg
cd ~/wg
chmod +x omni.sh
ln -s "$PWD/omni.sh" /usr/local/bin/omni     # 或把本目录加进 PATH
omni --version
```

首次运行会提示缺少配置，按下一步创建。

### 配置

复制示例配置、改成你自己的挂载点：

```sh
cp omnirc.py.example omnirc.py
cp AGENTS.md.example AGENTS.md        # 可选：给 AI 工具的工作规则
$EDITOR omnirc.py
```

最少只需改 `MOUNTS`：每台主机、每个共享一行。改成你的目录后：

```sh
omni doctor      # 自检：配置 / 挂载 / 数据库 / 解释器 / 依赖
omni index       # 建索引（首次自动全量；之后每次增量）
omni find 关键词
```

> 默认工作根目录是 `~/wg`；用环境变量 `OMNI_HOME` 可指向别处：
> `OMNI_HOME=~/omni omni doctor`。

---

## 多语言

界面支持多语言。语言判定优先级：

1. `--lang en|zh`（放在子命令**之前**：`omni --lang zh find 报告`）
2. 环境变量 `OMNI_LANG`
3. `LC_ALL` / `LC_MESSAGES` / `LANG` —— `zh*` 开头的 locale 选中中文
4. 都没有则回落 **英文**

```sh
omni --lang zh find 报告             # 单次
export OMNI_LANG=zh                  # 当前 shell 内一直生效
omni --lang en --help                # 帮助文本同样跟随语言
```

译文集中在 **`omni_i18n.py`**：key 是 `omni.py` 里的中文原文，value 是目标语言文本。
缺失的 key 会回落中文原文，所以**翻译不全绝不会让程序出错**。

**新增一门语言**：复制 `CATALOG["en"]` 整块，换成你的语言代码，然后**只翻译 value** ——
不要改 key，也不要丢掉任何 printf 占位符（`%s` / `%d` / `%%`）。

```sh
# 校验：有没有漏 key、有没有丢占位符
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

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `omni doctor` | 自检：配置、挂载、数据库、解释器、依赖 |
| `omni mounts` | 列出挂载点 + 在线状态 + 上次扫描时间 |
| `omni selftest` | 核心逻辑自测（11 项断言，不碰你的数据） |
| `omni index [--full\|--content\|--bg\|--status]` | 建/更新索引（默认增量，`--content` 抽正文，`--bg` 后台） |
| `omni find <词> [--pack]` | 检索；≥3 字走全文，≤2 字走文件名；`--pack` 打包结果+摘要+正文节选 |
| `omni recent [--days N]` | 最近变动的文件 |
| `omni du [--top N]` | 占用排行（按一级目录） |
| `omni open <n>` | 用访达打开 `find` 结果的第 n 条 |
| `omni save <url\|文件>` | 收集箱：网页/文件存入 `wiki/raw/`（源只读） |
| `omni inv init\|sync\|stats\|list` | L1 资产卡片（关键目录建档 + 用索引统计回填） |
| `omni sync push\|pull\|status\|mark` | L3 与知识库（如 ima）双向同步 |
| `omni audit [--push]` | L4 主动巡视（11 项检查 + 自动提交知识层版本） |
| `omni report` | 列出 / 打开巡视报告 |
| `omni git log\|status\|commit` | 知识层版本管理 |
| `omni ima list\|ls\|pull\|push` | ima 官方 OpenAPI 直连（可选） |
| `omni db stats\|vacuum\|rebuild` | 数据库维护（`rebuild` 需 `--yes`） |

全局选项：`--version`、`--lang en|zh`、`-h`。

---

## 设计取舍（踩过的坑）

这些是实际跑 80 万+ 文件后固化的结论，改代码前值得一读：

- **FTS5 trigram 对中文膨胀约 50×**。所以内容索引必须限长（`CONTENT_KEEP_CHARS`，
  默认 20K 字符/文件）并限制扩展名白名单，否则数据库会失控。
- **外部内容模式的坑**：`content='files'` 的全文字表必须包含 FTS 的全部列，
  否则 `snippet()` 会报 `no such column`。索引进程直接 `UPDATE files.content` 由触发器同步。
- **死挂载点会拖垮一切**。对端关机的 SMB 挂载点，`statvfs` / `os.walk` 会进入
  **内核不可中断睡眠**（连信号都杀不掉）。所以所有探测都放 **子进程 + `start_new_session`
  + 超时 `killpg`**；主机不可达时直接跳过磁盘/备份/垃圾/索引检查。
- **后台任务必须彻底脱离会话**：`stdin=DEVNULL` + 新建会话，否则托管解释器会报
  `Bad file descriptor`；`nohup &` 会被会话回收，所以 `index --bg` 内部自行 detach。
- **索引锁要验进程是否存活**（只按 mtime 判断会被死进程的残留锁卡住一小时）。
- **大共享要单独放宽扫描上限**。实测某台 Windows C 盘 31 万文件需 ~1625s，
  600s 的默认上限必然中断（断点续扫可恢复，但会反复）。给该挂载点配 `max_scan_s`。

---

## 目录结构（运行时生成，均在 `OMNI_HOME` 下）

```
~/wg/
├── omni.py            # 主程序（单文件，仅标准库）
├── omni_i18n.py       # 翻译目录（key = 中文原文）
├── omnirc.py          # 配置（唯一真源）
├── omni.sh            # 入口脚本
├── README.md          # 英文说明
├── INSTALL.zh-CN.md   # ★ 安装与多机配置说明书
├── omni.db            # SQLite 索引（唯一数据文件）
├── inventory/         # L1 资产卡片
├── reports/           # 巡视报告
├── wiki/
│   ├── _index.md      # 知识层导航
│   └── raw/           # 收集箱（omni save 落地）
├── AGENTS.md          # 给 AI 工具的工作规则声明
└── .omni/             # 状态 / 日志 / 锁 / 备份
```

---

## License

MIT，见 [LICENSE](LICENSE)。

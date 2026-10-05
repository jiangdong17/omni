# Changelog

All notable changes to omni are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

---

## 1.2.1 — 2026-10-05

### Fixed

- **`OMNI_HOME` portability was only half-true.** `omnirc.py.example` claimed all
  paths were derived from `OMNI_HOME`, but `DB_PATH`, `INVENTORY_DIR`, `REPORT_DIR`,
  `LOG_PATH`, `STATE_DIR`, `WIKI_RAW` and `AGENTS_PATH` were hardcoded to `~/wg/...`.
  With a custom root (`export OMNI_HOME=/opt/omni`) the database was silently written
  to `~/wg/omni.db` while state and logs went to `$OMNI_HOME/.omni` — a split-brain
  layout, and it created `~/wg` out of thin air. The example config now derives every
  path from `OMNI_HOME`. `MNT` stays independent on purpose (see the comment there).
  ([INSTALL.md §2.3](INSTALL.md))

### Added

- **Clear configuration errors instead of stack traces.** `load_cfg()` now reports
  a missing `omnirc.py`, a config file that raises on import, or a config file that
  is missing required keys — with the exact key names and the template path — in the
  language selected by `--lang` / `OMNI_LANG`. Previously this surfaced as a raw
  `FileNotFoundError` / `AttributeError`.
- `CHANGELOG.md` (this file).

---

## 1.2.0 — 2026-10-05

### Added

- **Multi-language support (English / 简体中文).** `--lang en|zh`, the `OMNI_LANG`
  environment variable, or automatic detection from `LC_ALL` / `LC_MESSAGES` / `LANG`;
  anything not matching `zh*` falls back to English. Untranslated strings silently
  fall back to the source Chinese text, so a partial translation never breaks a run.
  Catalog: `omni_i18n.py` (226 strings). `argparse`'s own `--help` and error messages
  are localized too.
- Bilingual documentation: `README.md` / `README.zh-CN.md`,
  `INSTALL.md` / `INSTALL.zh-CN.md`.

### Fixed

- `state_dir()` ignored `OMNI_HOME` and always resolved to `~/wg/.omni`.
- `cmd_index` wrote `index.lock` before ensuring the state directory existed, so a
  fresh `OMNI_HOME` failed with `FileNotFoundError`.

---

## 1.1.0 — 2026-10-05 · first public release

### Added

- Core CLI: `doctor`, `mounts`, `selftest`, `index`, `find`, `recent`, `du`, `open`,
  `inv`, `sync`, `audit`, `report`, `db`. Single file, standard library only,
  Python 3.9+ over SQLite FTS5 (trigram).
- Knowledge layer: `omni save <url|file>` (inbox → `wiki/raw/`), `omni git log|status|commit`
  (version control for the knowledge layer), `find --pack` (results + summary + excerpts
  packaged into a single Markdown file for an AI session), `sync push --top N`.
- Asset cards gained `status` / `sources` frontmatter plus a human-only
  `## 人工判断` section that omni never writes to.
- `audit` expanded from 7 to 11 checks, and now commits the knowledge layer afterwards.
- `AGENTS.md` — declarative rules for any AI tool working in the same tree.
- Tagged releases from 1.1.0 onwards; see
  [all releases](https://github.com/jiangdong17/omni/releases).

# Backup Checker - 本地备份清单校验与恢复演练 CLI

本地备份清单校验与恢复演练命令行工具。包含 `init`、`scan`、`report`、`drill`、`history` 等子命令，支持：

- 读取清单配置（YAML）
- 扫描源目录和备份目录
- 计算文件校验和（md5/sha1/sha256/sha512）
- 分类文件状态：缺失、过期、损坏、未登记、正常
- 巡检结果持久化到本地历史库
- 两次巡检结果对比
- 多格式报告导出（JSON/CSV/文本）
- 恢复演练（从备份恢复并验证）

## 项目结构

```
backup_checker/
├── backup_checker/
│   ├── __init__.py
│   ├── constants.py      # 退出码、状态常量
│   ├── config.py         # 配置模块 - 清单配置读取与验证
│   ├── scanner.py        # 扫描模块 - 目录扫描与校验和计算
│   ├── comparator.py     # 比对模块 - 文件分类
│   ├── history.py        # 历史模块 - 巡检结果持久化与对比
│   ├── reporter.py       # 报告模块 - 报告生成与导出
│   ├── drill.py          # 演练模块 - 恢复演练
│   └── cli.py            # CLI主入口
├── pyproject.toml
├── requirements.txt
└── README.md
```

## 退出码约定

| 退出码 | 常量名 | 说明 |
|--------|--------|------|
| 0 | EXIT_SUCCESS | 成功 |
| 1 | EXIT_GENERAL_ERROR | 通用错误 |
| 2 | EXIT_CONFIG_ERROR | 配置错误 |
| 3 | EXIT_DUPLICATE_TARGET | 重复目标路径 |
| 4 | EXIT_CHECKSUM_MISMATCH | 备份校验和不一致 |
| 5 | EXIT_MISSING_FILE | 缺失文件 |
| 6 | EXIT_DRILL_FAILED | 恢复演练失败 |
| 7 | EXIT_REPORT_ERROR | 报告生成错误 |
| 8 | EXIT_HISTORY_ERROR | 历史操作错误 |

## 安装

```bash
cd backup_checker
pip install -e .
```

安装后可以使用 `backup-checker` 命令。

## 子命令

### `init` - 初始化清单配置

创建 `backup-manifest.yaml` 配置文件。

```bash
backup-checker init --output-dir DIR --source-dir SRC --backup-dir BKP [--name NAME]
```

**选项：**
- `-o, --output-dir`：配置文件输出目录（默认：当前目录）
- `-s, --source-dir`：源目录路径（相对于 output-dir）
- `-b, --backup-dir`：备份目录路径（相对于 output-dir）
- `-n, --name`：清单名称（默认：backup-check）

---

### `scan` - 扫描与比对

扫描源目录和备份目录，计算校验和，比对结果，保存到历史库。

```bash
backup-checker scan [--config FILE] [--no-save] [--compare-history/--no-compare-history] [--brief]
```

**选项：**
- `-c, --config`：配置文件路径（默认：自动查找）
- `--no-save`：不保存到历史库
- `--compare-history/--no-compare-history`：是否与上次历史对比（默认：是）
- `--brief`：只显示摘要，不显示详情

---

### `report` - 生成报告

生成各种格式的校验报告。

```bash
backup-checker report [--config FILE] [--history FILE] [--format FMT] [--output FILE] [--brief]
```

**选项：**
- `-c, --config`：配置文件路径
- `--history`：从指定历史文件读取结果，而非重新扫描
- `-f, --format`：输出格式：console/json/csv/text（默认：console）
- `-o, --output`：输出文件路径（json/csv/text 格式必需）
- `--brief`：只显示摘要

---

### `drill` - 恢复演练

从备份恢复文件到临时目录（或指定目录），重新计算校验和并与源比对。

```bash
backup-checker drill [--config FILE] [--history FILE] [--restore-dir DIR] [--keep-restore] [--file PATH]
```

**选项：**
- `-c, --config`：配置文件路径
- `--history`：使用指定历史文件进行演练
- `-r, --restore-dir`：恢复目录（默认：临时目录，演练后自动清理）
- `--keep-restore`：演练完成后保留恢复的文件
- `-f, --file`：只演练指定文件（可重复）

---

### `history` - 历史管理

查看和管理扫描历史。

```bash
backup-checker history [--config FILE] [--show REF] [--compare FIRST SECOND]
```

**选项：**
- `-c, --config`：配置文件路径
- `--show`：显示指定历史文件详情（文件名或索引，-1 为最新）
- `--compare`：比较两个历史文件

---

## 验收主链路 - 使用示例目录

项目附带 `examples/` 目录，包含可复现的测试数据：

```
examples/
├── source/                    # 源目录
│   ├── documents/
│   │   ├── report.txt         # [OK] 正常文件（备份一致）
│   │   ├── notes.txt          # [OK] 正常文件（备份一致）
│   │   ├── contract.txt       # [CORR] 损坏文件（备份内容不同）
│   │   └── missing-file.txt   # [MISS] 缺失文件（备份不存在）
│   └── database/
│       ├── backup.sql         # [OK] 正常文件
│       └── config.json        # [OK] 正常文件
└── backup/                    # 备份目录
    ├── documents/
    │   ├── report.txt         # [OK] 与源一致
    │   ├── notes.txt          # [OK] 与源一致
    │   ├── contract.txt       # [CORR] 内容被篡改
    │   └── old-project-2020.zip  # [EXP] 过期文件（源不存在，超过保留期）
    ├── database/
    │   ├── backup.sql         # [OK] 与源一致
    │   └── config.json        # [OK] 与源一致
    └── extra/
        └── unregistered-file.txt  # [UNREG] 未登记文件（不在target范围内）
```

---

### 步骤 1: 初始化配置

```bash
cd examples
backup-checker init -o . -s source -b backup -n "example-backup"
```

**输出：**
```
[OK] Created config file: .\backup-manifest.yaml

Edit the config file to customize targets and settings,
then run 'backup-checker scan' to start verification.
```

**退出码：** `0`

生成的 `backup-manifest.yaml` 内容：
```yaml
manifest:
  name: example-backup
  source_dir: source
  backup_dir: backup
  targets:
  - path: documents/
    description: Important documents
  - path: database/
    description: Database backups
  retention_days: 30
  exclude_patterns:
  - '*.tmp'
  - '*.log'
  - '*.swp'
  - .DS_Store
  hash_algorithm: sha256
```

---

### 步骤 2: 第一次扫描（检测问题）

```bash
backup-checker scan --no-compare-history
```

**输出：**
```
Scanning source: D:\workSpace\AI__SPACE\zyx-00072\examples\source
  Found 6 files in source
Scanning backup: D:\workSpace\AI__SPACE\zyx-00072\examples\backup
  Found 7 files in backup

======================================================================
BACKUP VERIFICATION REPORT
======================================================================
Manifest:       example-backup
Source Dir:     D:\workSpace\AI__SPACE\zyx-00072\examples\source
Backup Dir:     D:\workSpace\AI__SPACE\zyx-00072\examples\backup
Hash Algorithm: sha256
Retention:      30 days

Scanned At:     2026-06-05 15:37:11
Source Files:   6
Backup Files:   7

----------------------------------------------------------------------
SUMMARY
----------------------------------------------------------------------
  [MISS] MISSING             1 files
  [CORR] CORRUPT             1 files
  [EXP] EXPIRED             1 files
  [UNREG] UNREGISTERED        1 files
  [OK] OK                  4 files

  [MISS] Found 4 issue(s) requiring attention

----------------------------------------------------------------------
[MISS] MISSING (1 files)
----------------------------------------------------------------------
  documents/missing-file.txt
    Source: 44dabc628bda423f... (68 B, 2026-06-05 15:12:49)
    Backup: NOT FOUND

----------------------------------------------------------------------
[CORR] CORRUPT (1 files)
----------------------------------------------------------------------
  documents/contract.txt
    Source: d31b5a79ca8c6b52... (65 B, 2026-06-05 15:36:27)
    Backup: 8de0d121a554ed11... (77 B, 2026-06-05 15:36:27)

----------------------------------------------------------------------
[EXP] EXPIRED (1 files)
----------------------------------------------------------------------
  documents/old-project-2020.zip
    Backup: e9a47d915e0951c6... (79 B, 2026-02-25 15:36:27)
    Note:   File in backup but not in source, older than 30 days

----------------------------------------------------------------------
[UNREG] UNREGISTERED (1 files)
----------------------------------------------------------------------
  extra/unregistered-file.txt
    Backup: 6fd3256b3c2d2af3... (60 B, 2026-06-05 15:12:49)
    Note:   File in backup but not covered by any target

======================================================================
[OK] Saved history to: D:\workSpace\AI__SPACE\zyx-00072\examples\.backup-history\scan_20260605_153711.json
```

**退出码：** `5`（存在缺失文件）

---

### 步骤 3: 导出报告

```bash
# JSON 格式
backup-checker report -f json -o report.json

# CSV 格式
backup-checker report -f csv -o report.csv

# 文本格式
backup-checker report -f text -o report.txt
```

**输出：**
```
[OK] JSON report exported to: report.json
[OK] CSV report exported to: report.csv
[OK] Text report exported to: report.txt
```

**退出码：** `0`

---

### 步骤 4: 恢复演练（失败路径 - 缺失文件）

```bash
backup-checker drill
```

**输出：**
```
[MISS] Drill failed: Drill aborted: 1 file(s) missing in backup: documents/missing-file.txt
```

**退出码：** `5`（缺失文件，演练失败）

---

### 步骤 5: 修复缺失文件后再次演练（失败路径 - 校验和不一致）

```bash
# 修复缺失文件
cp source/documents/missing-file.txt backup/documents/missing-file.txt

# 再次演练
backup-checker drill
```

**输出：**
```
[CORR] Drill failed: Drill aborted: 1 file(s) have checksum mismatch: documents/contract.txt
```

**退出码：** `4`（校验和不一致，演练失败）

---

### 步骤 6: 修复损坏文件，第二次扫描（对比历史）

```bash
# 修复损坏文件
cp source/documents/contract.txt backup/documents/contract.txt

# 第二次扫描（自动与上次历史对比）
backup-checker scan
```

**输出：**
```
Scanning source: D:\workSpace\AI__SPACE\zyx-00072\examples\source
  Found 6 files in source
Scanning backup: D:\workSpace\AI__SPACE\zyx-00072\examples\backup
  Found 8 files in backup

======================================================================
BACKUP VERIFICATION REPORT
======================================================================
... (省略相同部分) ...
  [MISS] MISSING             0 files
  [CORR] CORRUPT             0 files
  [EXP] EXPIRED             1 files
  [UNREG] UNREGISTERED        1 files
  [OK] OK                  6 files

  [EXP] Found 2 issue(s) requiring attention
... (省略EXPIRED和UNREGISTERED详情) ...

======================================================================
HISTORY COMPARISON REPORT
======================================================================
Previous Scan: 2026-06-05 15:37:11
Current Scan:  2026-06-05 15:38:21

----------------------------------------------------------------------
SUMMARY COMPARISON
----------------------------------------------------------------------
  [MISS] MISSING             1 ->     0 (-1)
  [CORR] CORRUPT             1 ->     0 (-1)
  [EXP] EXPIRED             1 ->     1 (no change)
  [UNREG] UNREGISTERED        1 ->     1 (no change)
  [OK] OK                  4 ->     6 (+2)

----------------------------------------------------------------------
CHANGES (2)
----------------------------------------------------------------------
  ↑ FIXED        documents/contract.txt
           corrupt -> ok
  ↑ FIXED        documents/missing-file.txt
           missing -> ok

======================================================================
[OK] Saved history to: D:\workSpace\AI__SPACE\zyx-00072\examples\.backup-history\scan_20260605_153821.json
```

**退出码：** `0`（无缺失和损坏文件）

---

### 步骤 7: 恢复演练（成功路径）

```bash
backup-checker drill
```

**输出：**
```
======================================================================
RECOVERY DRILL RESULTS
======================================================================
Started:    2026-06-05 15:38:22
Completed:  2026-06-05 15:38:22
Restore to: C:\Users\admin\AppData\Local\Temp\1\backup_drill_u2adazve

  [OK] Success: 6 files
  [ERR] Failed:  0 files

[OK] All files restored and verified successfully!
======================================================================
(Temporary restore directory has been cleaned up)
```

**退出码：** `0`

---

## 失败路径复现

### 1. 重复目标路径（退出码 3）

创建包含重复 target 的配置文件 `backup-manifest-duplicate.yaml`：
```yaml
manifest:
  name: duplicate-test
  source_dir: source
  backup_dir: backup
  targets:
  - path: documents/
    description: Important documents
  - path: database/
    description: Database backups
  - path: documents/        # 重复！
    description: Duplicate target!
  retention_days: 30
  hash_algorithm: sha256
```

```bash
backup-checker scan -c backup-manifest-duplicate.yaml --no-save
```

**输出：**
```
[ERR] Duplicate target paths found: documents/
  Duplicate paths: documents/
```

**退出码：** `3`

---

### 2. 备份校验和不一致（退出码 4）

参考验收步骤 5，当备份文件与源文件校验和不同时，演练返回退出码 4。

---

### 3. 缺失文件（退出码 5）

参考验收步骤 4，当源文件在备份中不存在时，演练返回退出码 5。

---

## 历史对比

第二次运行 `scan` 后（步骤 6），自动显示与上次历史的对比。也可以手动比较任意两次历史：

```bash
# 查看历史列表
backup-checker history

# 比较第一次和第二次扫描
backup-checker history --compare 0 1

# 使用 -1 表示最新
backup-checker history --compare 0 -1
```

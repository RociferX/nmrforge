# 开发流程

## 环境

- Windows 写码（本仓库）；Linux 测试虚拟机（NMRPipe/SMILE 后端）后续接入。
- 入口：`python main.py`（首次运行自动创建 venv `nmrforge/` 并 `pip install -e .`）。
- 依赖见 pyproject.toml；numpy 限制 <2.5（nmrglue 0.11 的 dtype 别名问题）。

## git 工作流

- 分支 `master`；一次提交对应一个逻辑变更。
- 例行：本地改 → `pytest`（`--basetemp`）→ `ruff check .` → commit →
  （如有 VM）push/pull → 全量回归。

## 测试

- `pytest` 全量；GUI 测试用 `QT_QPA_PLATFORM=offscreen`。
- **全路径回归(强制,0.2.163-补8)**:每次修改代码后必须运行
  `python -m pytest tests/test_full_paths.py`——覆盖自动
  (2D/3D uniform + NUS,含诊断与处理参数优化)、人工(fid.com/谱图脚本)、
  批量(数据组)全部路径;新增/改动处理流程时必须同步更新该文件。
- 不依赖真实 NMRPipe 的测试优先（FakeBackend/MockBackend 模式）。
- Windows 沙箱默认 basetemp 被 ACL 锁死：pytest 必须带
  `--basetemp=<新临时目录>`（如 `$env:TEMP\pytest_nmrforge`）。

## 静态检查

- `ruff check .` 应全仓通过。

## 提交约定

- 消息风格：`feat:` / `fix:` / `refactor:` / `docs:` + 中文简述。
- 文档与代码同步更新（CHANGELOG / PROJECT_STATE / README）。

## Windows 沙箱经验（重要）

- 沙箱辅助进程常报 ACL 错误：普通 shell 命令需提权
  （`sandbox_permissions=require_escalated`）执行。
- apply_patch 修改已有文件可能被 ACL 拒绝；可用模式：
  「apply_patch 新建补丁脚本 → 提权 python 执行 → 删除脚本」。
- 中文不要直接进 exec_command 参数（会乱码）：中文只能放 UTF-8 文件里
  （补丁脚本/文档），脚本内用 `open(..., encoding='utf-8')` 读写。

# 贡献指南 · invest-concierge

感谢你的兴趣！本项目遵循简单透明的协作方式。

## 如何提交 Issue

- **Bug 报告**：请包含 ①复现步骤 ②预期行为与实际行为 ③运行环境（Python 版本 / 系统）。
- **功能建议**：请先看 [ROADMAP](ROADMAP.md) 是否已在规划中。
- **数据报错**：AkShare 等免费接口可能变动，请注明数据源与报错信息（我们已知部分接口在 v1.18.64 后变动，页面已降级处理）。

## 如何提交 PR

1. Fork 仓库，从 `main` 建分支：`git checkout -b feat/your-feature`
2. 确保新增/修改代码**有测试**（`pytest tests/` 保持全绿）
3. 提交信息用中文或英文，说明改动目的（参考现有 commit 风格）
4. 通过 GitHub Actions CI（Python 3.9 / 3.11 双矩阵）
5. 提交 PR 并描述：改了什么 / 为什么 / 测试结果

## 开发约定

- **模块化**：`data/` 数据模块不直接调页面 API；页面不直接调网络接口（走 data 层）。
- **安全**：绝不在代码/文档中放 API Key；涉及文件写入用 `is_safe_write_path` 校验；URL 请求用 `is_safe_public_url` 校验（防 SSRF）。
- **缓存**：数据模块统一走 `data/cache.py` 的 `@cached`；fallback 降级是标配。
- **中文优先**：页面文案/注释中文为主，代码标识符英文。

## 环境

- Python 3.9+（CI 覆盖 3.9 / 3.11）
- `pip install -r requirements.txt`（Streamlit / pandas / numpy / akshare / openai / requests / matplotlib）
- 提前装好 akshare 底层依赖可减少地雷：`pip install -U pyarrow numexpr bottleneck`

---

有问题先搜 [issues](https://github.com/cx-ssg/invest-concierge/issues)，避免重复提问。
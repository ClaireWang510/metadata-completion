# Multi-Agent Metadata Completion (Prototype)

聚焦三类字段：**venue（发表期刊/会议）、authors[i].affiliations（作者机构）、resource_links（相关链接分类）**。

## 目录

```
metadata-completion/
├── run.py                     # 单篇入口
├── batch.py                   # 批量入口（读取一个目录）
├── config.py                  # 模型/阈值/预算
├── blackboard.py              # 共享黑板 + 候选值仲裁
├── cost.py                    # token & 调用记账
├── schema.py                  # Pydantic 数据模型
├── llm.py                     # LLM 客户端封装（记 token）
├── agents/
│   ├── planner.py             # 主智能体：诊断 / 规划 / 仲裁 / 停止
│   ├── web_agent.py           # arXiv + OpenAlex + DBLP + Crossref + 可选 IEEE
│   ├── pdf_agent.py           # PDF 首页图像/文本 + LaTeX front matter
│   ├── link_agent.py          # resource_links 分类
│   └── verifier.py            # 可信度验证
├── tools/
│   ├── arxiv.py
│   ├── openalex.py
│   ├── dblp.py
│   ├── crossref.py
│   ├── ieee.py
│   ├── matching.py            # Unicode 姓名归一化 + 全局一对一匹配
│   └── pdf_utils.py
├── annotation/
│   ├── schema.json            # gold 标注 JSON Schema
│   ├── template.json          # 标注人员用的空模板
│   └── guideline.md           # 中文标注手册
└── data/                      # 放你的 metadata json
```

## 快速开始

```bash
pip install -r requirements.txt

# 复制示例配置，并按需修改其中的值
cp .env.example .env

python run.py --input data/debug/2401.06806 --output_dir out

# 批量运行 debug 数据集
python batch.py --input_dir data/debug --output_dir out --workers 4
```

项目启动时会自动读取仓库根目录的 `.env`。已经由 shell、容器或部署平台设置的同名环境变量优先，`.env` 不会覆盖它们。

LLM 相关环境变量：

- `OPENAI_API_KEY`：API 密钥（必填）。
- `BASE_URL`：OpenAI 兼容接口地址；使用 OpenAI 官方接口时可留空。
- `PLANNER_MODEL`、`VERIFIER_MODEL`、`LINK_MODEL`、`VLM_MODEL`：各 Agent 使用的模型，默认均为 `gpt-4o-mini`。
- `LINK_BATCH_SIZE`、`VERIFIER_BATCH_SIZE`：链接分类与核验的单次请求上限，默认分别为 8 和 12；链接很多时可进一步调小。

其他可配置项见 `.env.example`，包括置信度、迭代/token 预算、PDF Agent 开关及 OpenAlex 邮箱。
如有 IEEE Xplore Metadata API 应用密钥，可设置 `IEEE_API_KEY`；没有密钥时系统仍使用
Crossref/OpenAlex 完成 DOI、venue 和可用的作者机构补全。

## 证据与选择性输出

系统会对已有 venue 做完整性检查；字符串 venue 或缺少 status/year/source/evidence 的对象仍会触发检索。
最终 venue 和机构不是“仅新增值”，而是包含 `source`、`evidence`、`evidence_url`、`confidence`
及 `decision`（`accepted` / `conflicted` / `abstained`）的最终字段级结论。`arxiv_doi`
与正式出版 DOI 分开保存，仓储 DOI 不会进入 venue DOI。

机构统一输出为 `{name, raw_name}`，不要求人工标注或自动推断外部机构标识符。

## 输出

每篇论文写入独立目录，并保留所有历史运行：

```text
out/<arxiv_id>/
├── latest.completed.json
├── latest.trace.json
├── latest.cost.json
└── runs/<UTC-run-id>/
    ├── completed.json
    ├── trace.json
    └── cost.json
```

`latest.*` 便于直接读取最近一次结果，`runs/` 用于比较多次 prompt / 模型调用。

## 测试与评测

```bash
python -m unittest discover -s tests -v
python evaluation.py --gold_dir gold --output_dir out
```

评测报告包含 venue/DOI/status 准确率、机构 F1、覆盖率、选择性风险和 Brier score。
标注界面位于 `data/annotation_app.py`，从仓库根目录运行 `python data/annotation_app.py`。

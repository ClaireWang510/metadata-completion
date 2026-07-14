# 元数据补全 · 人工标注手册（v0.1）

本次只标 **三类字段**：`venue_gold` / `authors_gold` / `resource_links_gold`。
每篇论文产出一份 JSON，路径为 `gold/<arxiv_id>.json`，字段必须符合 `annotation/schema.json`。
建议单篇 8–12 分钟；超过 20 分钟先跳过，标 `difficulty=hard`，最后集中讨论。

---

## 通用原则

1. **以 PDF 首页为准**（作者/机构），**以 DBLP / 出版商官网为准**（venue）。
2. **不要改原始 metadata**，只填 gold JSON。原始 metadata 只作为参考。
3. 每个字段只要能溯源到一个可信证据即可，不需要穷举所有来源。
4. 遇到"不确定"永远填 `unknown` / 留空 / 写进 `notes`；**不要猜**。

---

## 1. `venue_gold`

### 判定顺序
1. arXiv 摘要页顶部的 "Journal-ref" 或 "Comments" 字段（`https://arxiv.org/abs/<id>`）。
2. DBLP：`https://dblp.org/search?q=<title>`。
3. Google Scholar 顶部的 "cited by" 页脚（往往写明会议）。
4. 出版商官网（IEEE Xplore、ACL Anthology、OpenReview 等）。
5. PDF 首页页眉 / 脚注（"To appear in ..." / 版式徽标）。

### `status` 取值
| 值 | 含义 |
|---|---|
| `published` | 已在会议 proceedings / 期刊正式收录 |
| `accepted` | 官方公告或作者主页写明已接收，但尚未开会/上线 |
| `preprint` | 仅在 arXiv，无任何投稿状态证据 |
| `unknown` | 花 5 分钟仍无法判定 |

### `name` 规范
- 会议：`简称 年份`，如 `ICCV 2024`、`NeurIPS 2024`、`ACL 2024 Findings`。
- Workshop：`主会 年份 Workshops`，如 `CVPR 2024 Workshops`。
- 期刊：使用 DBLP/publisher 的官方缩写或全称，如 `IEEE TPAMI`、`ACM TOG`。
- Findings、Demo、Industry Track 需明确注明。
- 不区分 track 的会议不加后缀。

### `type` 取值
`conference / journal / workshop / preprint / unknown`。
Findings/Demo 一律归 `conference`；workshop 归 `workshop`。

### 陷阱
- OpenAlex 常把 workshop 归到主会，需要在 DBLP 上二次确认。
- 有些论文同时投了 workshop 和主会 —— 以 DBLP 为准。
- 若同一篇有多版本（v1 workshop、v2 期刊），以最新已发表版本为 gold。

---

## 2. `authors_gold`

### 判定顺序
1. **PDF 首页**（脚注 / 上标数字对齐机构）——**最高优先级**。
2. OpenAlex `authorships.institutions`。
3. 作者主页（个人主页 / Google Scholar）。

### 机构名规范
- 用 **英文正式名**，去掉 `Dept. of`, `School of`, `Lab`, 分校地址等。
  - ✓ `Tsinghua University`
  - ✗ `Dept. of CS, Tsinghua University, Beijing, China`
- 分校保留：`University of California, Berkeley` 保留 `Berkeley`。
- 企业研究院用官方英文名：`Google DeepMind`、`Microsoft Research Asia`。
- 机构只标注规范英文名；如需保留论文首页原文，可使用 `raw_name`。不要求标注外部机构标识符。

### 多机构
- 按 PDF 首页顺序完整列出，不要合并。
- 相同机构不同分部（如 `Google Research` vs `Google DeepMind`）保留 PDF 上写的那个。

### `evidence` 字段
优先级 `pdf_first_page > openalex > dblp > author_homepage > other`。
只需填出你**实际使用**的那一个来源。

### 陷阱
- 有些论文首页机构以数字上标编号 —— 严格对齐，不要按名字顺序猜。
- 通讯作者 * / † 的脚注不是机构，忽略。
- 已离职情况：以 PDF 首页当时写的机构为准，不追认新雇主。
- 若 PDF 首页也没写机构（个别 workshop 短文），填空数组并 `evidence=other`，`notes=首页未列机构`。

### `authors_list_fix`
只在**原 metadata 的作者列表本身错了**（漏、重、拆、并、错拼）时填英文全名列表，否则**留 null**。

---

## 3. `resource_links_gold`

### 6 类标签

| 标签 | 定义 | 典型信号词 |
|---|---|---|
| `official_code` | 本文作者发布的代码仓库 | "Code available at", "Our code:", 仓库 owner 与作者一致 |
| `official_dataset` | 本文作者发布的**新**数据集 | "We release", "Our dataset", 数据集名与论文标题挂钩 |
| `official_project` | 本文的项目主页 / demo / 论文站点 | 域名含 `-project.github.io`、"Project page" |
| `cited_external` | 引用的外部资源（baseline、被引数据集、比赛主页、其他人工作） | "based on", "we compare with", "using the ... dataset from" |
| `template_boilerplate` | 来自 LaTeX 模板的样板 URL | `MCG-NKU/CVPR_Template`, `acl-org/acl-style-files` 等 |
| `other` | 断链、作者主页、机构页、无法分类 | — |

### 判定顺序
1. 找到 URL 在正文/LaTeX 中的**锚点句**。锚点句为空说明是模板痕迹，多半是 `template_boilerplate`。
2. 检查 GitHub owner 是否为作者之一（或作者所属机构）——判 `official_code` 需要至少一条这样的证据。
3. 数据集必须**新提出**才算 `official_dataset`；仅使用现成数据集属于 `cited_external`。
4. 转义污染的 URL（含 `\_`）如果**去转义后**能命中已有官方链接，仍标为对应的 `official_*`，并在 `notes` 里写 "escaped_duplicate"。

### `added`：补漏
在 PDF/LaTeX 中扫读一遍，只补 `official_code / official_dataset / official_project`，并给出证据引用（≤ 30 词）。
不需要补 `cited_external`。

---

## 4. 质控

- **双盲双标**：每篇由 2 位标注员独立标，再由第三人（组长）仲裁差异。
- **一致性指标**：venue 字段用 Cohen's κ；affiliations 用 F1；links 用 Macro-F1。目标 κ ≥ 0.7 / F1 ≥ 0.8。
- 每 50 篇做一次校对例会，把分歧写进本手册的 FAQ。
- Pilot：前 20 篇由所有标注员共同标注，作为对齐样本。

---

## 5. 交付物

```
gold/
  2401.00663.json
  2401.00714.json
  ...
gold/_stats.csv          # id, difficulty, minutes_spent, confidence
```

标注文件需 `python -c "import json,jsonschema; jsonschema.validate(json.load(open('gold/xxx.json')), json.load(open('annotation/schema.json')))"` 通过。

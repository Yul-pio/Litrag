# 内容数据模型

## 目标

把“采集、审核、发布”拆开，避免爬虫直接污染首页。

```text
官方来源 / arXiv
      ↓
候选人（未来写入 data/candidates/）
      ↓
人工核验
      ↓
结构化数据
      ↓
信息流 / 学校页 / 工作台
```

## 数据文件

### `data/schools.json`

学校主表和官方招生入口。

关键字段：

```text
id
name
short_name
city
aliases
admissions_url
homepage
focus_programs
note
```

### `data/sources.json`

采集源注册表。记录每个学校从哪里采集、多久更新一次。

关键字段：

```text
id
school_id
name
url
source_type
content_types
update_frequency
enabled
notes
```

### `data/programs.json`

专业和研究方向的稳定结构。一个方向对应一条记录。

关键字段：

```text
id
school_id
college
year
major_code
major_name
degree_type
study_mode
direction_code
direction_name
plan
recommendation_quota
program_total_plan
program_total_recommendation
exam_subjects
notes_label
notes
verified_at
```

### `data/admission_records.json`

一份官方招生文档或页面的主记录，通过 `program_ids` 关联专业方向。

关键字段：

```text
id
school_id
college
year
document_type
title
source_id
source_url
source_label
source_kind
page_location
published_at
verified_at
status
program_ids
summary
why
undergrad
tags
audiences
featured
signal
read_minutes
feed_date
feed_time
```

### `data/radar_items.json`

只保存人工精选的非院校内容：论文导读、经验、工具、观点。

首页接口 `/api/radar` 会把：

```text
radar_items.json
+
admission_records.json
+
programs.json
+
sources.json
+
schools.json
```

合并成前端信息流。

## 发布规则

- `draft`：采集或人工录入，不进入首页。
- `verified`：来源、日期、专业和关键数字已核验，可发布。
- `rejected`：来源失效或不适用。

高利害字段必须先核验：

- 招生年份；
- 学院与专业；
- 招生计划；
- 推免人数；
- 考试科目；
- 发布时间；
- 官方链接。

## 自动采集的未来流程

```text
collector
  → data/candidates/raw_*.json
  → 规则校验
  → 人工审核
  → admission_records.json / programs.json
  → 重新运行校验
  → 发布
```

采集器不得直接修改 `radar_items.json`。

## 校验命令

```powershell
python scripts/validate_content.py
```

校验内容包括：

- ID 是否重复；
- school_id 是否存在；
- source_id 是否存在；
- program_id 是否存在；
- 官方链接是否存在；
- 状态是否合法；
- 考试科目是否为结构化数组。

## 候选采集格式

采集器只写入 `data/candidates/`，候选文件不会自动进入正式数据。

候选至少包含：

```text
candidate_id
candidate_type
status=candidate
school_id
source_id
year
document_type
college
title
source_url
source_label
source_kind
page_location
fetched_at
needs_review
raw
```

审核通过后，从候选生成：

```text
programs.json
admission_records.json
```

并运行：

```powershell
python scripts/validate_content.py
```

## GitHub Actions

每日采集工作流只生成候选 artifact，不自动提交或发布。

```text
.github/workflows/collect-pku.yml
        ↓
python scripts/collect_candidates.py --school pku --year 2027
        ↓
data/candidates/pku_2027_catalog.json
        ↓
人工审核
```

## 审核转换工具

从候选生成审核单：

```powershell
python scripts/promote_candidate.py draft `
  --candidate-file data/candidates/pku_2027_catalog.json `
  --candidate-id pku-2027-catalog-4251f989
```

审核单会生成在候选文件旁边，状态为 `draft`。

人工补齐以下字段后才能发布：

- 专业代码、专业名称；
- 学位类型、学习方式；
- 方向代码、方向名称；
- 计划数、推免数；
- 初试科目；
- 摘要、推荐理由、本科生怎么用；
- 核验时间。

应用审核单：

```powershell
python scripts/promote_candidate.py apply `
  --candidate-file data/candidates/pku_2027_catalog.json `
  --review data/candidates/pku-2027-catalog-4251f989.review.json
```

确认覆盖已有同 ID 记录时才使用：

```powershell
python scripts/promote_candidate.py apply ... --update
```

随后运行：

```powershell
python scripts/validate_content.py
```


# 传统工具兜底解析（pdfplumber / pypdf）

仅当 MinerU 与视觉模型都不可用，或当前任务明确要求使用本地代码做轻量提取时，才使用以下传统工具。这不是默认路径。

若 `extract_text()` 返回空字符串、乱码（大量 `(cid:X)` 或无意义字符）或结构严重残缺，说明最终兜底方案也无法可靠完成任务，应明确告知用户限制，而不是继续把它当作默认路径。

## 目录

- [依赖](#依赖)
- [1. 文本提取](#1-文本提取)
- [2. 表格提取](#2-表格提取)
- [3. 元数据提取](#3-元数据提取)

---

## 依赖

按实际兜底任务安装最小依赖：

```bash
# 基础文本和元数据读取
uv pip install pypdf

# 保留排版的文本、区域文本、表格抽取
uv pip install pdfplumber

# 表格导出 Excel
uv pip install pandas openpyxl

# 表格转 Markdown
uv pip install tabulate
```

如果不用 `uv`，可改用 `python3 -m pip install ...`。只安装当前任务需要的包，不必一次性安装全部依赖。

---

## 1. 文本提取

### 保留排版的文本提取（pdfplumber）

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            print(text)
```

### 基础文本提取（pypdf）

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
text = ""
for page in reader.pages:
    text += page.extract_text() or ""
print(text)
```

### 按区域提取文本（pdfplumber）

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    page = pdf.pages[0]
    # 按边界框裁剪：(left, top, right, bottom)
    region_text = page.within_bbox((100, 100, 400, 200)).extract_text()
    print(region_text)
```

---

## 2. 表格提取

### 提取所有表格

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for j, table in enumerate(tables):
            print(f"第 {i+1} 页 · 表格 {j+1}：")
            for row in table:
                print(row)
```

### 提取表格并导出为 Excel

```python
import pdfplumber
import pandas as pd

with pdfplumber.open("document.pdf") as pdf:
    all_tables = []
    for page in pdf.pages:
        for table in page.extract_tables():
            if table:
                df = pd.DataFrame(table[1:], columns=table[0])
                all_tables.append(df)

if all_tables:
    combined = pd.concat(all_tables, ignore_index=True)
    combined.to_excel("tables.xlsx", index=False)
```

### 提取表格转为 Markdown（推荐用于自身理解）

```python
import pdfplumber
from tabulate import tabulate  # 依赖：pip install tabulate

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        for table in page.extract_tables():
            if table:
                print(tabulate(table, headers="firstrow", tablefmt="pipe"))
```

### 复杂表格（自定义策略）

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    page = pdf.pages[0]
    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "intersection_tolerance": 15,
    }
    tables = page.extract_tables(table_settings)
```

---

## 3. 元数据提取

若用户只需要 PDF 元数据，可直接使用 `pypdf` 轻量读取；这属于工具性读取，不代表整体解析主流程应回退到传统工具。

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
meta = reader.metadata
print(f"标题：{meta.title}")
print(f"作者：{meta.author}")
print(f"主题：{meta.subject}")
print(f"创建工具：{meta.creator}")
print(f"总页数：{len(reader.pages)}")
```

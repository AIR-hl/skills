---
name: pdf-parser
description: 当用户需要解析 PDF 文件时使用此技能，包括提取文本、提取表格、读取元数据、对扫描件进行 OCR。默认优先使用 MinerU API；当 MinerU 不可用，或仅需要对少量页面内容进行快速比对校验时，再使用 Agent 可直接调用的视觉模型；仅当上述两者都无法使用时，才使用传统工具（pdfplumber、pypdf）。
license: Proprietary. LICENSE.txt has complete terms
---
# PDF 解析指南

## 解析流程

**默认先使用 MinerU 解析 PDF，仅在以下情况下再降级或切换：**

1. **优先：MinerU 解析**
   - 默认走 MinerU，获取更稳定的 Markdown / OCR / 表格 / 公式产物。
   - 适合正文提取、表格提取、扫描件 OCR、公式识别、按页分段处理等大多数正式解析场景。
   - 当用户明确要求“解析 PDF 内容”而不是“快速看一眼某一小块页面”时，优先用 MinerU，而不是视觉模型。

2. **第二层：视觉模型校验 / 补充理解**
   - 当 MinerU 不可用时使用。
   - 当只需要对少量页面、局部区域、单张表格、脚注、图注做快速比对校验时使用。
   - 当需要解释页面视觉布局、图文关系，或对 MinerU 结果做少量人工核对时使用。

3. **最后兜底：传统工具解析**
   - 只有当 MinerU 和视觉模型都不可用，或网络 / API 条件不满足时，才使用 `pdfplumber` / `pypdf`。
   - 传统工具更适合提取纯文本、简单表格和元数据，不应作为默认首选。

> 出于成本控制，不要在 MinerU 可用时默认走视觉模型通读全文。视觉能力主要用于少量校验、局部解释和 MinerU 不可用时的补充。

---

## 一、MinerU 解析（默认首选）

MinerU Agent 轻量解析 API 无需 Token，直接可用，输出 Markdown 格式。

### 1.1 适用场景

- 通篇提取正文
- 提取表格并保留较稳定结构
- 扫描件 OCR
- 含数学公式的文档
- 需要分页、分段、批量处理的 PDF
- 需要导出 Markdown 产物继续处理

### 1.2 注意事项

- 单次任务最多解析 20 页；若文档超出 20 页，请务必使用 `page_range` 参数分段提交，例如 `"1-20"`、`"21-40"`
- 每 IP 每分钟有请求数上限，超出返回 HTTP 429
- 本地文件上传是“签名上传”流程：先调用 `/parse/file` 获取 `file_url`，再对该 URL 执行原样 `PUT`
- 若只需要核对很少量的页面内容，不必默认再走一遍全文视觉解析；优先把视觉能力作为局部校验工具

---

### 1.3 解析在线 PDF（URL 模式）

适用于文件有公网可访问 URL 的场景。

```python
import requests, time

BASE_URL = "https://mineru.net/api/v1/agent"

def parse_pdf_url(url, language="ch", page_range=None,
                  enable_table=True, is_ocr=False, enable_formula=True):
    """
    通过 URL 提交 MinerU 解析任务，返回 Markdown 字符串。

    参数：
        url            - PDF 的公网可访问地址
        language       - 文档语言，默认 "ch"（中文），英文用 "en"，见语言代码表
        page_range     - 解析范围，如 "1-10"，不填则解析全文
        enable_table   - 是否识别表格，默认 True
        is_ocr         - 是否强制 OCR，扫描件设为 True
        enable_formula - 是否识别数学公式（输出 LaTeX），默认 True
    """
    data = {
        "url": url,
        "language": language,
        "enable_table": enable_table,
        "is_ocr": is_ocr,
        "enable_formula": enable_formula,
    }
    if page_range:
        data["page_range"] = page_range

    resp = requests.post(f"{BASE_URL}/parse/url", json=data)
    result = resp.json()
    # 注意：如果遇到 HTTP 429 限流报错，请在此处捕获或处理，sleep 等待后重试
    if result["code"] != 0:
        raise RuntimeError(f"提交任务失败：{result['msg']}")

    task_id = result["data"]["task_id"]
    return _poll(task_id)


def _poll(task_id, timeout=300, interval=5):
    labels = {"uploading": "下载中", "pending": "排队中",
              "running": "解析中", "waiting-file": "等待上传"}
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{BASE_URL}/parse/{task_id}")
        data = resp.json()["data"]
        state, elapsed = data["state"], int(time.time() - start)
        if state == "done":
            md = requests.get(data["markdown_url"]).text
            print(f"[{elapsed}s] 解析完成")
            return md
        if state == "failed":
            raise RuntimeError(f"[{elapsed}s] 解析失败：{data.get('err_msg')}")
        print(f"[{elapsed}s] {labels.get(state, state)}...")
        time.sleep(interval)
    raise TimeoutError(f"超时（{timeout}s），task_id：{task_id}")


# 示例：20 页以内
markdown = parse_pdf_url("https://example.com/paper.pdf", language="en")
print(markdown)

# 示例：超过 20 页时分段
part1 = parse_pdf_url("https://example.com/paper.pdf", language="en", page_range="1-20")
part2 = parse_pdf_url("https://example.com/paper.pdf", language="en", page_range="21-40")
```

---

### 1.4 解析本地 PDF（文件上传模式）

适用于文件在本地、无公网 URL 的场景。

```python
import requests, time, os

BASE_URL = "https://mineru.net/api/v1/agent"

def parse_pdf_file(file_path, language="ch", page_range=None,
                   enable_table=True, is_ocr=False, enable_formula=True):
    """上传本地 PDF 到 MinerU，返回 Markdown 字符串。"""
    data = {
        "file_name": os.path.basename(file_path),
        "language": language,
        "enable_table": enable_table,
        "is_ocr": is_ocr,
        "enable_formula": enable_formula,
    }
    if page_range:
        data["page_range"] = page_range

    # 第一步：获取签名上传 URL
    resp = requests.post(f"{BASE_URL}/parse/file", json=data)
    result = resp.json()
    # 注意：如果遇到 HTTP 429 限流报错，请在此处捕获或处理，sleep 等待后重试
    if result["code"] != 0:
        raise RuntimeError(f"获取上传链接失败：{result['msg']}")

    task_id = result["data"]["task_id"]
    file_url = result["data"]["file_url"]

    # 第二步：PUT 上传文件
    # 注意：这里使用签名上传 URL，按原样 PUT 文件即可；
    # 不要额外手工设置 Content-Type 或其他请求头，以免与签名不一致。
    with open(file_path, "rb") as f:
        put_resp = requests.put(file_url, data=f)
    if put_resp.status_code not in (200, 201):
        raise RuntimeError(f"文件上传失败，HTTP {put_resp.status_code}")
    print(f"文件上传成功，task_id：{task_id}")

    # 第三步：轮询结果（复用上方的 _poll 函数）
    return _poll(task_id)


# 示例：扫描件
markdown = parse_pdf_file("./scanned_report.pdf", is_ocr=True, page_range="1-20")
print(markdown)

# 示例：普通论文超过 20 页时分段
part1 = parse_pdf_file("./paper.pdf", language="en", page_range="1-20")
part2 = parse_pdf_file("./paper.pdf", language="en", page_range="21-40")
```

---

### 1.5 MinerU 结果下载与网络排查

MinerU 任务成功后，会在轮询结果中返回 `markdown_url`。这一步并不代表文件已经下载到本地，你还需要显式请求该 URL。

```python
md = requests.get(data["markdown_url"], timeout=120).text
with open("output.md", "w", encoding="utf-8") as f:
    f.write(md)
```

若轮询结果已经是 `state == "done"`，但下载 `markdown_url` 时遇到 SSL/TLS 报错（例如 `SSLEOFError`、`UNEXPECTED_EOF_WHILE_READING`），可先提示用户关闭代理并重试，而不是误判为 MinerU 解析失败。

---

### 1.6 Markdown 中的图片处理

MinerU 的 `Agent 轻量解析 API` 仅返回 Markdown CDN 链接，图片使用 `<!-- image-->` 占位。若只需要正文理解、摘要、问答，轻量 API 通常足够。

若需要更完整的解析结果（包括图片、多模态结构化内容、压缩包产物等），应改用 `精准解析 API`，**此时必须停下来，主动向用户询问 MinerU Token。** 精准解析 API 官方文档： https://mineru.net/apiManage/docs

不要跳过这一步，也不要在未拿到 Token 时继续承诺可输出带图结果。

---

## 二、视觉模型解析（MinerU 不可用或少量校验时）

### 2.1 推荐做法

- 当 MinerU 不可用时，直接使用 `Read` 工具读取 PDF 文件或页面截图，让 Agent 基于视觉能力理解文档内容。
- 当只关心某几页或某个局部区域时，可先读取对应页面截图或裁剪图，再让 Agent 聚焦解析。
- 当目标是对 MinerU 结果做少量复核、解释图表、确认脚注/图注/表头内容时，优先使用视觉模型，而不是重新走全文解析。

### 2.2 适用场景

- MinerU 当前不可用，需要临时替代
- 快速核对某页讲了什么、某段文字是什么意思
- 核对某张表、某条脚注、某个图注、某个局部区域
- 理解图文混排、版式关系、多栏布局
- 对 MinerU 输出做少量人工交叉验证

### 2.3 何时不适合优先用视觉模型

- 需要通篇稳定导出 Markdown
- 需要批量分页、分段处理长文档
- 需要更强的 OCR、表格识别或公式识别
- 需要把结果继续程序化处理

---

## 三、传统工具解析（最终兜底）

仅当 MinerU 和视觉模型都不可用，或当前任务明确要求使用本地代码做轻量提取时，才使用以下传统工具。

### 3.1 文本提取

#### 保留排版的文本提取（pdfplumber）

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            print(text)
```

#### 基础文本提取（pypdf）

```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
text = ""
for page in reader.pages:
    text += page.extract_text() or ""
print(text)
```

#### 按区域提取文本（pdfplumber）

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    page = pdf.pages[0]
    # 按边界框裁剪：(left, top, right, bottom)
    region_text = page.within_bbox((100, 100, 400, 200)).extract_text()
    print(region_text)
```

**传统工具的局限：** 若 `extract_text()` 返回空字符串、乱码（大量 `(cid:X)` 或无意义字符）或结构严重残缺，说明最终兜底方案也无法可靠完成任务，应明确告知用户限制，而不是继续把它当作默认路径。

---

### 3.2 表格提取

#### 提取所有表格

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

#### 提取表格并导出为 Excel

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

#### 提取表格转为 Markdown（推荐用于自身理解）

```python
import pdfplumber
from tabulate import tabulate # 依赖：pip install tabulate

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        for table in page.extract_tables():
            if table:
                # 使用 tabulate 将二维数组转为 Markdown 表格
                print(tabulate(table, headers="firstrow", tablefmt="pipe"))
```

#### 复杂表格（自定义策略）

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

### 3.3 元数据提取

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

---

### 语言代码参考（`language` 字段）

| 代码       | 语言             | 代码                     | 语言                       |
| ---------- | ---------------- | ------------------------ | -------------------------- |
| `ch`     | 中文简体（默认） | `chinese_cht`          | 中文繁体                   |
| `en`     | 英文             | `latin`                | 拉丁语系（西欧各语言）     |
| `japan`  | 日文             | `arabic`               | 阿拉伯语                   |
| `korean` | 韩文             | `cyrillic`             | 西里尔字母语系（俄语等）   |
| `el`     | 希腊语           | `devanagari`           | 天城文（印地语等）         |
| `th`     | 泰语             | `ta` / `te` / `ka` | 泰米尔 / 泰卢固 / 格鲁吉亚 |

---

## 快速参考

| 场景                                 | 默认路径             | 第二路径                                      | 最后兜底                         |
| ------------------------------------ | -------------------- | --------------------------------------------- | -------------------------------- |
| 普通文本提取 / 全文解析              | MinerU               | 视觉模型（MinerU 不可用时）                   | `pdfplumber` / `pypdf`         |
| 提取表格 / 理解版面                  | MinerU `enable_table=True` | 视觉模型做局部校验                        | `pdfplumber.extract_tables()`  |
| 扫描件 OCR                           | MinerU `is_ocr=True` | 视觉模型仅做少量可见内容核对                  | 传统工具通常效果有限            |
| 含数学公式                           | MinerU `enable_formula=True` | 视觉模型做少量解释或校验                 | 传统工具通常无法可靠还原        |
| 核对某页脚注 / 图注 / 局部区域       | MinerU 先出主结果     | 视觉模型做快速交叉验证                        | 按区域提取文本作为最终兜底      |
| 只看某一小块页面内容                 | 视觉模型可直接查看    | MinerU（若需要结构化结果）                    | 按区域提取文本                  |
| 读取元数据                           | 轻量读取即可         | —                                             | `pypdf reader.metadata`        |
| PDF 超过 20 页                       | MinerU 分段提交多个 `page_range` | 视觉模型只用于抽样核对                 | 传统工具按页遍历                |

---
name: pdf-parser
description: 解析各种 PDF 文件，提取正文、表格、公式与元数据，对扫描件做 OCR，并支持局部视觉校验。当用户上传 PDF、提供 PDF URL，或要求解析、阅读、提取、总结、OCR、查看 PDF 内容（如论文、报告、扫描件、表单）时使用。默认使用 MinerU API 输出 Markdown；局部校验或 MinerU 不可用时使用视觉能力；最后回退到 pdfplumber/pypdf。
---
# PDF 解析指南

## 方式选择

按下表选择路径；MinerU 可用时不要默认用视觉能力通读全文，视觉能力主要用于少量校验、局部解释和 MinerU 不可用时的补充。

| 场景 | 默认路径 | 第二路径 | 最后兜底 |
| --- | --- | --- | --- |
| 普通文本提取 / 全文解析 | MinerU | 视觉能力（MinerU 不可用时） | `pdfplumber` / `pypdf` |
| 提取表格 / 理解版面 | MinerU `--table` | 视觉能力做局部校验 | `pdfplumber.extract_tables()` |
| 扫描件 OCR | MinerU `--ocr` | 视觉能力仅做少量可见内容核对 | 传统工具通常效果有限 |
| 含数学公式 | MinerU `--formula` | 视觉能力做少量解释或校验 | 传统工具通常无法可靠还原 |
| 单独核对 1-3 页或局部区域 | 视觉能力，按 §2.1 渲染 PNG | MinerU（若需结构化结果） | 按区域提取文本 |
| 全文解析后的抽样复核 | MinerU 主结果 | 视觉能力按 §2.1 校验具体页 | 按区域提取文本 |
| 读取元数据 | `pypdf reader.metadata` | - | - |
| 本地 PDF 超过 20 页 | `scripts/mineru_parse.py` 自动分段 | 视觉能力只用于抽样核对 | 传统工具按页遍历 |
| 本地 PDF 超过 10MB | 提示轻量 API 文件大小限制；若需完整结构化解析，改用 MinerU 精准解析 API 并询问 Token | 视觉能力只用于局部核对 | 传统工具按页遍历 |

只读元数据时直接走 `pypdf`；除此之外，传统工具只在 MinerU 与视觉能力都不可用，或用户明确要求本地轻量提取时使用，详见 [references/fallback.md](references/fallback.md)。

---

## 一、MinerU 解析（默认首选）

MinerU Agent 轻量解析 API 无需 Token，输出 Markdown。使用 bundled 脚本作为默认入口，避免每次重写上传、轮询、下载和分段逻辑。

### 1.1 快速入口

```bash
python scripts/mineru_parse.py <input.pdf-or-url> --out output.md
```

默认启用表格与公式识别；扫描件需要加 `--ocr`，需要指定语言或页码范围时再加 `--language` / `--pages`。

常用参数：

- `<input.pdf-or-url>`：本地 PDF 路径或 `http(s)` PDF URL。
- `--out`：Markdown 输出路径；默认 `output.md`。
- `--language`：文档语言代码，默认 `ch`（中英文）；若有其他语言需求完整代码见 [references/languages.md](references/languages.md)。
- `--pages`：解析页码范围，如 `1-20`。多个范围可用逗号分隔，如 `1-20,21-40`。
- `--ocr`：强制 OCR，扫描件使用。
- `--table` / `--no-table`：启用或关闭表格识别；默认启用。
- `--formula` / `--no-formula`：启用或关闭公式识别；默认启用。

依赖：脚本需要 Python 3 与 `requests`。本地 PDF 自动分段依赖 `pypdf` 读取页数；若未安装 `pypdf`，脚本仍可提交文件，但无法预先判断是否超过 20 页。

### 1.2 默认行为

- 本地 PDF 若能读取页数且超过 20 页，脚本会自动按 `1-20`、`21-40` 等范围分段提交并合并 Markdown。
- URL 模式若未指定 `--pages`，脚本会先直接提交；若 MinerU 返回页数限制错误，应重新用 `--pages` 指定范围。
- MinerU 轻量 API 每个任务最多 20 页、文件最大 10MB；脚本按页分段不能绕过文件大小限制。
- 若轻量 API 因文件大小失败，需完整结构化解析时改用 MinerU 精准解析 API 并询问 Token；否则转为局部视觉核对或传统工具兜底。
- HTTP 429、临时服务错误会有限重试；持续失败时说明限流或服务不可用。
- 本地文件上传使用签名 URL 原样 `PUT`，不要额外设置 `Content-Type`。

### 1.3 结果与异常处理

- 成功后脚本会把 `markdown_url` 内容下载到 `--out`。
- 如果轮询已完成但 Markdown 下载出现 SSL/TLS 错误，先提示用户检查代理设置后重试，不要误判为 MinerU 解析失败。
- MinerU 轻量 API 中图片通常表现为 Markdown CDN 链接或 `<!-- image-->` 占位。只需正文理解、摘要、问答时，轻量 API 通常足够。
- 若需要完整图片、多模态结构化内容或压缩包产物，应改用 MinerU 精准解析 API；此时必须停下来向用户询问 MinerU Token。文档： https://mineru.net/apiManage/docs

---

## 二、视觉解析（局部校验 / MinerU 不可用兜底）

视觉能力不是默认全文解析路径，主要用于：

- 单独查看 1-3 页或某个局部区域；
- 对 MinerU 输出做少量交叉核对；
- 解释图文关系、多栏排版、表格外观或公式版面；
- MinerU 不可用、被限流，或对当前 PDF 失败时的临时兜底。

### 2.1 视觉质量要求（务必先读）

- 仅回答图像里能直接看到的内容；看不清就说明分辨率不足，需要更高 DPI 或更大裁剪。
- 表格、公式、图注按视觉布局复述，不要补全模型以为应该存在的内容。
- 多栏布局先判断阅读顺序再总结，避免把左右栏文字混在一起。
- 视觉结果与 MinerU 输出冲突时，以当前图像为准，并明确指出冲突点。

### 2.2 工作流（渲染后局部核对）

按顺序执行：

1. 先确定目标页码，尽量限制在 1-3 页。
2. 将目标页渲染为 PNG。默认 200 DPI 足够大多数核对场景；表格密集或字号偏小时提高到 300 DPI：

   ```bash
   mkdir -p tmp/pdf-pages
   pdftoppm -png -r 200 -f <起始页> -l <结束页> \
     "<input.pdf>" tmp/pdf-pages/page
   ```

3. 基于渲染图像回答具体问题；若图像看不清，重新提高 DPI 或裁剪局部后再核对。
4. 处理完毕后清理 `tmp/pdf-pages/`。

### 2.3 渲染工具

优先使用 bundled runtime 或系统 Poppler 中的 `pdftoppm` / `pdfinfo`。如果缺失，只安装当前任务需要的工具。

用于渲染的系统工具:

```bash
# macOS (Homebrew)
brew install poppler

# Ubuntu/Debian
sudo apt-get install -y poppler-utils
```

如果当前环境无法安装，说明缺少哪个依赖，并请用户在本地安装后继续。

### 2.4 临时目录与产物约定

- 中间 PNG 一律放在 `tmp/pdf-pages/`，任务结束后清理。
- 如需把局部解析结果保存给用户，写到 `output/pdf/` 下，文件名保留语义，如 `paper-page-3-table.md`。
- 不要把渲染产物提交进仓库。

---

## 三、传统工具解析（最终兜底）

只读元数据时可直接用 `pypdf`。除此之外，仅当 MinerU 与视觉能力都不可用，或用户明确要求本地轻量提取时，才使用传统工具。

`pdfplumber` 适合保留排版的文本/表格抽取，`pypdf` 适合基础文本与元数据读取。依赖安装与完整代码示例（文本提取、按区域裁剪、表格导出 Excel/Markdown、自定义表格策略、元数据读取）见 [references/fallback.md](references/fallback.md)。

若 `extract_text()` 返回空、乱码或大量 `(cid:X)`，说明兜底也无法可靠完成任务，应直接告知用户限制，而不是继续把它当作默认路径。

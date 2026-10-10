# 字形框选工具

本地运行的书法图版单字框选工具。在浏览器里打开一组图版，绘制、移动、缩放和删除单字框，按中文阅读顺序把释文贴到框上，保存为 `boxes.json`，再用命令行裁出单字图。

本仓库**只包含合成的示例图**，没有博物馆藏品，也没有受版权保护的书法图像。示例图是用 Pillow 画在纸色背景上的几个大字。请只使用你自己有权处理的图版。

## 环境

- Python 3.9 或更高版本
- 标注服务只用标准库
- 裁切单字、以及重新生成示例图，需要 [Pillow](https://python-pillow.org/)

```bash
python -m pip install -r requirements.txt
```

系统里如果没有 `python` 命令，把下面的 `python` 换成 `python3`。

### 安装成 `cba` 命令

用 [uv](https://docs.astral.sh/uv/) 可以装成命令 `cba`，装好后不必留在仓库目录里。安装包带上网页（`web/`）、`tools/` 和合成示例图。写出的 `boxes.json`、裁切图、审阅图和对照表都在当前目录，或在 `--data-dir` 指定的目录，不会写进安装包。

```bash
# 在本仓库里安装
uv tool install .

# 从 GitHub 安装。要装某一分支，在地址后加 @分支名
uv tool install git+https://github.com/AsahiArt/calligraphy-box-annotator

# 不长期安装，直接跑一次
uvx --from git+https://github.com/AsahiArt/calligraphy-box-annotator cba --help
```

```bash
cba --help
cba status --json
cba validate --json
cba serve --data-dir ./data --port 8765
```

`python -m cba` 仍然可用：在仓库根目录用源码运行，或在已经装了这个包的 Python 里运行。`cba serve` 和 `python serve.py` 打开的是同一个标注页面。没有自己的图版时，`cba serve` 只读包里的示例图，并把 `boxes.json` 写到数据目录。

## 一分钟试用

克隆后不用改配置。没有 `config.json` 时，程序会读取仓库里的 `config.example.json`，直接打开示例图版。

```bash
git clone https://github.com/chuxubank/calligraphy-box-annotator.git
cd calligraphy-box-annotator
python -m pip install -r requirements.txt
python serve.py
```

浏览器打开 <http://127.0.0.1:8765/>。第一次启动会用 `examples/demo/boxes.example.json` 生成 `boxes.json`（该文件已被 git 忽略）。页面上能看到示例框：右列第二字是灰框「重文」，左列第一字是蓝框「空白」。标签模式是「固定」，拖动框不会把后面的字全部错位。要按释文重贴，点「重贴标签」；把模式下拉改成「跟随起点」则恢复自动贴标签。

在字上再拖一个矩形，按 `S` 保存，然后：

```bash
python crop_glyphs.py
```

单字 PNG 写到 `cropped/`。文件名是 `{图版}_{框号}_{字}.png`，例如 `plate-01_0001_甲.png`。框号是界面上的 `#id`，四位补零。没有字标签时省略最后一段。重文点会写成 `plate-01_0002_乙_repeat.png`。标了不制卡的框默认不裁；需要时加 `--include-nocard`。

`plate-01` 的阅读顺序是右列 `甲乙丙丁`、左列 `戊己庚辛`。打开 `plate-02` 后，把「释文起点」改成 `8` 再点「重贴标签」，标签会接上 `子丑寅卯辰巳午未`。

要用自己的图，复制示例配置再改路径。`config.json` 已被 git 忽略：

```bash
cp config.example.json config.json
```

## 配置

`config.json`（可选）和 `config.example.json` 使用同一组字段。配置文件里的相对路径，相对于该配置文件所在目录。命令行参数和环境变量里的相对路径，相对于 `--data-dir`（或环境变量 `CBA_DATA_DIR`）。两者都没给时，这个目录就是当前工作目录。

| 字段 | 含义 | 示例默认值 |
| --- | --- | --- |
| `host` | 监听地址 | `127.0.0.1` |
| `port` | 端口 | `8765` |
| `plates_dir` | 图版目录（JPG/PNG） | `examples/demo/plates` |
| `boxes` | `boxes.json` 路径 | `examples/demo/boxes.json` |
| `text` | 释文纯文本 | `examples/demo/transcription.txt` |
| `plate_glob` | 图版文件名 glob；留空表示目录下全部 `.jpg` / `.jpeg` / `.png` | `*.png` |
| `source` | 写入 `boxes.json` 的 `source` | `manual` |
| `crop_out` | 裁切输出目录 | `cropped` |
| `cjk_font` | 对照表用的中文字体文件；留空则自动找系统字体 | （空） |

优先级：命令行 > 环境变量 > `config.json` > 源码检出时的 `config.example.json` > 内置默认值。在仓库里跑、又没有指定数据目录时，没有 `config.json` 就会用仓库根目录的 `config.example.json`。装好的 `cba` 如果数据目录里也没有 `config.json`，图版和释文改读安装包里的示例，`boxes.json` 和裁切目录则放在数据目录。

环境变量：

| 变量 | 对应字段 |
| --- | --- |
| `CBA_DATA_DIR` | 数据目录（等同 `--data-dir`） |
| `CBA_CONFIG` | 配置文件路径 |
| `CBA_HOST` | `host` |
| `CBA_PORT` | `port` |
| `CBA_PLATES_DIR` | `plates_dir` |
| `CBA_BOXES` | `boxes` |
| `CBA_TEXT` | `text` |
| `CBA_PLATE_GLOB` | `plate_glob` |
| `CBA_SOURCE` | `source` |
| `CBA_CROP_OUT` | `crop_out` |
| `CBA_FONT` | `cjk_font` |

```bash
python serve.py --plates-dir plates --boxes boxes.json --text transcription.txt --port 8765
python crop_glyphs.py --plates-dir plates --boxes boxes.json --out cropped
```

图版 id 是文件名去掉扩展名。`plate-01.png` 的 id 是 `plate-01`。同名的多种扩展名只保留一个。`plate_glob` 必须是图版目录内的相对模式，不能是绝对路径，也不能包含 `..`。

## 使用

1. 把 JPG/PNG 放到一个目录，释文存成 UTF-8 纯文本。
2. 在配置里填上 `plates_dir`、`boxes`、`text`。
3. `python serve.py`，在浏览器里框选。
4. 保存后运行 `python crop_glyphs.py`。

释文里只取中日韩统一表意文字（U+4E00–U+9FFF）。标点、字母和数字会跳过。以 `#` 开头的行是注释，整行不参与贴标签。

「跟随起点」时，标签按中文阅读顺序分配：先按框的横向位置分成若干列（列从右到左），列内再从上到下。某一张图版的「释文起点」是这张图第一个框在整篇释文里的下标（从 0 计）。改完起点后点「重贴标签」，或让输入框失焦，标签会重算。几何变化（新建、移动、缩放、删除）也会按当前起点重贴。

「固定」时，每个框保留自己的 `char`。加载、保存和移动都不会重算。新画的框没有字：选中它，在「字」里填一个汉字，或在「序号」里填释文下标（旁边会预览那个字），再点「贴到选中」或按 `L`。这只改选中框。同一列里，上方序号 `a`、下方序号 `b` 正好空出一位、中间只有这一个框时，「邻框补序号」会把 `a + 1` 填进序号框，仍要再贴一次。「重贴标签」会先询问，确认后才按起点覆盖整张，逐列对齐会被改掉。详见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

坐标是整张图像的像素，包含边框，原点在左上角，`x` 向右、`y` 向下。

### 快捷键

| 按键 | 作用 |
| --- | --- |
| 拖拽空白处 | 画新框 |
| 点击框内 | 选中并拖动 |
| 拖动选中框的边角 | 缩放 |
| 滚轮 | 以指针为中心缩放 |
| 空格或鼠标中键拖动 | 平移画布 |
| `N` / `P` | 下一张 / 上一张 |
| `S` | 保存 |
| `Ctrl+Z` 或 `Cmd+Z` | 撤销 |
| `Delete` 或 `Backspace` | 删除选中框 |
| `L` | 把「字」或「序号」贴到选中框（不改其他框） |

选中框之后，可以用「重文点」和「不制卡」两个按钮打标。不制卡的原因是补纸、空白或残损。给选中框单独贴字用「字」/「序号」和「贴到选中」（快捷键 `L`）。序号框旁边的字是释文预览。最后改的是序号时，贴上的字来自释文；最后改的是字、序号留空时，只改这个字，不动 `ti`。

工具栏还有上一张、下一张、撤销、删除选中、清空本张、保存、重贴标签、缩放按钮。未保存就关闭页面时，浏览器会提示。

## API

服务只提供标注页面和下面这些接口，不会把仓库里的其他文件暴露出去。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/plates` | `{"plates": ["plate-01", ...]}` |
| `GET` | `/api/plate/<id>` | 该图版的 JPEG 或 PNG |
| `GET` | `/api/text` | `{"chars": ["甲", ...], "length": 16}` |
| `GET` | `/api/boxes` | 当前 `boxes.json`（缺图版会补成空列表） |
| `POST` | `/api/boxes` | 保存。请求体需要 `plates`，可选 `textOffsetByPlate`、`labelMode` 和 `source` |

`POST` 成功时返回 `{"ok": true, "path": "...", "counts": {"plate-01": 4}}`。未知图版、越界的标签（不是单个 CJK 字）会被丢掉。保存是先写临时文件再替换。

### boxes.json

```json
{
  "source": "manual",
  "labelMode": "fixed",
  "textOffsetByPlate": {
    "plate-01": 0,
    "plate-02": 8
  },
  "plates": {
    "plate-01": [
      {"id": 1, "col": 1, "ti": 0, "x": 321, "y": 83, "w": 150, "h": 148, "char": "甲"},
      {"id": 2, "col": 1, "ti": 1, "x": 321, "y": 242, "w": 150, "h": 148, "char": "乙", "repeatMark": true}
    ]
  }
}
```

`char` 可以省略。`x`、`y`、`w`、`h` 是整图像素。`col` 是列号（1 为最右一列），`ti` 是释文序号。`repeatMark` 表示重文点。`noCard` 加上 `noCardReason`（`repair` / `blank` / `damaged`）表示留框但不制卡。

`labelMode` 为 `fixed` 或 `offset`。这个文件和 `cropped/` 都在 `.gitignore` 里。第一次启动 `serve.py` 时，如果 `boxes.json` 还不存在，会从旁边的 `boxes.example.json` 复制一份；没有示例文件就写一份空的。

## 裁切

```bash
python crop_glyphs.py
python crop_glyphs.py --boxes boxes.json --plates-dir plates --out cropped --plate-glob "*.jpg"
python crop_glyphs.py --include-nocard --skip-repeat
```

每个框裁成一张 PNG。框超出图像的部分会裁掉；裁完没有像素的框会跳过。默认跳过 `noCard`。`repeatMark` 会裁出，文件名多一段 `_repeat`；`--skip-repeat` 则跳过。输出目录默认是配置里的 `crop_out`。

## 实战经验

用这个工具给《书谱》做逐列重对之后，整理了两份说明。做法是通用的，仓库里仍然只有合成示例图。

- [docs/WORKFLOW.md](docs/WORKFLOW.md)：逐列对齐、按墨迹切成正好 N 格、整列居中、对照表、spec 重跑链、人只看拿不准的列。
- [docs/CURSIVE_RULES.md](docs/CURSIVE_RULES.md)：改字点、重文点、侧写小字、补纸 / 空白 / 残笔，以及为什么不要靠通用书法分类器。

```bash
python tools/column_cut.py --per-column 4 --plate plate-01
python tools/contact_sheet.py --lowconf examples/demo/lowconf.example.json --out sheets
python tools/apply_column_spec.py --spec path/to/spec.json --label fix-1
```

## 给 AI 代理用

看图的是代理自己的视觉。本仓库不调用模型 API，也不读取 API key。在仓库根目录按 [AGENTS.md](AGENTS.md) 操作：先看哪些列还没审，导出一列的图和释文，对照 [docs/CURSIVE_RULES.md](docs/CURSIVE_RULES.md)，把能确定的改动写成 spec 再写回，拿不准的列留给人。

```bash
python -m cba status --json
python -m cba validate --json
python -m cba export-column --plate plate-01 --col 1 --json
# 装成命令之后也可以：
cba status --json
uvx --from git+https://github.com/AsahiArt/calligraphy-box-annotator cba status --json
```

`status` 列出每张图版、每一列的框数，以及还没看过的列。审阅进度在 `boxes.json` 旁边的 `boxes.review.json`，中断后重新运行 `status` 即可继续。人标成 `approved` 的列不会被改写。

## 开发

```
serve.py            本地 HTTP 服务
crop_glyphs.py      按 boxes.json 裁切单字
boxannotator.py     配置、图版发现、校验与裁切
cba/                代理用命令行（cba，或 python -m cba）
pyproject.toml      用 uv 安装时的包配置，控制台命令是 cba
tools/              列切分、对照表、spec 应用
web/                页面、样式、前端逻辑
docs/               逐列工作流和草书规则
AGENTS.md           给编程代理的逐步说明
examples/demo/      合成示例图、释文、示例框
config.example.json 开箱即用的示例配置
```

重新生成示例图（需要本机有中文字体，例如文泉驿、Noto Sans CJK，或用 `CBA_DEMO_FONT` 指定字体文件）：

```bash
python examples/demo/generate_plates.py
```

测试：

```bash
python -m unittest discover -s tests -t .
```

改界面时请保持现有交互：缩放与平移、撤销、保存、阅读顺序贴标签、释文起点。不要把馆藏扫描件或真实标注数据放进仓库。

## English

Local tool for boxing single characters on calligraphy plates. Open a folder of JPG/PNG images in the browser, draw and edit boxes, label them from a plain-text transcription in Chinese reading order (columns right to left, top to bottom within a column), save `boxes.json` in full-image pixel coordinates, and crop glyph PNGs with `crop_glyphs.py`.

```bash
python -m pip install -r requirements.txt
python serve.py
# http://127.0.0.1:8765/
python crop_glyphs.py
```

To drive the same steps from an agent, use `python -m cba` or the `cba` console script (status, export-column, apply-spec, contact-sheet, validate, serve). The agent supplies its own vision; this repo never calls a model API. The workflow, spec format, and the rule against rewriting human-approved boxes are in [AGENTS.md](AGENTS.md).

Install it with uv, then run it from any directory. Writes go to the current directory or `--data-dir`, and the installed package is read-only:

```bash
uv tool install .
uv tool install git+https://github.com/AsahiArt/calligraphy-box-annotator
uvx --from git+https://github.com/AsahiArt/calligraphy-box-annotator cba status --json
cba serve --data-dir ./data --port 8765
```

`python -m cba` still works from a source checkout and from any environment where the package is installed. `cba serve` launches the same annotator as `python serve.py`.

Python 3.9+ is enough to serve the UI (stdlib only). Pillow is required to crop glyphs, draw contact sheets, and regenerate the demo plates. In a source checkout, if `config.json` is absent, `config.example.json` is used, so a fresh clone runs against the synthetic plates under `examples/demo/`. The first launch seeds `boxes.json` from `boxes.example.json` when that file sits beside the configured boxes path, or from the copy bundled in the installed package. Copy `config.example.json` to `config.json` to point at your own images. See the Chinese sections above for config fields, the HTTP API, and keyboard shortcuts. [docs/WORKFLOW.md](docs/WORKFLOW.md) and [docs/CURSIVE_RULES.md](docs/CURSIVE_RULES.md) record the per-column alignment workflow learned on the cursive scroll *Shupu*.

This repository does not include museum or other copyrighted calligraphy images. You must supply images you have the rights to use. The code is MIT licensed.

## 许可

代码以 [MIT License](LICENSE) 发布。图版内容不在许可范围内：使用者自行准备图片，并自行确认自己有权使用、复制和裁切这些图片。

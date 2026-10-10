# 给编程代理的操作说明

你自己看图。这个仓库**没有**模型 API，也不要配置或读取任何 API key（包括 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 一类）。需要目视的时候，用你自带的视觉去读 `export-column` 写出的 PNG。

在仓库根目录执行。下面的路径都是相对路径。不要把馆藏扫描件放进仓库，不要改 `examples/demo/boxes.example.json`。

## 循环

1. **准备。** 在仓库里：`python -m pip install -r requirements.txt`，然后用 `python -m cba`。也可以装成命令：`uv tool install .`，之后在任意目录运行 `cba`。不克隆仓库时：`uv tool install git+https://github.com/AsahiArt/calligraphy-box-annotator`，或临时执行 `uvx --from git+https://github.com/AsahiArt/calligraphy-box-annotator cba status --json`。装好的命令把 `boxes.json`、`review/`、`sheets/` 写到当前目录或 `--data-dir`，示例图从安装包只读读取，不要往安装包里写文件。源码检出且没有指定数据目录时，没有 `config.json` 则用 `config.example.json`，图在 `examples/demo/plates/`，释文在 `examples/demo/transcription.txt`。`boxes.json` 不存在时，只读命令会改读旁边的 `boxes.example.json`（安装包里则改读打包的示例）。
2. **看进度。** `python -m cba status --json`。只处理 `unreviewed`。`approved` 为真的列跳过，不要写进 spec。
3. **要几何提案时再切列。** `python -m cba cut --plate plate-01 --per-column 4 --json`（等同 `python tools/column_cut.py`）。这一步**不写** `boxes.json`。把 spans 抄进 spec，并填上正确的 `t0`，再用 `apply-spec` 落盘。
4. **导出一列。** `python -m cba export-column --plate <图版> --col <列号> --json`。打开 JSON 里的 `image`（带编号的框，编号自上而下从 1 起），并读 `text`（这一列的释文切片，行序与编号一致）。
5. **对照 [docs/CURSIVE_RULES.md](docs/CURSIVE_RULES.md) 看这一列。** 逐项检查：
   - 改字点、字旁孤立的墨点：不要单独成框，并进旁边的字。
   - 重文点：保留原字，标 `repeat`（框上是 `repeatMark`），不要把它当异写。
   - 侧写补上的小字：占自己的释文序号，spec 里用四元组 `[y0, y1, x, w]`。
   - 补纸 / 空白 / 残笔：留框、留标签，`noCard` 原因分别是 `repair` / `blank` / `damaged`。
   - 框要罩住整字并居中：先整列对准墨迹，再微调单框。不要切掉笔画。
6. **能确定就写 spec，然后应用。** `python -m cba apply-spec --spec <文件> --label fix-1 --json`。它会先备份成 `boxes.json.pre_<label>`，再替换点名的列。然后重新 `export-column`（或 `python -m cba contact-sheet --json`）再看一遍。确认无误后：`python -m cba review --plate <图版> --col <列号> --reviewed --json`。
7. **不能确定就升级，不要猜字。** `python -m cba review --plate <图版> --col <列号> --unresolved --note "原因" --json`。原因写给人看，例如墨迹不够、顶边是空白、补纸上只剩几个字。
8. **交给人的清单**就是 `status --json` 里的 `unresolved`。另外跑 `python -m cba validate --json`。有问题且该列不是 `approved` 时，用 spec 修，或标 unresolved。`approved` 列即使校验报错也不要改框，把问题写进 unresolved 的 note 即可。

中断之后从第 2 步继续。进度在配置的 boxes 路径旁边：`boxes.json` 对应 `boxes.review.json`。`reviewed` 的列不要重看，除非后来的 `validate` 指出它有问题。

## 命令

全部接受 `--json`（结果是 stdout 上的一个 JSON 对象）、`--data-dir`、`--boxes`、`--plates-dir`、`--text`、`--plate-glob`、`--config`。失败时退出码非 0。没有交互提示。相对路径相对于 `--data-dir`（或 `CBA_DATA_DIR`），没指定时就是当前目录。

| 命令 | 作用 |
| --- | --- |
| `status` | 每张图版、每一列的框数、`ti` 范围、释文，以及 `unreviewed` / `unresolved` / `approved` |
| `export-column --plate ID --col N` | 写出 `review/<图版>/col-NN.png` 和同名 `.txt` |
| `apply-spec --spec 文件 --label 名字` | 应用 spec。`--dry-run` 不写文件 |
| `contact-sheet` | 对照表，默认写到 `sheets/`。`--lowconf` 给低置信列画红框 |
| `validate` | 检查 `boxes.json`。有问题则退出码为 1 |
| `review --plate ID --col N --reviewed\|--unresolved\|--clear` | 写审阅 sidecar。`--unresolved` 必须带 `--note`。不会改 `approved` |
| `cut --plate ID --per-column N` | 按墨迹提议框，不落盘 |
| `serve --data-dir 目录 --port 端口` | 打开标注网页。`boxes.json` 写在数据目录，不写进安装包 |

`validate` 的 `issues[].code`：

| code | 含义 |
| --- | --- |
| `schema` | 缺字段、宽高不是正数、类型不对 |
| `empty_char` / `bad_char` | 没有字，或不是一个 CJK 字 |
| `overlap` | 两个框的矩形面积相交。只贴着边不算 |
| `duplicate_id` / `duplicate_ti` | 框号或释文序号重复 |
| `missing_ti` / `ti_gap` / `ti_out_of_range` | 没有 `ti`、列内或列间序号有空洞、序号超出释文 |
| `ti_order` | 同一列里，越往下 `ti` 没有变大 |
| `char_mismatch` | `char` 不等于释文里的 `chars[ti]` |
| `no_card_reason` | `noCard` 但原因不是 `repair` / `blank` / `damaged` |

`apply-spec` 若返回 `"code": "approved"`，立刻停止，不要换一条命令去改那些框。

## spec

一个对象，只写要替换的列。`spans` 自上而下，每一项是 `[y0, y1]` 或侧写小字的 `[y0, y1, x, w]`（整图像素）。第 `i` 项的释文序号是 `t0 + i`。`repeat` 是要标重文点的序号列表。`noCard` 的键是这些序号（JSON 里写成字符串），值是 `repair`、`blank` 或 `damaged`。

示例图版 `plate-01` 右列（列 1）四个字，第二个是重文点。这组数和 `examples/demo/boxes.example.json` 一致：

```json
{
  "plates": {
    "plate-01": {
      "1": {
        "t0": 0,
        "x": 321,
        "w": 150,
        "spans": [[83, 231], [242, 390], [400, 548], [559, 707]],
        "repeat": [1]
      }
    }
  }
}
```

下面这一列**不是**示例图版的真实坐标，只说明两种标记怎么写。第一段是空白不制卡（`ti` 为 4）。第二段 `[100, 170, 250, 42]` 是侧写小字：它有自己的 `x`、`w`，`ti` 是 5，后面的字依次后移。不要把小字并进旁边的大字。

```json
{
  "plates": {
    "plate-01": {
      "2": {
        "t0": 4,
        "x": 89,
        "w": 150,
        "spans": [[83, 231], [100, 170, 250, 42], [242, 390]],
        "noCard": {"4": "blank"}
      }
    }
  }
}
```

`t0` 从邻列推：列号 1 是最右一列，它的 `t0` 接上一张图版最后一列的 `tiMax + 1`；同一张图版里，左列接右列的 `tiMax + 1`。`status` 里每列都有 `tiMin` / `tiMax`。

## 不要改人已确认的框

人用两种方式确认，你都要当成只读：

- 框上的 `"approved": true`（页面保存时会保留这个字段）。
- `boxes.review.json` 里该列的 `"approved": true`。没有 `col` 的已确认框，整张图版都不要改。

```json
{
  "version": 1,
  "columns": {
    "plate-01": {
      "1": {"reviewed": true, "unresolved": false, "approved": true, "note": ""}
    }
  }
}
```

没有命令能替人把列标成 approved。不要手改 `boxes.json` 来绕过拒绝；每一层改动都走 spec，这样才有 `boxes.json.pre_<label>` 可以退回。

只有规则和图像对不上、你无法判断的列，才标 `unresolved`。能判断的自己写完。

## English

Run `python -m cba` from the repo root, or install the `cba` command with `uv tool install .` / `uv tool install git+https://github.com/AsahiArt/calligraphy-box-annotator`. A one-off run is `uvx --from git+https://github.com/AsahiArt/calligraphy-box-annotator cba`. The agent uses its own vision on `export-column` PNGs; this repo makes no model calls and needs no API key. Loop: `status` → export each unreviewed column → compare with `docs/CURSIVE_RULES.md` → write a spec → `apply-spec` → export again → `review --reviewed`, or `review --unresolved --note` when the column cannot be decided. `approved` columns and boxes are never rewritten (`apply-spec` exits non-zero and leaves the file unchanged). Resume from `boxes.review.json` via `status`. `cba serve --data-dir <dir> --port 8765` launches the annotator; writes stay in that directory (or the current directory), never in the installed package. Demo plates are synthetic; do not add museum images.

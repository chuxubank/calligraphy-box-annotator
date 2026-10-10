# 逐列对齐工作流

这套做法来自对孙过庭《书谱》一卷草书的实战：大约 350 列、三千多个框，逐列重新对上释文，再目视改完拿不准的地方。下面只保留能用到任何图版上的步骤。仓库里没有馆藏扫描件；示例图仍是 `examples/demo/` 里的合成图。

## 不要用「整张图版一个起点」

旧做法是给每一张图版一个释文起点，然后按阅读顺序把字依次贴上去。列内只要漏一个框、多一个框，这一张图版上后面的标签全部错一位，而且会一直错到下一张。

改为**逐列对齐**：

- 每一列自己对应释文里的一段 `[t0, t1)`。框上的 `ti` 是这段里的序号，`col` 是列号（阅读顺序，1 是最右一列）。
- 漏一个框只影响这一列。下一列的起点还在原来的 `ti` 上。
- 标注工具用 `labelMode: "fixed"`。加载、保存、移动、缩放都**不会**按图版起点重算标签。只有明确点「重贴标签」才覆盖。
- `textOffsetByPlate` 可以留着当参考，固定模式下工具会忽略它。

## 一列切成正好 N 个框

N 是这一列释文的字数。不要按「看起来像一个连通域」随便切：草书连笔会把两个字粘在一起，强制按连通域计数时，又常常把旁边的改字点当成一个字。

做法：

1. 先定列的横向范围（墨迹带，或已有框的 x 范围）。
2. 在这一列的纵向墨迹上找空隙，切成**正好 N 段**。空隙不够就合并靠得最近的碎块；一段里明显有两个字，就在这段内部最弱的位置切开。
3. **先把整列移到墨迹的中心**，每次最多移动约 ±20% 列宽，可以迭代几次。
4. 再逐框微调，每框最多 ±15% 自身宽度。

不要一上来就逐框居中。小字、侧写的字、笔势甩出去的一点，会被吸到相邻列里。

`tools/column_cut.py` 是这个步骤的通用实现，不依赖某一次卷子的坐标。示例图版上可以试：

```bash
python tools/column_cut.py --per-column 4 --plate plate-01
```

它只打印切出来的框，不写 `boxes.json`。确认之后再收进标注文件。

## 用对照表复查，而不是对着原图一格格猜

`tools/contact_sheet.py` 按列排出对照表（列从右往左）：

- 格子里是**原色裁图**，不把墨迹抽成二值图，补纸、印章、浅淡的笔才能看见。
- 旁边印出标签。
- 灰色小字是 `ti`（释文序号）。没有 `ti` 时退回框号。
- 低置信列画**红框**。传入 `--low-cols 2` 或一份 JSON（列表，或 `{"2": "原因"}`）。
- 重文点是灰框，标「重文」。不制卡是蓝框加斜线，并写原因（补纸 / 空白 / 残损）。

```bash
python tools/contact_sheet.py --lowconf examples/demo/lowconf.example.json --out sheets
```

红框不是失败记录，而是「这一列还不能自动算完」。书谱那次，350 列里多数有目视锚点；剩下的列标红，错位被限制在未锚定的那一列内部。

## 手改写成 spec，一层一层重跑

目视确定的改动不要只留在对话里。写成一份 spec：某一列、释文起点 `t0`、每个字的纵向范围 `(y0, y1)`，侧写的小字再加 `(x, w)`。然后用脚本应用到 `boxes.json`。

```bash
python tools/apply_column_spec.py --spec path/to/spec.json --label fix-3
```

脚本会先把当前文件复制为 `boxes.json.pre_fix-3`，再替换 spec 里点名的列。其他列不动。重跑顺序是一条链：自动切分 → spec 1 → spec 2 → … → 再出对照表。链是可重复的；备份让你能退回任意一层。

spec 的形状：

```json
{
  "plates": {
    "plate-01": {
      "1": {
        "t0": 0,
        "x": 321,
        "w": 150,
        "spans": [[83, 231], [242, 390]],
        "repeat": [1],
        "noCard": {"4": "blank"}
      }
    }
  }
}
```

`spans` 的顺序就是阅读顺序（上到下）。四元组 `[y0, y1, x, w]` 表示写在列侧的小字，它在释文里占自己的位置，而不是附在旁边那个字上。

## 目视通查，人只看拿不准的

每一列都要看过：对照表上的字和释文是否同一个、一个框里是不是两个字、一个字是不是被切成两截。能确定的写成 spec。不能确定的保持红框，并写一句原因（墨迹不够、补纸上只剩几个字、顶边是空白）。

书谱后期就是这样做的：程序和目视先处理能判断的列，对照表上标红的才送到人面前。人确认的是硬的那几个，不是从头再标一遍。

## 和本仓库工具的对应

| 步骤 | 工具 |
| --- | --- |
| 浏览、改框、固定标签、标重文点 / 不制卡 | `python serve.py` |
| 按墨迹把每列切成 N 框并整列居中 | `python tools/column_cut.py` |
| 对照表 | `python tools/contact_sheet.py` |
| 把一层手改写回 boxes.json，并留备份 | `python tools/apply_column_spec.py` |
| 裁单字图（默认跳过不制卡，重文点文件名带 `_repeat`） | `python crop_glyphs.py` |

领域上的取舍（改字点、重文点、侧写小字、补纸）见 [CURSIVE_RULES.md](CURSIVE_RULES.md)。

## English

Per-column alignment replaced a single running text offset per plate: one missed box no longer shifts every later label. Cut each column into exactly N boxes on ink gaps, centre the whole column on its ink, then nudge each box by at most about ±15% of its width. Review with contact sheets (original-colour crop, printed label, grey transcription index, red border on low-confidence columns). Record manual fixes as specs and re-run them in order, each step backing up `boxes.json`. A visual pass checks every column; only undecidable columns go to a person. The worked example is the cursive scroll *Shupu* (书谱); this repository still ships only synthetic demo plates.

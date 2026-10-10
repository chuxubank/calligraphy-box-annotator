# 草书框选里学到的规则

这些规则是在给《书谱》逐列重对时，由使用的人改出来的，不是分类器训出来的。换一卷字时仍然适用；它们约束的是框和标签，不是某一家博物馆的图。

## 字旁的点往往不是字

改字用的点多半在字的右侧。它标记「这个字要改」，本身不是释文里的一个字，**不要给它单独一个框**。

单独一个墨点或一小段笔画，常常是旁边那个字的一部分（之字底、心字的一点、连出去的一笔）。把它并进相邻的字。两边都离得很远、面积也说得通的小墨点，才更像一个真的小字，先不要并。

强制「这一列必须有 N 个框」时，两个粘在一起的字中间如果没有空隙，多出来的名额就会落在这种点上，把点当成字。框数要对，但切点必须落在墨迹空隙上，而不是为了凑数去圈一个点。

## 重文点保留标签，但不做异写

重文点（例如「彬彬」的第二个彬、「通会通会」里重复的那两个字）在释文里占一个位置，框上**保留原来的字**，同时标 `repeatMark: true`。

制卡、做异写字头时不要用它。它是一个点或很小的重复记号，不是这个字的另一种写法。裁切时文件名会带 `_repeat`；`--skip-repeat` 可以整段跳过。

界面里重文点是灰框，角上标「重文」。

## 侧写的小字是后来补进释文的

有的字写得很小，挤在列的一侧，是正文漏写之后补上的。它**占释文里自己的序号**，不并进旁边那个大字。框可以用不同于这一列的 `x` / `w`（spec 里写成四元组）。

漏掉它，下面的标签会整列错一位。

## 释文里有的字都留着

被点去、又在别处重写的句子，只要释文里还在，框就保留。同一个字的不同写法都可以各自做一张卡。不要因为「这个字已经有一个好看的」就删掉另一个。

重文点是这条规则的例外：标签留着，但不拿去当异写。

## 补纸、空白、只剩残笔：留框，但不制卡

有些位置必须在序列里占着，却不能拿去当字卡：

| `noCardReason` | 什么时候用 |
| --- | --- |
| `repair` | 字在后补的纸条上，往往是工整的小字，不是这一卷的草书 |
| `blank` | 框落在空白纸上，墨迹不在这里（或只是占位） |
| `damaged` | 原字还在这个位置，但只剩下残笔，框用来记住位置 |

标 `noCard: true`，并写上原因。框和标签都保留，裁切和制卡默认跳过。界面里是蓝框、一条斜线，角上写「补纸 / 空白 / 残损」。`--include-nocard` 才会把它们裁出来。

## 字要完整，并且在框里居中

框要罩住整字，不能切掉笔画。长长的「心」字底、甩出去的竖笔，都要留在框内。

居中的顺序见 [WORKFLOW.md](WORKFLOW.md)：先整列对准墨迹带，再在 ±15% 宽度里微调单个框。只挪单个框，小字会被拖进旁边一列。

## 通用书法分类器帮不上忙

在真草书上，现成的 OCR 和以楷书、行书为主训练的 ResNet 一类分类器，top-1 大约只有 6%，接近不可用。标签以释文和逐列目视为准，不要用分类器的第一候选去改 `char`。

对照表和 spec 比「再跑一个模型」更有效：人只需要确认红框里那几列。

## English

Dots beside a character (usually on the right) are correction marks, not characters, and get no box. A lone dot or stroke fragment is usually part of the neighbouring glyph, so merge it. Repetition marks keep their label, are flagged `repeatMark`, and are never an alternate writing. Small characters written at the side of a column were omitted and inserted later; they take their own place in text order. Glyphs on repair slips, blank placeholders, and fragment-only damaged glyphs keep a box but are flagged `noCard` with a reason (`repair`, `blank`, or `damaged`). Keep every character that is in the transcription, including repeated passages; different writings of the same character can all be cards. Keep glyphs centred and do not clip strokes. Generic OCR or ResNet calligraphy classifiers were near-useless on real cursive (about 6% top-1). Forcing a box count tends to promote a dot into a character when two glyphs are joined.

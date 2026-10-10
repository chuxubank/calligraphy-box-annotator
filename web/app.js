(() => {
  "use strict";
  const HANDLE = 8;
  const MIN_BOX = 8;
  const COL_THRESH_RATIO = 0.55;
  const els = {
    canvas: document.getElementById("canvas"),
    plateSelect: document.getElementById("plateSelect"),
    textOffsetInput: document.getElementById("textOffsetInput"),
    statPlate: document.getElementById("statPlate"),
    statCount: document.getElementById("statCount"),
    statZoom: document.getElementById("statZoom"),
    statMsg: document.getElementById("statMsg"),
    statPreview: document.getElementById("statPreview"),
    statSelChar: document.getElementById("statSelChar"),
    statTextProgress: document.getElementById("statTextProgress"),
  };
  const ctx = els.canvas.getContext("2d");
  const state = {
    plates: [], plateIndex: 0, image: null, boxesByPlate: {}, nextId: 1,
    selectedId: null, zoom: 1, panX: 0, panY: 0, history: [], dirty: false,
    mode: "idle", drawStart: null, draft: null, moveOffset: null,
    resizeHandle: null, spaceDown: false, lastPointer: null,
    chars: [], textOffsetByPlate: {}, labelMode: "offset",
    labelField: null, labelSyncId: null,
  };
  function plateId() { return state.plates[state.plateIndex] || null; }
  function boxes() {
    const id = plateId();
    if (!id) return [];
    if (!state.boxesByPlate[id]) state.boxesByPlate[id] = [];
    return state.boxesByPlate[id];
  }
  function getTextOffset() {
    const id = plateId();
    if (!id) return 0;
    const v = state.textOffsetByPlate[id];
    return typeof v === "number" && isFinite(v) ? Math.max(0, Math.floor(v)) : 0;
  }
  function setTextOffset(n) {
    const id = plateId();
    if (!id) return;
    const v = Math.max(0, Math.floor(Number(n) || 0));
    state.textOffsetByPlate[id] = v;
    if (els.textOffsetInput) els.textOffsetInput.value = String(v);
  }
  function msg(text, ok) {
    els.statMsg.textContent = text || "";
    els.statMsg.style.color = ok === false ? "#f08080" : "#7dcea0";
  }
  /** Columns RTL by center, each column top-to-bottom. Same rule as suggest_missing_ti. */
  function clusterColumns(list) {
    if (!list || !list.length) return [];
    const items = list.slice();
    const widths = items.map(function (b) { return Math.max(1, b.w); }).sort(function (a, b) { return a - b; });
    const medianW = widths[Math.floor(widths.length / 2)] || 40;
    const thresh = COL_THRESH_RATIO * medianW;
    items.sort(function (a, b) {
      return (b.x + b.w / 2) - (a.x + a.w / 2);
    });
    const columns = [];
    for (let i = 0; i < items.length; i++) {
      const b = items[i];
      const cx = b.x + b.w / 2;
      let placed = false;
      for (let c = 0; c < columns.length; c++) {
        const col = columns[c];
        if (Math.abs(cx - col.cx) < thresh) {
          col.boxes.push(b);
          col.cx = (col.cx * (col.boxes.length - 1) + cx) / col.boxes.length;
          placed = true;
          break;
        }
      }
      if (!placed) columns.push({ cx: cx, boxes: [b] });
    }
    columns.sort(function (a, b) { return b.cx - a.cx; });
    for (let c = 0; c < columns.length; c++) {
      columns[c].boxes.sort(function (a, b) { return a.y - b.y; });
    }
    return columns;
  }
  /** Reading order: columns RTL, within a column top-to-bottom. */
  function sortBoxesReadingOrder(list) {
    const columns = clusterColumns(list);
    const ordered = [];
    for (let c = 0; c < columns.length; c++) {
      for (let j = 0; j < columns[c].boxes.length; j++) ordered.push(columns[c].boxes[j]);
    }
    return ordered;
  }
  function columnIndexOf(box, list) {
    const columns = clusterColumns(list);
    for (let c = 0; c < columns.length; c++) {
      if (columns[c].boxes.indexOf(box) >= 0) return columns[c].boxes;
    }
    return null;
  }
  /**
   * If this box is the only one between neighbours whose ti values leave
   * exactly one index unused, return that index. Same rule as
   * boxannotator.suggest_missing_ti.
   */
  function suggestMissingTi(box, list) {
    const column = columnIndexOf(box, list);
    if (!column) return null;
    const index = column.indexOf(box);
    let aboveIndex = -1;
    let aboveTi = null;
    for (let j = index - 1; j >= 0; j--) {
      if (typeof column[j].ti === "number" && isFinite(column[j].ti)) {
        aboveIndex = j;
        aboveTi = Math.round(column[j].ti);
        break;
      }
    }
    let belowIndex = -1;
    let belowTi = null;
    for (let j = index + 1; j < column.length; j++) {
      if (typeof column[j].ti === "number" && isFinite(column[j].ti)) {
        belowIndex = j;
        belowTi = Math.round(column[j].ti);
        break;
      }
    }
    if (aboveTi == null || belowTi == null) return null;
    if (belowTi - aboveTi === 2 && belowIndex - aboveIndex === 2) return aboveTi + 1;
    return null;
  }
  function isCjkChar(text) {
    return typeof text === "string" && text.length === 1 && text >= "\u4e00" && text <= "\u9fff";
  }
  function parseIndex(text) {
    const raw = String(text || "").trim();
    if (!raw) return null;
    if (!/^-?\d+$/.test(raw)) return NaN;
    return parseInt(raw, 10);
  }
  function syncTiPreview() {
    const el = document.getElementById("tiPreview");
    const input = document.getElementById("boxTiInput");
    if (!el || !input) return;
    const ti = parseIndex(input.value);
    if (ti == null) { el.textContent = ""; return; }
    if (typeof ti !== "number" || ti < 0 || ti >= state.chars.length) { el.textContent = "—"; return; }
    el.textContent = state.chars[ti];
  }
  function syncLabelInputs() {
    if (state.selectedId !== state.labelSyncId) {
      state.labelField = null;
      state.labelSyncId = state.selectedId;
    }
    const b = selectedBox();
    const charInput = document.getElementById("boxCharInput");
    const tiInput = document.getElementById("boxTiInput");
    // A toolbar click fires mouseup before the button's click handler. Do not
    // copy the box back over a value the user, or 邻框补序号, just put in the field.
    if (charInput && document.activeElement !== charInput && state.labelField !== "char") {
      charInput.value = b && b.char ? b.char : "";
    }
    if (tiInput && document.activeElement !== tiInput && state.labelField !== "ti") {
      tiInput.value = b && typeof b.ti === "number" && isFinite(b.ti) ? String(Math.round(b.ti)) : "";
    }
    syncTiPreview();
  }
  function assignCharsForPlate(pid, force) {
    // labelMode "fixed": labels were aligned per column. Moving a box must not
    // re-derive every label from the running plate offset. 重贴标签 passes force.
    if (state.labelMode === "fixed" && !force) return;
    const list = state.boxesByPlate[pid] || [];
    const start = typeof state.textOffsetByPlate[pid] === "number"
      ? Math.max(0, Math.floor(state.textOffsetByPlate[pid]))
      : 0;
    const ordered = sortBoxesReadingOrder(list);
    for (let i = 0; i < ordered.length; i++) {
      const idx = start + i;
      ordered[i].char = (idx >= 0 && idx < state.chars.length) ? state.chars[idx] : "";
    }
  }
  function assignCharsCurrent(force) {
    const id = plateId();
    if (!id) return;
    assignCharsForPlate(id, force);
  }
  function selectedBox() {
    return boxes().find(function (b) { return b.id === state.selectedId; }) || null;
  }
  function reasonLabel(reason) {
    if (reason === "repair") return "补纸";
    if (reason === "blank") return "空白";
    if (reason === "damaged") return "残损";
    return "";
  }
  function pushHistory() {
    const id = plateId();
    if (!id) return;
    state.history.push({
      plate: id,
      boxes: boxes().map(function (b) { return Object.assign({}, b); }),
      selectedId: state.selectedId,
      nextId: state.nextId,
      textOffset: getTextOffset(),
    });
    if (state.history.length > 80) state.history.shift();
  }
  function undo() {
    const snap = state.history.pop();
    if (!snap) { msg("无可撤销", false); return; }
    state.boxesByPlate[snap.plate] = snap.boxes.map(function (b) { return Object.assign({}, b); });
    if (plateId() === snap.plate) {
      state.selectedId = snap.selectedId;
      state.nextId = snap.nextId;
      state.textOffsetByPlate[snap.plate] = snap.textOffset;
      if (els.textOffsetInput) els.textOffsetInput.value = String(snap.textOffset);
    }
    state.dirty = true; updateStatus(); draw(); msg("已撤销");
  }
  function updateStatus() {
    const id = plateId();
    els.statPlate.textContent = id
      ? ("图版 " + id + " (" + (state.plateIndex + 1) + "/" + state.plates.length + ")")
      : "—";
    els.statCount.textContent = "框: " + boxes().length;
    els.statZoom.textContent = "缩放: " + Math.round(state.zoom * 100) + "%";
    document.title = id ? ("字形框选 · " + id) : "字形框选工具";

    const start = getTextOffset();
    const nBoxes = boxes().length;
    const total = state.chars.length;
    const endUsed = Math.min(total, start + nBoxes);
    if (els.statTextProgress) {
      els.statTextProgress.textContent = "释文: " + endUsed + "/" + total;
    }
    if (els.statPreview) {
      const preview = state.chars.slice(start, start + 8).join("");
      els.statPreview.textContent = preview ? ("释文: " + preview + (total > start + 8 ? "…" : "")) : "";
    }
    if (els.statSelChar) {
      const sel = selectedBox();
      let label = (sel && sel.char) ? sel.char : "";
      if (sel && sel.repeatMark) label += " 重文";
      if (sel && sel.noCard) label += " 不制卡" + (reasonLabel(sel.noCardReason) ? ("·" + reasonLabel(sel.noCardReason)) : "");
      els.statSelChar.textContent = label;
    }
    const repeatBtn = document.getElementById("btnRepeat");
    const noCardBtn = document.getElementById("btnNoCard");
    const reasonSel = document.getElementById("noCardReason");
    const modeSel = document.getElementById("labelMode");
    const selBox = selectedBox();
    if (repeatBtn) repeatBtn.classList.toggle("active", !!(selBox && selBox.repeatMark));
    if (noCardBtn) noCardBtn.classList.toggle("active", !!(selBox && selBox.noCard));
    if (reasonSel && selBox && selBox.noCardReason && document.activeElement !== reasonSel) {
      reasonSel.value = selBox.noCardReason;
    }
    if (modeSel && document.activeElement !== modeSel) modeSel.value = state.labelMode || "offset";
    if (els.textOffsetInput && document.activeElement !== els.textOffsetInput) {
      els.textOffsetInput.value = String(start);
      els.textOffsetInput.max = String(Math.max(0, total));
    }
    syncLabelInputs();
  }
  function fitZoom() {
    if (!state.image) return;
    const wrap = els.canvas.parentElement;
    const pad = 20;
    const sx = (wrap.clientWidth - pad) / state.image.width;
    const sy = (wrap.clientHeight - pad) / state.image.height;
    state.zoom = Math.min(1, sx, sy);
    state.panX = (wrap.clientWidth - state.image.width * state.zoom) / 2;
    state.panY = (wrap.clientHeight - state.image.height * state.zoom) / 2;
  }
  function resizeCanvas() {
    const wrap = els.canvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    els.canvas.width = Math.floor(wrap.clientWidth * dpr);
    els.canvas.height = Math.floor(wrap.clientHeight * dpr);
    els.canvas.style.width = wrap.clientWidth + "px";
    els.canvas.style.height = wrap.clientHeight + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }
  function screenToImage(sx, sy) {
    return { x: (sx - state.panX) / state.zoom, y: (sy - state.panY) / state.zoom };
  }
  function clampBox(b) {
    if (!state.image) return b;
    let x = b.x, y = b.y, w = b.w, h = b.h;
    if (w < 0) { x += w; w = -w; }
    if (h < 0) { y += h; h = -h; }
    x = Math.max(0, Math.min(x, state.image.width));
    y = Math.max(0, Math.min(y, state.image.height));
    w = Math.max(0, Math.min(w, state.image.width - x));
    h = Math.max(0, Math.min(h, state.image.height - y));
    return Object.assign({}, b, { x: x, y: y, w: w, h: h });
  }
  function hitTest(ix, iy) {
    const list = boxes();
    for (let i = list.length - 1; i >= 0; i--) {
      const b = list[i];
      if (ix >= b.x && iy >= b.y && ix <= b.x + b.w && iy <= b.y + b.h) return b;
    }
    return null;
  }
  function handleAt(ix, iy, b) {
    const hs = HANDLE / state.zoom;
    const pts = {
      nw: { x: b.x, y: b.y }, ne: { x: b.x + b.w, y: b.y },
      sw: { x: b.x, y: b.y + b.h }, se: { x: b.x + b.w, y: b.y + b.h },
      n: { x: b.x + b.w / 2, y: b.y }, s: { x: b.x + b.w / 2, y: b.y + b.h },
      w: { x: b.x, y: b.y + b.h / 2 }, e: { x: b.x + b.w, y: b.y + b.h / 2 },
    };
    for (const name of Object.keys(pts)) {
      const p = pts[name];
      if (Math.abs(ix - p.x) <= hs && Math.abs(iy - p.y) <= hs) return name;
    }
    return null;
  }
  function drawCharBadge(b, sel) {
    const ch = b.char;
    if (!ch) return;
    const padX = 3 / state.zoom;
    const padY = 2 / state.zoom;
    const fontPx = Math.max(11, Math.min(18, b.h * 0.28)) / state.zoom;
    ctx.font = "600 " + fontPx + "px \"Songti SC\",\"Noto Serif SC\",\"SimSun\",serif";
    const tw = ctx.measureText(ch).width;
    const bw = tw + padX * 2;
    const bh = fontPx + padY * 2;
    const bx = b.x;
    const by = b.y;
    if (b.repeatMark && !b.noCard) {
      ctx.fillStyle = "rgba(75,85,99,0.92)";
      ctx.strokeStyle = "#d1d5db";
    } else if (b.noCard) {
      ctx.fillStyle = "rgba(3,105,161,0.92)";
      ctx.strokeStyle = "#7dd3fc";
    } else {
      ctx.fillStyle = sel ? "rgba(47,111,237,0.92)" : "rgba(15,23,42,0.88)";
      ctx.strokeStyle = sel ? "#93c5fd" : "#f8fafc";
    }
    ctx.lineWidth = 1 / state.zoom;
    const r = 2 / state.zoom;
    // rounded-ish rect via path
    ctx.beginPath();
    ctx.moveTo(bx + r, by);
    ctx.lineTo(bx + bw - r, by);
    ctx.quadraticCurveTo(bx + bw, by, bx + bw, by + r);
    ctx.lineTo(bx + bw, by + bh - r);
    ctx.quadraticCurveTo(bx + bw, by + bh, bx + bw - r, by + bh);
    ctx.lineTo(bx + r, by + bh);
    ctx.quadraticCurveTo(bx, by + bh, bx, by + bh - r);
    ctx.lineTo(bx, by + r);
    ctx.quadraticCurveTo(bx, by, bx + r, by);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fffef8";
    ctx.textBaseline = "top";
    ctx.fillText(ch, bx + padX, by + padY);
  }
  function drawFlagTag(b) {
    const tags = [];
    if (b.repeatMark) tags.push("重文");
    if (b.noCard) tags.push(reasonLabel(b.noCardReason) || "不制卡");
    if (!tags.length) return;
    const fontPx = Math.max(11, 13) / state.zoom;
    ctx.font = "600 " + fontPx + "px sans-serif";
    ctx.textBaseline = "top";
    const text = tags.join(" ");
    const pad = 3 / state.zoom;
    const tw = ctx.measureText(text).width;
    const bw = tw + pad * 2;
    const bh = fontPx + pad * 2;
    const bx = b.x + b.w - bw;
    const by = b.y;
    ctx.fillStyle = b.noCard ? "rgba(3,105,161,0.92)" : "rgba(55,65,81,0.92)";
    ctx.fillRect(bx, by, bw, bh);
    ctx.fillStyle = "#f8fafc";
    ctx.fillText(text, bx + pad, by + pad);
  }
  function draw() {
    const wrap = els.canvas.parentElement;
    const w = wrap.clientWidth, h = wrap.clientHeight;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#111";
    ctx.fillRect(0, 0, w, h);
    if (!state.image) {
      ctx.fillStyle = "#888"; ctx.font = "16px sans-serif";
      ctx.fillText("加载中…", 24, 40); return;
    }
    ctx.save();
    ctx.translate(state.panX, state.panY);
    ctx.scale(state.zoom, state.zoom);
    ctx.drawImage(state.image, 0, 0);
    const list = boxes();
    for (let i = 0; i < list.length; i++) {
      const b = list[i];
      const sel = b.id === state.selectedId;
      let stroke = sel ? "#2f6fed" : "#22c55e";
      let fill = sel ? "rgba(47,111,237,0.18)" : "rgba(34,197,94,0.12)";
      if (b.noCard) {
        stroke = sel ? "#38bdf8" : "#0284c7";
        fill = "rgba(14,165,233,0.20)";
      } else if (b.repeatMark) {
        stroke = sel ? "#e5e7eb" : "#9ca3af";
        fill = "rgba(156,163,175,0.22)";
      }
      ctx.lineWidth = (sel ? 2.5 : 1.5) / state.zoom;
      ctx.strokeStyle = stroke;
      ctx.fillStyle = fill;
      ctx.fillRect(b.x, b.y, b.w, b.h);
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      if (b.noCard) {
        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x + b.w, b.y + b.h);
        ctx.stroke();
      }
      drawFlagTag(b);
      // small id at bottom-left (secondary); char badge at top-left
      ctx.fillStyle = b.noCard
        ? "rgba(2,132,199,0.9)"
        : (b.repeatMark ? "rgba(75,85,99,0.9)" : (sel ? "rgba(47,111,237,0.85)" : "rgba(22,163,74,0.75)"));
      ctx.font = (10 / state.zoom) + "px sans-serif";
      ctx.textBaseline = "alphabetic";
      ctx.fillText("#" + b.id, b.x + 2 / state.zoom, b.y + b.h - 3 / state.zoom);
      drawCharBadge(b, sel);
      if (sel) {
        const hs = HANDLE / state.zoom;
        const pts = [
          [b.x, b.y], [b.x + b.w, b.y], [b.x, b.y + b.h], [b.x + b.w, b.y + b.h],
          [b.x + b.w / 2, b.y], [b.x + b.w / 2, b.y + b.h],
          [b.x, b.y + b.h / 2], [b.x + b.w, b.y + b.h / 2],
        ];
        ctx.fillStyle = "#fff"; ctx.strokeStyle = "#2f6fed";
        ctx.lineWidth = 1 / state.zoom;
        for (let j = 0; j < pts.length; j++) {
          const px = pts[j][0], py = pts[j][1];
          ctx.fillRect(px - hs / 2, py - hs / 2, hs, hs);
          ctx.strokeRect(px - hs / 2, py - hs / 2, hs, hs);
        }
      }
    }
    if (state.draft) {
      const d = state.draft;
      ctx.lineWidth = 1.5 / state.zoom;
      ctx.setLineDash([6 / state.zoom, 4 / state.zoom]);
      ctx.strokeStyle = "#fbbf24";
      ctx.fillStyle = "rgba(251,191,36,0.15)";
      ctx.fillRect(d.x, d.y, d.w, d.h);
      ctx.strokeRect(d.x, d.y, d.w, d.h);
      ctx.setLineDash([]);
    }
    ctx.restore();
  }
  function pointerPos(e) {
    const r = els.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  function applyResize(b, handle, img) {
    let x1 = b.x, y1 = b.y, x2 = b.x + b.w, y2 = b.y + b.h;
    if (handle.indexOf("w") >= 0) x1 = img.x;
    if (handle.indexOf("e") >= 0) x2 = img.x;
    if (handle.indexOf("n") >= 0) y1 = img.y;
    if (handle.indexOf("s") >= 0) y2 = img.y;
    Object.assign(b, clampBox({ id: b.id, x: x1, y: y1, w: x2 - x1, h: y2 - y1, char: b.char }));
  }
  function onPointerDown(e) {
    els.canvas.focus();
    const p = pointerPos(e);
    state.lastPointer = p;
    const img = screenToImage(p.x, p.y);
    const panMode = state.spaceDown || e.button === 1 || e.button === 2;
    if (panMode) {
      state.mode = "pan"; els.canvas.style.cursor = "grabbing"; e.preventDefault(); return;
    }
    if (e.button !== 0) return;
    const sel = boxes().find(function (b) { return b.id === state.selectedId; });
    if (sel) {
      const h = handleAt(img.x, img.y, sel);
      if (h) {
        pushHistory(); state.mode = "resize"; state.resizeHandle = h; state.dirty = true; return;
      }
    }
    const hit = hitTest(img.x, img.y);
    if (hit) {
      pushHistory();
      state.selectedId = hit.id; state.mode = "move";
      state.moveOffset = { x: img.x - hit.x, y: img.y - hit.y };
      state.dirty = true; updateStatus(); draw(); return;
    }
    state.selectedId = null; state.mode = "draw";
    state.drawStart = img; state.draft = { x: img.x, y: img.y, w: 0, h: 0 };
    updateStatus(); draw();
  }
  function onPointerMove(e) {
    const p = pointerPos(e);
    const img = screenToImage(p.x, p.y);
    if (state.mode === "pan" && state.lastPointer) {
      state.panX += p.x - state.lastPointer.x;
      state.panY += p.y - state.lastPointer.y;
      state.lastPointer = p; draw(); return;
    }
    if (state.mode === "draw" && state.drawStart) {
      state.draft = clampBox({
        x: state.drawStart.x, y: state.drawStart.y,
        w: img.x - state.drawStart.x, h: img.y - state.drawStart.y,
      });
      draw(); return;
    }
    if (state.mode === "move" && state.selectedId != null) {
      const b = boxes().find(function (x) { return x.id === state.selectedId; });
      if (b) {
        b.x = img.x - state.moveOffset.x; b.y = img.y - state.moveOffset.y;
        Object.assign(b, clampBox(b)); draw();
      }
      return;
    }
    if (state.mode === "resize" && state.selectedId != null) {
      const b = boxes().find(function (x) { return x.id === state.selectedId; });
      if (b) applyResize(b, state.resizeHandle, img);
      draw(); return;
    }
    const sel = boxes().find(function (x) { return x.id === state.selectedId; });
    if (sel) {
      const h = handleAt(img.x, img.y, sel);
      if (h) {
        const map = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
          n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };
        els.canvas.style.cursor = map[h] || "crosshair"; return;
      }
    }
    if (state.spaceDown) els.canvas.style.cursor = "grab";
    else if (hitTest(img.x, img.y)) els.canvas.style.cursor = "move";
    else els.canvas.style.cursor = "crosshair";
  }
  function onPointerUp(e) {
    const gesture = state.mode === "draw" || state.mode === "move" || state.mode === "resize" || state.mode === "pan";
    if (state.mode === "draw" && state.draft) {
      const d = clampBox(state.draft);
      if (d.w >= MIN_BOX && d.h >= MIN_BOX) {
        pushHistory();
        const nb = {
          id: state.nextId++,
          x: Math.round(d.x), y: Math.round(d.y),
          w: Math.round(d.w), h: Math.round(d.h),
          char: "",
        };
        boxes().push(nb); state.selectedId = nb.id; state.dirty = true;
        assignCharsCurrent();
        msg("新增框 #" + nb.id + (nb.char ? (" → " + nb.char) : ""));
      }
      state.draft = null; state.drawStart = null;
    }
    if (state.mode === "move" || state.mode === "resize") {
      const b = boxes().find(function (x) { return x.id === state.selectedId; });
      if (b) {
        b.x = Math.round(b.x); b.y = Math.round(b.y);
        b.w = Math.round(b.w); b.h = Math.round(b.h);
      }
      // geometry may change reading order
      assignCharsCurrent();
      state.dirty = true;
    }
    state.mode = "idle"; state.lastPointer = null;
    els.canvas.style.cursor = state.spaceDown ? "grab" : "crosshair";
    // Toolbar buttons also receive mouseup. Refreshing the inputs here would
    // wipe a suggested 序号 before the button's click handler can read it.
    if (gesture) { updateStatus(); draw(); }
  }
  function deleteSelected() {
    if (state.selectedId == null) { msg("未选中框", false); return; }
    pushHistory();
    const id = plateId();
    state.boxesByPlate[id] = boxes().filter(function (b) { return b.id !== state.selectedId; });
    state.selectedId = null; state.dirty = true;
    assignCharsCurrent();
    updateStatus(); draw(); msg("已删除选中");
  }
  function clearPlate() {
    if (!boxes().length) { msg("本张已空"); return; }
    if (!confirm("清空图版 " + plateId() + " 的全部 " + boxes().length + " 个框？")) return;
    pushHistory();
    state.boxesByPlate[plateId()] = [];
    state.selectedId = null; state.dirty = true; updateStatus(); draw(); msg("已清空本张");
  }
  function relabelCurrent() {
    const id = plateId();
    if (!id) return;
    if (state.labelMode === "fixed") {
      const ok = confirm("固定模式下重贴标签会按释文起点覆盖本张全部框的字，逐列对齐会被改掉。继续覆盖？");
      if (!ok) { msg("已取消重贴"); return; }
    }
    pushHistory();
    setTextOffset(els.textOffsetInput ? els.textOffsetInput.value : getTextOffset());
    assignCharsCurrent(true);
    state.dirty = true;
    updateStatus(); draw();
    msg((state.labelMode === "fixed" ? "已按起点覆盖固定标签 · 起点 " : "已重贴标签 · 起点 ") + getTextOffset());
  }
  function applyLabelToSelected() {
    const b = selectedBox();
    if (!b) { msg("未选中框", false); return; }
    const tiInput = document.getElementById("boxTiInput");
    const charInput = document.getElementById("boxCharInput");
    const tiParsed = parseIndex(tiInput ? tiInput.value : "");
    const charText = charInput ? charInput.value.trim() : "";
    if (charText && !isCjkChar(charText)) { msg("字需要是一个汉字", false); return; }
    const tiReady = typeof tiParsed === "number" && !isNaN(tiParsed);
    const useTi = state.labelField === "ti" || (state.labelField !== "char" && tiReady);
    if (useTi) {
      if (tiParsed == null || isNaN(tiParsed)) { msg("先填写释文序号", false); return; }
      if (tiParsed < 0 || tiParsed >= state.chars.length) {
        msg("序号超出释文（0–" + Math.max(0, state.chars.length - 1) + "）", false);
        return;
      }
      pushHistory();
      b.ti = tiParsed;
      b.char = state.chars[tiParsed];
    } else if (charText) {
      pushHistory();
      b.char = charText;
    } else {
      msg("填写一个字，或填写释文序号", false);
      return;
    }
    state.labelField = null;
    state.dirty = true;
    updateStatus();
    draw();
    const tiNote = typeof b.ti === "number" ? (" · 序号 " + b.ti) : "";
    const modeNote = state.labelMode === "offset" ? "（跟随起点时，保存仍会按起点重贴）" : "";
    msg("已贴到 #" + b.id + " · " + b.char + tiNote + modeNote);
  }
  function suggestTiForSelected() {
    const b = selectedBox();
    if (!b) { msg("未选中框", false); return; }
    const ti = suggestMissingTi(b, boxes());
    const input = document.getElementById("boxTiInput");
    if (ti == null) { msg("邻框没有正好空出一位", false); return; }
    if (input) input.value = String(ti);
    state.labelField = "ti";
    syncTiPreview();
    const ch = ti >= 0 && ti < state.chars.length ? state.chars[ti] : "";
    msg("建议序号 " + ti + (ch ? (" · " + ch) : "") + "，按 L 或「贴到选中」写入");
  }
  function toggleRepeat() {
    const b = selectedBox();
    if (!b) { msg("未选中框", false); return; }
    pushHistory();
    b.repeatMark = !b.repeatMark;
    state.dirty = true;
    updateStatus(); draw();
    msg(b.repeatMark ? "已标重文点" : "已取消重文点");
  }
  function toggleNoCard() {
    const b = selectedBox();
    if (!b) { msg("未选中框", false); return; }
    pushHistory();
    b.noCard = !b.noCard;
    if (b.noCard) {
      const reasonSel = document.getElementById("noCardReason");
      const reason = reasonSel ? reasonSel.value : "damaged";
      b.noCardReason = reason === "repair" || reason === "blank" || reason === "damaged" ? reason : "damaged";
    } else {
      b.noCardReason = "";
    }
    state.dirty = true;
    updateStatus(); draw();
    msg(b.noCard ? ("不制卡 · " + reasonLabel(b.noCardReason)) : "已取消不制卡");
  }
  function setLabelMode(mode) {
    const next = mode === "fixed" ? "fixed" : "offset";
    if (next === state.labelMode) return;
    state.labelMode = next;
    state.dirty = true;
    updateStatus();
    msg(next === "fixed" ? "标签模式：固定（移动框不会重贴）" : "标签模式：跟随起点");
  }
  function zoomAt(factor, pivot) {
    const wrap = els.canvas.parentElement;
    const px = pivot ? pivot.x : wrap.clientWidth / 2;
    const py = pivot ? pivot.y : wrap.clientHeight / 2;
    const before = screenToImage(px, py);
    state.zoom = Math.max(0.05, Math.min(8, state.zoom * factor));
    state.panX = px - before.x * state.zoom;
    state.panY = py - before.y * state.zoom;
    updateStatus(); draw();
  }
  async function loadPlate(index) {
    if (index < 0 || index >= state.plates.length) return;
    state.plateIndex = index; state.selectedId = null; state.draft = null; state.mode = "idle";
    els.plateSelect.value = plateId();
    if (els.textOffsetInput) els.textOffsetInput.value = String(getTextOffset());
    assignCharsCurrent();
    updateStatus(); msg("加载图片…");
    const img = new Image();
    try {
      await new Promise(function (resolve, reject) {
        img.onload = resolve;
        img.onerror = reject;
        img.src = "/api/plate/" + encodeURIComponent(plateId());
      });
    } catch (err) {
      state.image = null;
      updateStatus();
      draw();
      msg("图片加载失败", false);
      return;
    }
    state.image = img;
    let maxId = 0;
    const keys = Object.keys(state.boxesByPlate);
    for (let i = 0; i < keys.length; i++) {
      const arr = state.boxesByPlate[keys[i]];
      for (let j = 0; j < arr.length; j++) maxId = Math.max(maxId, arr[j].id || 0);
    }
    state.nextId = Math.max(state.nextId, maxId + 1);
    fitZoom(); resizeCanvas(); updateStatus(); msg("已打开 " + plateId());
  }
  async function saveBoxes() {
    // ensure current plate labels are fresh before save
    assignCharsCurrent();
    const body = {
      source: "manual",
      labelMode: state.labelMode === "fixed" ? "fixed" : "offset",
      textOffsetByPlate: {},
      plates: {},
    };
    for (let i = 0; i < state.plates.length; i++) {
      const pid = state.plates[i];
      body.textOffsetByPlate[pid] = typeof state.textOffsetByPlate[pid] === "number"
        ? Math.max(0, Math.floor(state.textOffsetByPlate[pid]))
        : 0;
      body.plates[pid] = (state.boxesByPlate[pid] || []).map(function (b) {
        const item = { id: b.id, x: b.x, y: b.y, w: b.w, h: b.h };
        if (b.char) item.char = b.char;
        if (typeof b.col === "number" && isFinite(b.col)) item.col = Math.round(b.col);
        if (typeof b.ti === "number" && isFinite(b.ti)) item.ti = Math.round(b.ti);
        if (b.repeatMark === true) item.repeatMark = true;
        if (b.noCard === true) {
          item.noCard = true;
          if (b.noCardReason === "repair" || b.noCardReason === "blank" || b.noCardReason === "damaged") {
            item.noCardReason = b.noCardReason;
          }
        }
        return item;
      });
    }
    msg("保存中…");
    const res = await fetch("/api/boxes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) { msg("保存失败", false); return; }
    await res.json();
    state.dirty = false;
    let n = 0;
    const keys = Object.keys(body.plates);
    for (let i = 0; i < keys.length; i++) n += body.plates[keys[i]].length;
    msg("已保存 " + n + " 框");
  }
  async function init() {
    const platesRes = await fetch("/api/plates");
    const boxesRes = await fetch("/api/boxes");
    const textRes = await fetch("/api/text");
    const platesData = await platesRes.json();
    const boxesData = await boxesRes.json();
    const textData = await textRes.json();
    state.plates = platesData.plates || [];
    state.chars = Array.isArray(textData.chars) ? textData.chars : [];
    state.boxesByPlate = {};
    state.textOffsetByPlate = {};
    state.labelMode = boxesData.labelMode === "fixed" ? "fixed" : "offset";
    const savedOffsets = boxesData.textOffsetByPlate || {};
    for (let i = 0; i < state.plates.length; i++) {
      const pid = state.plates[i];
      const raw = (boxesData.plates && boxesData.plates[pid]) || [];
      state.boxesByPlate[pid] = raw.map(function (b) {
        return {
          id: b.id, x: b.x, y: b.y, w: b.w, h: b.h,
          char: typeof b.char === "string" ? b.char : "",
          col: typeof b.col === "number" ? b.col : undefined,
          ti: typeof b.ti === "number" ? b.ti : undefined,
          repeatMark: b.repeatMark === true,
          noCard: b.noCard === true,
          noCardReason: typeof b.noCardReason === "string" ? b.noCardReason : "",
        };
      });
      const off = savedOffsets[pid];
      state.textOffsetByPlate[pid] = typeof off === "number" ? Math.max(0, Math.floor(off)) : 0;
      assignCharsForPlate(pid);
    }
    els.plateSelect.innerHTML = "";
    for (let i = 0; i < state.plates.length; i++) {
      const opt = document.createElement("option");
      opt.value = state.plates[i]; opt.textContent = state.plates[i];
      els.plateSelect.appendChild(opt);
    }
    els.plateSelect.addEventListener("change", function () {
      const i = state.plates.indexOf(els.plateSelect.value);
      if (i >= 0) loadPlate(i);
    });
    document.getElementById("btnPrev").onclick = function () { loadPlate(state.plateIndex - 1); };
    document.getElementById("btnNext").onclick = function () { loadPlate(state.plateIndex + 1); };
    document.getElementById("btnUndo").onclick = undo;
    document.getElementById("btnDelete").onclick = deleteSelected;
    document.getElementById("btnClear").onclick = clearPlate;
    document.getElementById("btnSave").onclick = function () { saveBoxes(); };
    document.getElementById("btnZoomIn").onclick = function () { zoomAt(1.15); };
    document.getElementById("btnZoomOut").onclick = function () { zoomAt(1 / 1.15); };
    document.getElementById("btnZoomReset").onclick = function () { fitZoom(); updateStatus(); draw(); };
    document.getElementById("btnRelabel").onclick = relabelCurrent;
    const labelBtn = document.getElementById("btnLabelBox");
    const suggestBtn = document.getElementById("btnSuggestTi");
    const charInput = document.getElementById("boxCharInput");
    const tiInput = document.getElementById("boxTiInput");
    if (labelBtn) labelBtn.onclick = applyLabelToSelected;
    if (suggestBtn) suggestBtn.onclick = suggestTiForSelected;
    if (charInput) {
      charInput.addEventListener("input", function () { state.labelField = "char"; });
      charInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); applyLabelToSelected(); }
      });
    }
    if (tiInput) {
      tiInput.addEventListener("input", function () {
        state.labelField = "ti";
        syncTiPreview();
      });
      tiInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); applyLabelToSelected(); }
      });
    }
    const repeatBtn = document.getElementById("btnRepeat");
    const noCardBtn = document.getElementById("btnNoCard");
    const modeSel = document.getElementById("labelMode");
    const reasonSel = document.getElementById("noCardReason");
    if (repeatBtn) repeatBtn.onclick = toggleRepeat;
    if (noCardBtn) noCardBtn.onclick = toggleNoCard;
    if (modeSel) modeSel.addEventListener("change", function () { setLabelMode(modeSel.value); });
    if (reasonSel) {
      reasonSel.addEventListener("change", function () {
        const b = selectedBox();
        if (!b || !b.noCard) return;
        pushHistory();
        b.noCardReason = reasonSel.value;
        state.dirty = true;
        updateStatus(); draw();
        msg("不制卡 · " + reasonLabel(b.noCardReason));
      });
    }
    if (els.textOffsetInput) {
      els.textOffsetInput.addEventListener("change", function () {
        pushHistory();
        setTextOffset(els.textOffsetInput.value);
        assignCharsCurrent();
        state.dirty = true;
        updateStatus(); draw();
        msg("起点 → " + getTextOffset());
      });
      els.textOffsetInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          els.textOffsetInput.blur();
          relabelCurrent();
        }
      });
    }
    els.canvas.addEventListener("mousedown", onPointerDown);
    window.addEventListener("mousemove", onPointerMove);
    window.addEventListener("mouseup", onPointerUp);
    els.canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    els.canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, pointerPos(e));
    }, { passive: false });
    window.addEventListener("keydown", function (e) {
      if (e.code === "Space" && !e.repeat) {
        state.spaceDown = true; els.canvas.style.cursor = "grab"; e.preventDefault();
      }
      const tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if ((e.key === "Delete" || e.key === "Backspace") && !e.metaKey && !e.ctrlKey) {
        e.preventDefault(); deleteSelected();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault(); undo();
      } else if (!e.ctrlKey && !e.metaKey && e.key.toLowerCase() === "n") {
        e.preventDefault(); loadPlate(state.plateIndex + 1);
      } else if (!e.ctrlKey && !e.metaKey && e.key.toLowerCase() === "p") {
        e.preventDefault(); loadPlate(state.plateIndex - 1);
      } else if (!e.ctrlKey && !e.metaKey && e.key.toLowerCase() === "s") {
        e.preventDefault(); saveBoxes();
      } else if (!e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === "l") {
        e.preventDefault(); applyLabelToSelected();
      }
    });
    window.addEventListener("keyup", function (e) {
      if (e.code === "Space") { state.spaceDown = false; els.canvas.style.cursor = "crosshair"; }
    });
    window.addEventListener("resize", function () { resizeCanvas(); });
    window.addEventListener("beforeunload", function (e) {
      if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
    });
    if (state.plates.length) await loadPlate(0);
    else msg("未找到图版", false);
  }
  init().catch(function (err) { console.error(err); msg("初始化失败: " + err, false); });
})();

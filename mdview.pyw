# -*- coding: utf-8 -*-
"""mdview：本地 Markdown 查看器，只用 Python 自带的 tkinter，无第三方依赖。

    pythonw mdview.pyw [文件或目录 ...]      打开查看器
    python  mdview.pyw --dump a.md           打印解析结果（调试用）
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk
from tkinter import font as tkfont

UI_FAMILIES = ("microsoft yahei ui", "microsoft yahei", "segoe ui", "tahoma")
MONO_FAMILIES = ("consolas", "cascadia mono", "courier new")
MD_EXTS = (".md", ".markdown", ".mdown", ".mkd", ".txt")

THEMES = {
    "light": {
        "bg": "#ffffff", "fg": "#1f2328", "muted": "#6b7480",
        "panel": "#f6f8fa", "border": "#d0d7de", "heading": "#0a3d7a",
        "link": "#0969da", "code_fg": "#b3266b", "code_bg": "#f0f1f3",
        "block_bg": "#f7f8fa", "quote_bg": "#f4f6f8", "qtext": "#57606a",
        "mark": "#fff2a8", "code_str": "#0a3069", "code_com": "#6e7781",
        "th": "#eef1f4", "stripe": "#fafbfc",
        "cell": "#ffffff", "done": "#6b7480", "accent": "#0969da",
        "found": "#ffdf5e", "found_cur": "#ffa62b", "sel": "#bfdcff",
        "toc_bg": "#f6f8fa", "toc_sel": "#d3e6fb", "btn_bg": "#f2f4f7",
        "btn_hl": "#e3e7ed", "tip_bg": "#ffffe1",
    },
    "dark": {
        "bg": "#0d1117", "fg": "#d3dbe5", "muted": "#8a94a0",
        "panel": "#161b22", "border": "#30363d", "heading": "#7cc4ff",
        "link": "#58a6ff", "code_fg": "#ffab70", "code_bg": "#232a33",
        "block_bg": "#161b22", "quote_bg": "#161b22", "qtext": "#9aa4b0",
        "mark": "#4d431f", "code_str": "#a5d6ff", "code_com": "#768390",
        "th": "#1f2630", "stripe": "#11161d",
        "cell": "#0d1117", "done": "#7a8590", "accent": "#58a6ff",
        "found": "#7a5f16", "found_cur": "#a9740f", "sel": "#2b4c73",
        "toc_bg": "#161b22", "toc_sel": "#274159", "btn_bg": "#21262d",
        "btn_hl": "#30363d", "tip_bg": "#1c2128",
    },
}

HEADING_SIZES = (26, 20, 16, 13, 12, 11)
BULLETS = ("\u2022", "\u25e6", "\u25aa", "\u00b7")
CHECK_ON = "\u2611"
CHECK_OFF = "\u2610"
ESCAPABLE = "\\`*_{}[]()#+-.!|>~=$"
LM_STEP = 2
WHEEL_LINES = 3
CELL_PAD = 18
QUOTE_LM = 30

# ---------------------------------------------------------------- 行内解析 ---

LINK_RE = re.compile(r"^\[(?P<label>[^[\]]*)\]\(\s*(?P<url>[^()\s]*"
                     r"(?:\([^()]*\)[^()\s]*)*)\s*(?:\"[^\"]*\")?\s*\)")
IMG_RE = re.compile(r"^!\[(?P<label>[^[\]]*)\]\(\s*(?P<url>[^()\s]*"
                    r"(?:\([^()]*\)[^()\s]*)*)\s*(?:\"[^\"]*\")?\s*\)")
FOOTREF_RE = re.compile(r"^\[\^(?P<label>[^\]\s]+)\](?!:)")
AUTOLINK_RE = re.compile(r"^<(?P<url>https?://[^\s<>]+)>")
BR_RE = re.compile(r"^<br\s*/?>", re.I)
TAG_RE = re.compile(r"^</?[a-zA-Z][^<>\n]{0,60}/?>")
BARE_URL_RE = re.compile(r"^https?://[^\s<>)\]]+[^\s<>)\].,;:!?'\u3002\uff0c\uff1b\uff01\uff1f]")
DELIM_RE = re.compile(r"^(\*{1,3}|_{1,3})")


def span(text, styles=(), **extra):
    d = {"text": text, "styles": list(styles)}
    d.update(extra)
    return d


def plain(spans):
    return "".join(s.get("text", "") for s in spans if not s.get("nl"))


def parse_inline(text):
    out = []
    buf = []

    def flush():
        if buf:
            out.append(span("".join(buf)))
            del buf[:]

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        prev = text[i - 1] if i else ""
        if ch == "\\" and i + 1 < n and text[i + 1] in ESCAPABLE:
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == "\n":
            flush()
            out.append({"nl": True})
            i += 1
            continue
        if ch == "`":
            fence = re.match(r"`+", text[i:]).group(0)
            close = text.find(fence, i + len(fence))
            if close != -1:
                code = text[i + len(fence):close].replace("\n", " ")
                if len(code) > 1 and code[0] == code[-1] == " ":
                    code = code[1:-1]
                flush()
                out.append(span(code, ["code"]))
                i = close + len(fence)
                continue
        if ch == "<":
            m = AUTOLINK_RE.match(text[i:])
            if m:
                flush()
                out.append(span(m.group("url"), ["link"], url=m.group("url")))
                i += m.end()
                continue
            m = BR_RE.match(text[i:])
            if m:
                flush()
                out.append({"nl": True})
                i += m.end()
                continue
            m = TAG_RE.match(text[i:])
            if m:
                i += m.end()
                continue
        if text[i:i + 2] == "![":
            m = IMG_RE.match(text[i:])
            if m:
                url = m.group("url").strip("<>")
                alt = m.group("label") or url.rsplit("/", 1)[-1]
                flush()
                out.append(span(alt, ["img"], url=url))
                i += m.end()
                continue
        if ch == "[":
            m = LINK_RE.match(text[i:])
            if m:
                flush()
                inner = parse_inline(m.group("label"))
                url = m.group("url").strip("<>")
                for s in inner:
                    if not s.get("nl"):
                        s["styles"] = s["styles"] + ["link"]
                        s["url"] = url
                out.extend(inner)
                i += m.end()
                continue
            m = FOOTREF_RE.match(text[i:])
            if m:
                flush()
                out.append(span(m.group("label"), ["footref"],
                                label=m.group("label")))
                i += m.end()
                continue
        if ch in "*_":
            run = DELIM_RE.match(text[i:]).group(1)
            d, ln = run[0], len(run)
            cjk = "\u4e00" <= prev <= "\u9fff"
            gap = i + ln >= n or text[i + ln].isspace()
            if not (d == "_" and (prev.isalnum() or cjk)) and not gap:
                if ln == 1:
                    e = re.escape(d)
                    pat = "(?<!%s)%s(?!%s)" % (e, e, e)
                else:
                    pat = re.escape(run)
                cm = re.search(pat, text[i + ln:])
                if cm:
                    body = text[i + ln:i + ln + cm.start()]
                    if body.strip() and not body[-1].isspace():
                        styles = (["bold", "italic"] if ln == 3 else
                                  ["bold"] if ln == 2 else ["em"])
                        flush()
                        inner = parse_inline(body)
                        for s in inner:
                            if not s.get("nl"):
                                s["styles"] = s["styles"] + styles
                        out.extend(inner)
                        i += ln + cm.end()
                        continue
        if text[i:i + 2] == "~~":
            cm = re.search(r"~~(?!~)", text[i + 2:])
            if cm and text[i + 2:i + 2 + cm.start()].strip():
                flush()
                body = text[i + 2:i + 2 + cm.start()]
                inner = parse_inline(body)
                for s in inner:
                    if not s.get("nl"):
                        s["styles"] = s["styles"] + ["strike"]
                out.extend(inner)
                i += 2 + cm.end()
                continue
        if text[i:i + 2] == "==":
            cm = re.search(r"(?<!=)==(?!=)", text[i + 2:])
            if cm and text[i + 2:i + 2 + cm.start()].strip():
                flush()
                inner = parse_inline(text[i + 2:i + 2 + cm.start()])
                for s in inner:
                    if not s.get("nl"):
                        s["styles"] = s["styles"] + ["mark"]
                out.extend(inner)
                i += 2 + cm.end()
                continue
        if ch == "h" and not prev.isalnum() and prev not in "/-_.":
            m = BARE_URL_RE.match(text[i:])
            if m:
                flush()
                out.append(span(m.group(0), ["link"], url=m.group(0)))
                i += m.end()
                continue
        buf.append(ch)
        i += 1
    flush()
    return out


# ---------------------------------------------------------------- 块级解析 ---

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^\n`]*)$")
ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
HR_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
QUOTE_RE = re.compile(r"^ {0,3}>[ ]?(.*)$")
LIST_RE = re.compile(r"^( *)([-*+]|\d{1,9}[.)])( +)(.*)$")
TABLE_DELIM_RE = re.compile(r"^[ \t|]*[:-]+(?:[ \t|]*[:-]+)*[ \t|]*$")
FOOTDEF_RE = re.compile(r"^ {0,3}\[\^([^\]\s]+)\]:[ \t]*(.*)$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
TRAIL_RE = re.compile(r"[ \t]+$")


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [p.strip().replace("\\|", "|") for p in re.split(r"(?<!\\)\|", s)]


def cell_align(cell):
    c = cell.replace(" ", "")
    if c.startswith(":") and c.endswith(":"):
        return "center"
    if c.endswith(":"):
        return "right"
    return "left"


class Slugger:
    def __init__(self):
        self.used = {}

    def __call__(self, text):
        s = re.sub(r"[^\w\u4e00-\u9fff \-]", "", text).strip().lower()
        s = re.sub(r"\s+", "-", s) or "sec"
        if s in self.used:
            self.used[s] += 1
            s = "%s-%d" % (s, self.used[s])
        else:
            self.used[s] = 1
        return s


class Doc:
    def __init__(self):
        self.slug = Slugger()
        self.footnums = {}


def heading_item(doc, level, raw, anchor=None):
    spans = parse_inline(raw)
    title = plain(spans)
    return {"kind": "heading", "level": level, "spans": spans, "title": title,
            "anchor": anchor or doc.slug(title)}


def starts_block(line):
    s = TRAIL_RE.sub("", line.rstrip())
    return bool(ATX_RE.match(s) or HR_RE.match(s) or LIST_RE.match(s)
                or FENCE_RE.match(s))


def is_table_start(lines, i):
    line = TRAIL_RE.sub("", lines[i].rstrip())
    if "|" not in line or i + 1 >= len(lines):
        return None
    nxt = TRAIL_RE.sub("", lines[i + 1].rstrip())
    if "|" not in nxt or "-" not in nxt or not TABLE_DELIM_RE.match(nxt):
        return None
    cols = split_row(nxt)
    head = split_row(line)
    if not cols or len(head) != len(cols):
        return None
    if not all(re.match(r"^:?-{1,}:?$", c.replace(" ", "")) for c in cols):
        return None
    aligns = [cell_align(c) for c in cols]
    j = i + 2
    rows = []
    while j < len(lines):
        l = TRAIL_RE.sub("", lines[j].rstrip())
        if not l.strip() or "|" not in l or starts_block(l):
            break
        r = split_row(l)
        if len(r) < len(aligns):
            r += [""] * (len(aligns) - len(r))
        rows.append(r[:len(aligns)])
        j += 1
    return {"kind": "table", "head": head, "rows": rows, "aligns": aligns}, j


def collect_footnotes(lines, doc):
    """摘出脚注定义，编号按定义出现顺序。"""
    out = list(lines)
    defs = []
    i = 0
    while i < len(out):
        m = FOOTDEF_RE.match(out[i])
        if not m:
            i += 1
            continue
        label = m.group(1)
        body = [m.group(2)]
        j = i + 1
        while j < len(out) and (not out[j].strip()
                                or out[j].startswith("    ")
                                or out[j].startswith("\t")):
            if out[j].strip():
                body.append(out[j].strip())
            j += 1
        doc.footnums[label] = len(defs) + 1
        defs.append({"kind": "footdef", "label": label,
                     "num": len(defs) + 1,
                     "spans": parse_inline(" ".join(body))})
        for k in range(i, j):
            out[k] = ""
        i = j
    return out, defs


def parse_blocks(lines, doc):
    out = []
    para = []
    i, n = 0, len(lines)

    def emit_para():
        if not para:
            return
        text = "\n".join(para).rstrip()
        del para[:]
        if not text:
            return
        buf = []
        for ln in text.split("\n"):
            m = IMG_RE.match(ln.strip())
            if m and ln.strip().count("](") == 1:
                if buf:
                    out.append({"kind": "para",
                                "spans": parse_inline("\n".join(buf))})
                    del buf[:]
                url = m.group("url").strip("<>")
                out.append({"kind": "image", "url": url,
                            "alt": m.group("label") or url})
            else:
                buf.append(re.sub(r"\\$", "", ln.rstrip()))
        if buf:
            out.append({"kind": "para", "spans": parse_inline("\n".join(buf))})

    while i < n:
        raw = lines[i]
        line = TRAIL_RE.sub("", raw.rstrip())
        stripped = line.strip()
        if not stripped:
            emit_para()
            i += 1
            continue
        m = FENCE_RE.match(line)
        if m:
            emit_para()
            fc, c = m.group(1), m.group(1)[0]
            close_re = re.compile("^ {0,3}" + re.escape(c) +
                                  "{" + str(len(fc)) + ",}[ \t]*$")
            j, body = i + 1, []
            while j < n and not close_re.match(TRAIL_RE.sub("", lines[j].rstrip())):
                body.append(lines[j])
                j += 1
            out.append({"kind": "code", "lang": m.group(2).strip(),
                        "text": "\n".join(body).rstrip("\n")})
            i = j + 1
            continue
        if para and SETEXT_RE.match(line):
            raw_p = "\n".join(para).strip()
            if raw_p:
                del para[:]
                out.append(heading_item(doc, 1 if line.strip()[0] == "=" else 2,
                                        raw_p))
                i += 1
                continue
        m = ATX_RE.match(line)
        if m:
            emit_para()
            body = (m.group(2) or "").strip()
            body = body.rstrip(" #").strip() if body.endswith("#") else body
            am = re.search(r"\{#([\w\-:.]+)\}$", body)
            anchor = None
            if am:
                anchor = am.group(1)
                body = body[:am.start()].strip()
            out.append(heading_item(doc, len(m.group(1)), body, anchor))
            i += 1
            continue
        if HR_RE.match(line):
            emit_para()
            out.append({"kind": "hr"})
            i += 1
            continue
        m = QUOTE_RE.match(line)
        if m:
            emit_para()
            j, inner = i, []
            while j < n:
                qm = QUOTE_RE.match(TRAIL_RE.sub("", lines[j].rstrip()))
                if qm:
                    inner.append(qm.group(1))
                elif lines[j].strip() and not starts_block(lines[j]):
                    inner.append(lines[j].strip())
                else:
                    break
                j += 1
            sub = parse_blocks(inner, doc)
            for it in sub:
                it["quote"] = it.get("quote", 0) + 1
            out.extend(sub)
            i = j
            continue
        if LIST_RE.match(line):
            emit_para()
            block, j = collect_list_block(lines, i)
            out.extend(parse_list(block, doc))
            i = j
            continue
        tbl = is_table_start(lines, i)
        if tbl:
            emit_para()
            item, i = tbl
            out.append(item)
            continue
        if re.match(r"^ {0,3}<(?:!--|/?[a-zA-Z])", line):
            emit_para()
            j, body = i, []
            while j < n and lines[j].strip():
                body.append(lines[j])
                j += 1
            out.append({"kind": "htmlraw", "text": "\n".join(body)})
            i = j
            continue
        if stripped.startswith("|"):
            emit_para()
            out.append({"kind": "htmlraw", "text": line})
            i += 1
            continue
        para.append(line)
        i += 1
    emit_para()
    return out


def collect_list_block(lines, start):
    first = LIST_RE.match(TRAIL_RE.sub("", lines[start].rstrip()))
    base = len(first.group(1))
    need = base + len(first.group(2)) + len(first.group(3))
    block = []
    blanks = 0
    end = start
    for j in range(start, len(lines)):
        raw = lines[j]
        if not raw.strip():
            blanks += 1
            continue
        s = TRAIL_RE.sub("", raw.rstrip())
        ind = len(s) - len(s.lstrip(" "))
        m = LIST_RE.match(s)
        if ind < need and not (m and ind >= base):
            break
        if blanks and block:
            block.extend([""] * blanks)
        blanks = 0
        if m and ind >= base:
            need = ind + len(m.group(2)) + max(1, len(m.group(3)))
        block.append(s)
        end = j + 1
    return block, end


def parse_list(block, doc):
    items = []
    cur = None
    base = None
    pending_blank = False
    for line in block:
        if not line.strip():
            pending_blank = True
            continue
        ind = len(line) - len(line.lstrip(" "))
        m = LIST_RE.match(line)
        if m and (base is None or ind <= base):
            if base is None:
                base = ind
            marker = m.group(2)
            ordered = marker[-1] in ".)"
            cur = {"ordered": ordered,
                   "start": int(re.match(r"\d+", marker).group(0)) if ordered else 0,
                   "ci": ind + len(marker) + len(m.group(3)),
                   "lines": [m.group(4)],
                   "gap": bool(pending_blank and items)}
            items.append(cur)
        elif cur is not None:
            if pending_blank:
                cur["lines"].append("")
            cut = min(ind, cur["ci"])
            cur["lines"].append(line[cut:] if len(line) > cut else "")
        else:
            break
        pending_blank = False
    return flatten_list(items, doc, 0)


def flatten_list(items, doc, level):
    out = []
    loose = any(it["gap"] or any(l.strip() == "" for l in it["lines"])
                or len([l for l in it["lines"] if l.strip()]) > 1 for it in items)
    for it in items:
        lines = list(it["lines"])
        nonblank = [l for l in lines if l.strip()]
        if nonblank:
            dd = min(len(l) - len(l.lstrip(" ")) for l in nonblank)
            lines = [l[dd:] if l.strip() else "" for l in lines]
        checked = None
        for k, l in enumerate(lines):
            cm = re.match(r"^\[([ xX])\][ \t]+(.*)$", l)
            if cm:
                checked = cm.group(1).lower() == "x"
                lines[k] = cm.group(2)
            break
        marker = ("%d." % it["start"]) if it["ordered"] \
            else BULLETS[min(level, len(BULLETS) - 1)]
        first = True
        for s in parse_blocks(lines, doc):
            k = s["kind"]
            if k == "para" and first:
                out.append({"kind": "li", "level": level, "marker": marker,
                            "checked": checked, "loose": loose,
                            "spans": s["spans"], "quote": s.get("quote", 0)})
                first = False
            elif k == "li":
                s["level"] = level + 1
                s["loose"] = loose
                out.append(s)
                first = False
            elif k == "para":
                out.append({"kind": "gap", "level": level})
                out.append({"kind": "lipara", "level": level,
                            "spans": s["spans"], "quote": s.get("quote", 0)})
            else:
                if k == "code":
                    s["pad"] = level * 22
                out.append(s)
                first = False
        if first:
            out.append({"kind": "li", "level": level, "marker": marker,
                        "checked": checked, "loose": loose, "spans": []})
    return out


def parse_front_matter(lines):
    if not lines or TRAIL_RE.sub("", lines[0].strip()) != "---":
        return None, lines
    for i in range(1, min(len(lines), 150)):
        if TRAIL_RE.sub("", lines[i].strip()) in ("---", "..."):
            meta = []
            for ln in lines[1:i]:
                m = re.match(r"^([\w\u4e00-\u9fff\-]+):[ \t]*(.*)$", ln)
                if m:
                    meta.append((m.group(1), m.group(2).strip()))
                elif ln.strip():
                    meta.append(("", ln.strip()))
            return (meta or None), lines[i + 1:]
    return None, lines


def _renumber_footrefs(items, doc):
    def fix(spans):
        for s in spans:
            if s.get("nl") or "footref" not in s.get("styles", []):
                continue
            label = s.get("label", "")
            num = doc.footnums.get(label)
            s["text"] = "[%s]" % (num if num else label)
            if num:
                s["url"] = "#footnote-%s" % label
    for it in items:
        if it.get("spans"):
            fix(it["spans"])


def parse_document(text, doc=None):
    doc = doc or Doc()
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines = lines.split("\n")
    meta, lines = parse_front_matter(lines)
    lines, footdefs = collect_footnotes(lines, doc)
    items = parse_blocks(lines, doc)
    if meta:
        items.insert(0, {"kind": "meta", "meta": meta})
    if footdefs:
        items.append({"kind": "hr"})
        items.append(heading_item(doc, 2, "脚注", "footnotes"))
        items.extend(footdefs)
    _renumber_footrefs(items, doc)
    return items


# ---------------------------------------------------------------- 查看器 ---

def pick_family(names, wanted, default):
    low = [n.lower() for n in names]
    for w in wanted:
        for n in low:
            if w in n:
                return n
    return default


class Viewer:
    def __init__(self, root, targets=None):
        self.root = root
        self.path = None
        self.raw = ""
        self.enc = "utf-8"
        self.enc_list = ["utf-8", "gbk", "big5", "utf-16", "latin-1"]
        self.theme_name = "light"
        self.th = THEMES["light"]
        self.font_size = 11
        self.toc_visible = True
        self.wrap_on = True
        self.show_src = False
        self.width_px = 900
        self.render_ms = 0
        self.links = {}
        self._link_ids = {}
        self._link_n = 0
        self.anchors = {}
        self.headings = []
        self.iid_by_line = {}
        self.toc_map = {}
        self.toc_stack = []
        self.margin_tags = set()
        self.link_tags = set()
        self.photos = []
        self.photo_cache = {}
        self.cell_frames = []
        self.link_fonts = {}
        self.recent = []
        self.last_sync = 0.0
        self.sync_job = None
        self._cur_head = 0
        self.find_job = None
        self.hits = []
        self.hit_pos = -1
        self.doc_font = None

        self.files = self._expand_targets(targets or [])
        if self.files:
            self.path = self.files[0]
        self.settings_file = self._settings_path()
        self._load_settings()
        self._init_fonts()
        self._build_ui()
        self._apply_theme()
        if self.path:
            self.open_file(self.path)
        else:
            self._render(WELCOME_MD)
            self.status.set("点“打开”选择 .md 文件，或把文件拖到程序图标上；F1/“帮助”看快捷键")

    # ------------------------------------------------------------ 配置 ----
    @staticmethod
    def _expand_targets(targets):
        out = []
        for t in targets:
            t = os.path.abspath(t)
            if not os.path.exists(t) and any(ord(c) > 0xD7FF and
                                             ord(c) < 0xE000 for c in t):
                try:
                    t = os.path.abspath(os.fsencode(t).decode("mbcs"))
                except (UnicodeDecodeError, UnicodeEncodeError, OSError):
                    pass
            if os.path.isdir(t):
                try:
                    out += [os.path.join(t, f) for f in sorted(os.listdir(t))
                            if f.lower().endswith(MD_EXTS)]
                except OSError:
                    pass
            elif os.path.isfile(t):
                out.append(t)
        return out

    def _settings_path(self):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "mdview")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = os.path.expanduser("~")
        return os.path.join(d, "settings.json")

    def _load_settings(self):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        for k in ("font_size", "theme_name", "toc_visible", "wrap_on", "show_src"):
            if k in d and isinstance(d[k], type(getattr(self, k))):
                setattr(self, k, d[k])
        self.recent = [r for r in d.get("recent", [])
                       if isinstance(r, str) and os.path.isfile(r)]
        if not self.path and self.recent:
            self.path = self.recent[0]

    def _save_settings(self):
        data = {"font_size": self.font_size, "theme_name": self.theme_name,
                "toc_visible": self.toc_visible, "wrap_on": self.wrap_on,
                "show_src": self.show_src, "recent": self.recent[:15]}
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ------------------------------------------------------------ 字体 ----
    def _init_fonts(self):
        fams = list(tkfont.families(self.root))
        self.ui = pick_family(fams, UI_FAMILIES, "TkDefaultFont")
        self.mono = pick_family(fams, MONO_FAMILIES, "Courier New")
        f = tkfont.nametofont("TkDefaultFont")
        if self.ui != "TkDefaultFont":
            f.configure(family=self.ui, size=9)
        self.f_body = tkfont.Font(family=self.ui, size=self.font_size)

    def _rebuild_fonts(self):
        sz = self.font_size
        F = tkfont.Font
        self.f_body.configure(family=self.ui, size=sz)
        self.f_mono = F(family=self.mono, size=sz)
        self.f_small = F(family=self.ui, size=max(sz - 2, 7))
        self.f_mono_small = F(family=self.mono, size=max(sz - 1, 7))
        self.f_h = {lv: F(family=self.ui, size=max(HEADING_SIZES[lv - 1]
                                                   + sz - 11, 9), weight="bold")
                    for lv in range(1, 7)}
        self.f_mh = {lv: F(family=self.mono, size=max(HEADING_SIZES[lv - 1]
                                                      + sz - 11, 9), weight="bold")
                     for lv in range(1, 7)}
        self.f_variants = {
            (): self.f_body, ("bold",): F(family=self.ui, size=sz, weight="bold"),
            ("em",): F(family=self.ui, size=sz, slant="italic"),
            ("bold", "em"): F(family=self.ui, size=sz, weight="bold", slant="italic"),
        }
        self.f_mono_variants = {
            (): self.f_mono, ("bold",): F(family=self.mono, size=sz, weight="bold"),
            ("em",): F(family=self.mono, size=sz, slant="italic"),
            ("bold", "em"): F(family=self.mono, size=sz, weight="bold",
                              slant="italic"),
        }
        self.link_fonts = {}
        self.f_link_body = F(family=self.ui, size=sz, underline=1)
        self.f_link_mono = F(family=self.mono, size=sz, underline=1)
        self.cell_fonts = {
            "body": F(family=self.ui, size=sz),
            "bodyb": F(family=self.ui, size=sz, weight="bold"),
            "mono": F(family=self.mono, size=sz),
            "monob": F(family=self.mono, size=sz, weight="bold"),
        }

    def _link_font(self, key):
        f = self.link_fonts.get(key)
        if f is None:
            fam, sz, styles = key
            f = tkfont.Font(family=fam, size=sz, underline=1,
                            weight="bold" if "bold" in styles else "normal",
                            slant="italic" if "em" in styles else "roman")
            self.link_fonts[key] = f
        return f

    # ------------------------------------------------------------ 界面 ----
    def _build_ui(self):
        root = self.root
        root.title("Markdown 查看器")
        root.geometry("1180x780")
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        bar = tk.Frame(root)
        self.toolbar = bar
        bar.pack(fill="x")
        self.btns = {}

        def btn(key, text, cmd, tip=""):
            b = tk.Button(bar, text=text, command=cmd, relief="flat", padx=9,
                          pady=2, cursor="hand2", font=("Segoe UI", 9))
            b.pack(side="left", padx=1)
            self.btns[key] = b
            if tip:
                _bind_tip(self, b, tip)
            return b

        def sep():
            self.seps.append(tk.Frame(bar, width=1, height=20))
            self.seps[-1].pack(side="left", padx=6, fill="y")

        self.seps = []
        btn("open", "打开", self.open_dialog, "Ctrl+O")
        btn("prev", "上一份", lambda: self.step_file(-1), "Alt+左方向键")
        btn("next", "下一份", lambda: self.step_file(1), "Alt+右方向键")
        btn("recent", "最近", self._recent_menu, "最近打开过的文件")
        sep()
        btn("reload", "重载", lambda: self.reload(True), "F5，重新读取并按内容判断编码")
        btn("enc", "编码", self._cycle_enc, "手动切换编码，当前编码显示在下方状态栏")
        sep()
        btn("toc", "目录", self._toggle_toc, "显示或隐藏左侧目录")
        btn("find", "查找", self.show_find, "Ctrl+F")
        btn("wrap", "换行", self._toggle_wrap, "长行是否自动换行")
        btn("src", "源码", self._toggle_src, "显示原始 Markdown 文本")
        sep()
        btn("minus", "字号-", lambda: self.change_font(-1), "Ctrl+-")
        btn("plus", "字号+", lambda: self.change_font(1), "Ctrl++")
        btn("theme", "主题", self._toggle_theme, "浅色/深色切换")
        btn("copy", "复制", self._copy_all, "复制全文到剪贴板")
        btn("edit", "编辑", self._open_editor, "用 VS Code / 记事本打开当前文件")
        sep()
        btn("help", "帮助", self._help, "F1")
        self.file_lbl = tk.Label(bar, anchor="w")
        self.file_lbl.pack(side="left", padx=12)
        root.bind("<F1>", lambda e: self._help())

        find = tk.Frame(root)
        self.findbar = find
        self.find_var = tk.StringVar()
        self.case_var = tk.BooleanVar(value=False)
        self.find_entry = tk.Entry(find, textvariable=self.find_var, width=32)
        self.find_entry.pack(side="left", padx=(10, 4), pady=4)
        self.find_case = tk.Checkbutton(find, text="区分大小写",
                                        variable=self.case_var,
                                        command=self.do_find)
        self.find_case.pack(side="left", padx=4)
        self.find_prev = tk.Button(find, text="上一个",
                                   command=lambda: self.goto_hit(-1))
        self.find_prev.pack(side="left", padx=2)
        self.find_next = tk.Button(find, text="下一个",
                                   command=lambda: self.goto_hit(1))
        self.find_next.pack(side="left", padx=2)
        self.find_lbl = tk.Label(find)
        self.find_lbl.pack(side="left", padx=8)
        tk.Button(find, text="关闭", command=self.hide_find).pack(side="left",
                                                                  padx=6)
        self.find_entry.bind("<Return>", lambda e: self.goto_hit(1))
        self.find_entry.bind("<KP_Enter>", lambda e: self.goto_hit(1))
        self.find_entry.bind("<Shift-Return>", lambda e: self.goto_hit(-1))
        self.find_entry.bind("<KeyRelease>", self._on_find_key)

        body = tk.Frame(root)
        self.body = body
        body.pack(fill="both", expand=True)
        self.pane = tk.PanedWindow(body, orient="horizontal", sashwidth=6,
                                   bd=0, sashpad=2, opaqueresize=True)
        self.pane.pack(fill="both", expand=True)

        right = tk.Frame(self.pane)
        self.text = tk.Text(right, wrap="word", undo=False, padx=18, pady=10,
                            bd=0, highlightthickness=0, insertwidth=1,
                            font=str(self.f_body), takefocus=1)
        self.ysb = ttk.Scrollbar(right, orient="vertical", command=self._yview)
        self.xsb = ttk.Scrollbar(right, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self._yview_set,
                            xscrollcommand=self.xsb.set)
        self.xsb.pack(side="bottom", fill="x")
        self.ysb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.pane.add(right, stretch="always")

        left = tk.Frame(self.pane)
        self.toc_frame = left
        self.toc_title = tk.Label(left, text="目录", anchor="w")
        self.toc_title.pack(fill="x", padx=8, pady=(8, 2))
        self.toc = ttk.Treeview(left, show="tree", selectmode="browse",
                                height=20)
        self.toc.column("#0", width=236, stretch=True)
        tkb = ttk.Scrollbar(left, orient="vertical", command=self.toc.yview)
        self.toc.configure(yscrollcommand=tkb.set)
        self.toc.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        tkb.pack(side="right", fill="y", pady=4)
        self.toc.bind("<<TreeviewSelect>>", self._toc_click)
        self.pane.add(left, width=252, stretch="never")

        self.status = tk.StringVar()
        self.status_bar = tk.Label(root, textvariable=self.status, anchor="w",
                                   font=("Segoe UI", 9), padx=10, pady=4)
        self.status_bar.pack(fill="x")
        root.minsize(520, 320)
        root.bind("<Destroy>", self._on_destroy, add="+")

        t = self.text
        t.bind("<Button-1>", self._on_click)
        t.bind("<Motion>", self._on_motion)
        t.bind("<Leave>", lambda e: self._hide_tip())
        t.bind("<MouseWheel>", self._on_wheel)
        t.bind("<Control-MouseWheel>", self._on_zoom)
        t.bind("<Control-c>", self._on_copy)
        t.bind("<Control-C>", self._on_copy)
        t.bind("<Configure>", self._on_resize)
        t.configure(state="disabled")
        r = root
        r.bind_all("<Control-o>", lambda e: self.open_dialog())
        r.bind_all("<Control-f>", lambda e: self.show_find())
        r.bind_all("<Escape>", self._on_escape)
        r.bind_all("<Control-w>", lambda e: self.root.destroy())
        r.bind_all("<F5>", lambda e: self.reload(True))
        r.bind_all("<F11>", lambda e: self._toggle_full())
        for key in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            r.bind_all(key, lambda e: self.change_font(1))
        for key in ("<Control-minus>", "<Control-KP_Subtract>"):
            r.bind_all(key, lambda e: self.change_font(-1))
        r.bind_all("<Alt-Left>", lambda e: self.step_file(-1))
        r.bind_all("<Alt-Right>", lambda e: self.step_file(1))
        r.bind_all("<Prior>", lambda e: self._page(-1))
        r.bind_all("<Next>", lambda e: self._page(1))
        r.bind_all("<MouseWheel>", self._on_wheel)
        r.bind_all("<Control-MouseWheel>", self._on_zoom)

    # ------------------------------------------------------------ 主题 ----
    def _apply_theme(self):
        self.th = THEMES[self.theme_name]
        th = self.th
        self.root.configure(bg=th["bg"])
        self.toolbar.configure(bg=th["bg"])
        self.findbar.configure(bg=th["panel"])
        self.body.configure(bg=th["bg"])
        self.pane.configure(bg=th["border"])
        self.text.configure(bg=th["bg"], fg=th["fg"], insertbackground=th["fg"],
                            selectbackground=th["sel"], takefocus=1)
        self.status_bar.configure(bg=th["panel"], fg=th["muted"])
        self.file_lbl.configure(fg=th["muted"])
        self.toc_title.configure(bg=th["toc_bg"], fg=th["fg"])
        self.toc_frame.configure(bg=th["toc_bg"])
        for child in self.toc_frame.winfo_children():
            try:
                child.configure(bg=th["toc_bg"])
            except tk.TclError:
                pass
        for b in list(self.btns.values()) + [self.find_prev, self.find_next,
                                             self.find_case, self.find_entry]:
            try:
                b.configure(bg=th["btn_bg"] if isinstance(b, tk.Button) else th["bg"],
                            fg=th["fg"], activebackground=th["btn_hl"],
                            activeforeground=th["fg"])
            except tk.TclError:
                pass
        self.find_lbl.configure(bg=th["panel"], fg=th["muted"])
        for f in self.seps:
            f.configure(bg=th["border"])
        s = self.style
        s.configure("Treeview", background=th["toc_bg"],
                    fieldbackground=th["toc_bg"], foreground=th["fg"],
                    borderwidth=0, relief="flat", font=("Segoe UI", 9),
                    rowheight=22)
        s.map("Treeview", background=[("selected", th["toc_sel"])],
              foreground=[("selected", th["fg"])])
        s.configure("TFrame", background=th["bg"])
        s.configure("TScrollbar", background=th["panel"], troughcolor=th["bg"],
                    bordercolor=th["border"], lightcolor=th["panel"],
                    darkcolor=th["panel"], arrowcolor=th["muted"])
        s.map("TScrollbar", background=[("active", th["btn_hl"])])
        self.btns["src"].configure(text="渲染" if self.show_src else "源码")
        self._configure_tags()

    def _configure_tags(self):
        t = self.text
        th = self.th
        self._rebuild_fonts()
        t.tag_configure("para", spacing1=5, spacing2=1, spacing3=5)
        t.tag_configure("blank", spacing1=6, spacing3=0)
        t.tag_configure("code", background=th["block_bg"], foreground=th["fg"],
                        font=str(self.f_mono), lmargin1=16, lmargin2=16,
                        rmargin=16, spacing1=0, spacing2=0, spacing3=0)
        t.tag_configure("codecap", background=th["block_bg"],
                        foreground=th["muted"], font=str(self.f_small),
                        lmargin1=16, rmargin=16, spacing1=8, spacing2=0,
                        spacing3=0)
        t.tag_configure("codeend", spacing3=12)
        t.tag_configure("codehl", foreground=th["code_fg"])
        t.tag_configure("costr", foreground=th["code_str"])
        t.tag_configure("codecom", foreground=th["code_com"],
                        font=str(self.f_mono_variants[("em",)]))
        t.tag_configure("inlinecode", background=th["code_bg"],
                        foreground=th["code_fg"], font=str(self.f_mono))
        t.tag_configure("codebg", background=th["code_bg"])
        t.tag_configure("quotebg", background=th["quote_bg"])
        t.tag_configure("qtext", foreground=th["qtext"])
        t.tag_configure("li", spacing1=1, spacing2=1, spacing3=1)
        t.tag_configure("liloose", spacing3=7)
        t.tag_configure("lipara", spacing1=3, spacing2=1, spacing3=3)
        t.tag_configure("hr", foreground=th["border"], spacing1=10, spacing3=10)
        t.tag_configure("meta", foreground=th["muted"], font=str(self.f_small),
                        spacing1=4, spacing3=8)
        t.tag_configure("htmlraw", foreground=th["muted"],
                        font=str(self.f_mono_small), background=th["panel"],
                        spacing1=4, spacing3=6)
        t.tag_configure("muted", foreground=th["muted"])
        t.tag_configure("done", foreground=th["done"], overstrike=1)
        t.tag_configure("check", foreground=th["accent"])
        t.tag_configure("strike", overstrike=1, foreground=th["muted"])
        t.tag_configure("mark", background=th["mark"])
        t.tag_configure("imgalt", foreground=th["muted"], font=str(self.f_small),
                        spacing1=2, spacing3=8)
        t.tag_configure("footdef", spacing1=2, spacing2=1, spacing3=2)
        t.tag_configure("target", background=th["found"])
        t.tag_configure("found", background=th["found"])
        t.tag_configure("found_cur", background=th["found_cur"])
        for lv in range(1, 7):
            t.tag_configure("h%d" % lv, font=str(self.f_h[lv]),
                            foreground=th["heading"],
                            spacing1=max(22 - lv * 3, 6), spacing2=1,
                            spacing3=max(10 - lv, 3))
        for key, f in self.f_variants.items():
            t.tag_configure("f_" + "".join(key), font=str(f))
        for key, f in self.f_mono_variants.items():
            t.tag_configure("mf_" + "".join(key), font=str(f))
        self._raise_tags()

    PRIORITY = ["hr", "para", "li", "lipara", "footdef", "meta", "imgalt",
                "htmlraw", "codecap", "code", "codehl", "costr", "codecom",
                "done", "strike", "qtext",
                "quotebg", "mark", "liloose", "codeend", "blank",
                "inlinecode", "codebg"]
    PRIORITY_HI = ["f_boldem", "f_bold", "f_em", "mf_boldem", "mf_bold",
                   "mf_em", "check", "muted", "target", "found", "found_cur"]

    def _raise_tags(self):
        t = self.text
        for lv in range(6, 0, -1):
            t.tag_raise("h%d" % lv)
        for name in self.PRIORITY:
            t.tag_raise(name)
        for name in self.margin_tags:
            t.tag_raise(name)
        for name in self.PRIORITY_HI:
            t.tag_raise(name)
        for name in sorted(self.link_tags):
            t.tag_raise(name)
        for name in ("target", "found", "found_cur"):
            t.tag_raise(name)

    # ------------------------------------------------------------ 打开 ----
    def open_dialog(self):
        p = filedialog.askopenfilename(
            parent=self.root, title="打开 Markdown 文件",
            initialdir=os.path.dirname(self.path) if self.path
            else os.path.expanduser("~"),
            filetypes=[("Markdown", "*.md *.markdown *.mdown *.mkd"),
                       ("文本", "*.txt"), ("所有文件", "*.*")])
        if p:
            self.open_file(p)

    def open_file(self, path):
        path = os.path.normpath(os.path.abspath(path))
        if not os.path.isfile(path):
            self.status.set("文件不存在：" + path)
            return False
        self.path = path
        self.recent = [path] + [r for r in self.recent
                                if os.path.normcase(r) != os.path.normcase(path)]
        self.recent = self.recent[:15]
        self._save_settings()
        return self.reload(True)

    def reload(self, sniff=False):
        if not self.path:
            return False
        try:
            with open(self.path, "rb") as f:
                data = f.read()
        except OSError as e:
            self.status.set("读取失败：%s" % e)
            return False
        enc = None
        if sniff:
            enc, text = self._sniff(data)
            if text is None:
                self.status.set("无法识别编码，可点“编码”手动切换")
                return False
        else:
            try:
                text = data.decode(self.enc)
            except (UnicodeDecodeError, LookupError):
                text = data.decode("utf-8", "replace")
                enc = "utf-8"
        if enc:
            self.enc = enc
        if data.startswith(b"\xef\xbb\xbf"):
            self.enc = "utf-8-sig"
        ok = self._render(text)
        if not ok:
            return False
        base = os.path.basename(self.path)
        self.file_lbl.configure(text=base)
        self.root.title("%s - Markdown 查看器" % base)
        self.status.set("%s    %s    %s    %d 行    渲染 %d ms"
                        % (self.path, _human(len(data)), self.enc,
                           text.count("\n") + 1, self.render_ms))
        return True

    @staticmethod
    def _sniff(data):
        if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
            return "utf-16", data.decode("utf-16", "replace")
        probe = data[:400000]
        best = None
        for enc in ("utf-8", "gbk", "big5"):
            try:
                probe.decode(enc)
                ok = True
            except UnicodeDecodeError:
                ok = False
            try:
                text = data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                if ok:
                    continue
                text = None
            if text is None:
                continue
            cn = sum(1 for c in text[:8000] if "\u4e00" <= c <= "\u9fff")
            rep = text.count("\ufffd")
            score = cn * 3 - rep * 800 + (100 if ok else -100000)
            if best is None or score > best[0]:
                best = (score, enc, text)
        if best:
            return best[1], best[2]
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return enc, data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        try:
            return "utf-8", data.decode("utf-8", "replace")
        except LookupError:
            return None, None

    def _cycle_enc(self):
        if not self.raw:
            return
        try:
            k = self.enc_list.index(self.enc.replace("-sig", ""))
        except ValueError:
            k = 0
        self.enc = self.enc_list[(k + 1) % len(self.enc_list)]
        if self.path:
            with open(self.path, "rb") as f:
                data = f.read()
            try:
                text = data.decode(self.enc)
            except (UnicodeDecodeError, LookupError):
                text = data.decode(self.enc, "replace")
            self._render(text)
            self.status.set("编码：%s（如果中文仍是乱码，继续点“编码”切换）" % self.enc)

    def step_file(self, d):
        files = self.files if len(self.files) > 1 else []
        if not files and self.path:
            files = self._expand_targets([os.path.dirname(self.path)])
        if not files:
            return
        try:
            k = [os.path.normcase(f) for f in files].index(
                os.path.normcase(self.path))
        except ValueError:
            k = 0
        self.open_file(files[(k + d) % len(files)])

    # ------------------------------------------------------------ 渲染 ----
    def _render(self, text):
        self.raw = text
        t0 = time.time()
        if self.show_src:
            items = [{"kind": "src", "text": text}]
        else:
            items = parse_document(text)
        for w in self.text.winfo_children():
            try:
                w.destroy()
            except tk.TclError:
                pass
        self.cell_frames = []
        t = self.text
        if not self.show_src:
            t.configure(wrap="word" if self.wrap_on else "none")
        t.configure(state="normal")
        t.delete("1.0", "end")
        self.links = {}
        self.anchors = {}
        self.headings = []
        self._cur_head = 0
        self.iid_by_line = {}
        self.toc_map = {}
        self.toc_stack = []
        self.margin_tags = set()
        for name in list(self.link_tags):
            try:
                t.tag_delete(name)
            except tk.TclError:
                pass
        self.link_tags = set()
        self._link_ids = {}
        self._link_n = 0
        self.toc.delete(*self.toc.get_children())
        self.photos = []
        if not items:
            t.insert("end", "（空文件）\n", ("muted",))
        for it in items:
            getattr(self, "_d_" + it["kind"])(it)
        t.configure(state="disabled")
        t.see("1.0")
        t.xview_moveto(0)
        self.render_ms = int((time.time() - t0) * 1000)
        self._raise_tags()
        self._set_toc_visible()
        return True

    def _line(self):
        return int(self.text.index("end-1c").split(".")[0])

    def _lm_tag(self, lm, lm1=None):
        lm = int(lm // LM_STEP * LM_STEP)
        lm1 = lm if lm1 is None else int(lm1 // LM_STEP * LM_STEP)
        name = "lm%d" % lm if lm1 == lm else "lm%d_%d" % (lm1, lm)
        if name not in self.margin_tags:
            self.text.tag_configure(name, lmargin1=lm1, lmargin2=lm)
            self.margin_tags.add(name)
        return name

    def _qt(self, it, extra=0, base=0):
        q = it.get("quote", 0)
        tags = []
        if q:
            tags += ["qtext", "quotebg"]
        return base + q * QUOTE_LM + extra, tags

    def _span_tags(self, s, style_tags, mode="body"):
        text = s["text"]
        tags = list(style_tags)
        styles = s.get("styles", [])
        url = s.get("url", "")
        mono = "inlinecode" in styles
        for x in styles:
            if x == "link":
                tags.append(self._link_tag(url, styles, mode))
            elif x == "code":
                tags.append("inlinecode" if mode != "heading" else "codebg")
            elif x == "bold" and mode != "heading":
                tags.append(("mf_" if mono else "f_") + "bold")
            elif x == "em" and mode != "heading":
                tags.append(("mf_" if mono else "f_") + "em")
            elif x in ("strike", "mark"):
                tags.append(x)
            elif x == "footref":
                u = s.get("url") or ("#%s" % s.get("label", ""))
                tags.append(self._link_tag(u, styles, mode))
            elif x == "img":
                tags.append("muted")
        return text, tags

    def _insert_spans(self, spans, style_tags, mode="body", eol="\n",
                     on_image=None):
        t = self.text
        for s in spans:
            if s.get("nl"):
                t.insert("end", "\n", tuple(style_tags))
                continue
            text, tags = self._span_tags(s, style_tags, mode)
            if "img" in s.get("styles", []):
                if on_image:
                    on_image(s, tags)
                else:
                    t.insert("end", " " + text + " ", tuple(tags))
                continue
            if text:
                t.insert("end", text, tuple(tags))
        if eol:
            t.insert("end", eol, tuple(style_tags))

    def _link_tag(self, url, styles=(), mode="body"):
        key = "%s|%s|%s|%s" % (url, "|".join(sorted(styles)), mode,
                               self.font_size)
        done = self._link_ids.get(key)
        if done:
            return done
        self._link_n += 1
        name = "L%d" % self._link_n
        fam = self.mono if ("inlinecode" in styles or mode == "mono") else self.ui
        f = self._link_font((fam, self.font_size, tuple(styles)))
        kw = {"foreground": self.th["link"], "underline": 1}
        if mode != "heading":
            kw["font"] = str(f)
        self.text.tag_configure(name, **kw)
        self.links[name] = url
        self._link_ids[key] = name
        self.link_tags.add(name)
        return name

    def _d_para(self, it):
        lm, qtags = self._qt(it)
        style = ["para"] + qtags + [self._lm_tag(lm)]
        self._insert_spans(it["spans"], style)

    def _d_heading(self, it):
        t = self.text
        lv = min(it["level"], 6)
        lm, qtags = self._qt(it)
        style = ["h%d" % lv] + qtags + [self._lm_tag(lm)]
        line = self._line()
        self._insert_spans(it["spans"], style, mode="heading")
        self.anchors[it["anchor"]] = line
        self.headings.append((line, lv, it["title"], it["anchor"]))
        while self.toc_stack and self.toc_stack[-1][0] >= lv:
            self.toc_stack.pop()
        parent = self.toc_stack[-1][1] if self.toc_stack else ""
        iid = self.toc.insert(parent, "end", text=it["title"] or "（无标题）",
                              tags=("lv%d" % lv,), open=True)
        self.toc_stack.append((lv, iid))
        self.toc_map[iid] = line
        self.iid_by_line[line] = iid

    def _d_code(self, it):
        t = self.text
        lm, qtags = self._qt(it, it.get("pad", 0), base=0)
        tags = ["code"] + qtags + [self._lm_tag(lm + 16)]
        if it.get("lang"):
            cap = [x.strip() for x in re.split(r"[,\[\]{}=]", it["lang"]) if x]
            t.insert("end", " ".join(cap) + "\n",
                     tuple(["codecap"] + qtags + [self._lm_tag(lm + 16)]))
        keys = _lang_keys(it.get("lang", ""))
        lines = it["text"].split("\n")
        for k, ln in enumerate(lines):
            last = k == len(lines) - 1
            row = tags + (["codeend"] if last else [])
            for txt, tg in _split_code(ln, keys):
                t.insert("end", txt, tuple(row + ([tg] if tg else [])))
            t.insert("end", "\n", tuple(row))

    def _pad_to(self, text, width):
        f = self.f_body
        unit = max(f.measure(" ") , 1)
        pad = width - f.measure(text)
        return text + " " * max(0, int(round(pad / unit)))

    def _d_li(self, it):
        t = self.text
        lm, qtags = self._qt(it, 16 + it.get("level", 0) * 22)
        marker = it.get("marker", "\u2022")
        checked = it.get("checked")
        text_w = max(self.f_body.measure(marker) + 12, 22)
        tab = lm + text_w
        tname = self._lm_tag(tab, lm)
        style = ["li"] + qtags + [tname] + (["liloose"] if it.get("loose")
                                            else [])
        if checked is None:
            t.insert("end", self._pad_to(marker, text_w),
                     tuple(style + ["muted"]))
        else:
            glyph = CHECK_ON if checked else CHECK_OFF
            t.insert("end", self._pad_to(glyph, text_w), tuple(style + ["check"]))
        spans = it["spans"]
        if checked:
            spans = [dict(s, styles=s.get("styles", []) + ["done"])
                     for s in spans]
        self._insert_spans(spans, style)

    def _d_lipara(self, it):
        lm, qtags = self._qt(it, 16 + it.get("level", 0) * 22 + 22)
        style = ["lipara", self._lm_tag(lm)] + qtags
        self._insert_spans(it["spans"], style)

    def _d_gap(self, it):
        self.text.insert("end", "\n", ("blank",))

    def _d_hr(self, it):
        lm, qtags = self._qt(it, 16)
        unit = max(self.f_body.measure("—"), 1)
        n = max(int((self.width_px - lm) / unit), 8)
        self.text.insert("end", "\u2014" * n + "\n",
                         tuple(["hr"] + qtags + [self._lm_tag(lm)]))

    def _d_meta(self, it):
        t = self.text
        lm, qtags = self._qt(it, 16)
        style = ["meta"] + qtags + [self._lm_tag(lm)]
        t.insert("end", "-" * 24 + " 文档信息\n", tuple(style))
        for k, v in it["meta"]:
            t.insert("end", ("    %s：%s" % (k, v) if k else "    " + v) + "\n",
                     tuple(style))

    def _d_htmlraw(self, it):
        t = self.text
        lm, qtags = self._qt(it, 16)
        style = ["htmlraw"] + qtags + [self._lm_tag(lm)]
        for ln in it["text"].split("\n"):
            t.insert("end", ln + "\n", tuple(style))

    def _d_src(self, it):
        t = self.text
        t.configure(wrap="none")
        for ln in it["text"].split("\n"):
            t.insert("end", ln + "\n", ("code",))

    def _d_footdef(self, it):
        t = self.text
        lm, qtags = self._qt(it, 34)
        text_w = 26
        style = ["footdef"] + qtags + [self._lm_tag(lm + text_w, lm)]
        self.anchors["footnote-%s" % it["label"]] = self._line()
        t.insert("end", self._pad_to("%d." % it["num"], text_w),
                 tuple(style + ["muted"]))
        self._insert_spans(it["spans"], style)

    def _d_image(self, it):
        t = self.text
        lm, qtags = self._qt(it, 8)
        path = self._media_path(it["url"])
        photo = self._photo(path)
        if photo:
            lbl = tk.Label(t, image=photo, bd=0, bg=self.th["bg"],
                           highlightthickness=1, cursor="hand2",
                           highlightbackground=self.th["border"])
            lbl.image = photo
            lbl._url = it["url"]
            lbl.bind("<Button-1>",
                     lambda ev, u=it["url"]: self._open_url(u))
            lbl.bind("<Enter>", lambda ev, u=it["url"]: self._tip(u))
            lbl.bind("<Leave>", self._hide_tip)
            self.photos.append(lbl)
            t.window_create("end", window=lbl, padx=0, pady=6)
            t.insert("end", "\n", ("blank", self._lm_tag(lm)))
            if it["alt"]:
                t.insert("end", it["alt"] + "\n", ("imgalt",))
        else:
            why = "（JPG/WEBP 需要 Pillow 支持，当前未安装）" if photo is False \
                else ("（文件不存在）" if path else "（地址无法解析）")
            self._insert_spans([span("图片 %s %s" % (it["url"], why))],
                               ["imgalt"] + qtags + [self._lm_tag(lm)])

    def _media_path(self, url):
        url = (url or "").strip().replace("\\", "/")
        if not url or url.startswith(("#", "mailto:", "data:")):
            return None
        if re.match(r"^[a-zA-Z]:", url) or url.startswith("//"):
            p = url
        elif url.startswith("file:"):
            from urllib.parse import urlparse
            from urllib.request import url2pathname
            p = url2pathname(urlparse(url).path)
        elif self.path:
            p = os.path.join(os.path.dirname(self.path), url)
        else:
            p = url
        return os.path.normpath(p)

    def _photo(self, path):
        if not path:
            return None
        key = (path, self.width_px, self.font_size)
        if key in self.photo_cache:
            return self.photo_cache[key]
        if not os.path.isfile(path):
            self.photo_cache[key] = None
            return None
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".jpg", ".jpeg", ".webp"):
                try:
                    from PIL import Image, ImageTk
                except ImportError:
                    self.photo_cache[key] = False
                    return False
                im = Image.open(path)
                maxw = max(self.width_px - 70, 200)
                if im.width > maxw:
                    im = im.resize((maxw, int(im.height * maxw / im.width)))
                photo = ImageTk.PhotoImage(im)
            else:
                photo = tk.PhotoImage(file=path)
                maxw = max(self.width_px - 70, 200)
                if photo.width() > maxw:
                    factor = max(1, int(photo.width() / maxw))
                    photo = photo.subsample(factor, factor)
                if photo.height() > 1400:
                    factor = max(1, int(photo.height() / 1200))
                    photo = photo.subsample(factor, factor)
        except tk.TclError:
            self.photo_cache[key] = None
            return None
        except Exception:
            self.photo_cache[key] = False
            return False
        self.photos.append(photo)
        self.photo_cache[key] = photo
        return photo

    # ------------------------------------------------------------ 表格 ----
    def _d_table(self, it):
        t = self.text
        head, rows, aligns = it["head"], it["rows"], it["aligns"]
        lm, qtags = self._qt(it, 16)
        avail = max(self.width_px - lm - 8, 120)
        if avail < (len(aligns) + 1) * 64:
            self._table_as_text(it, lm, qtags)
            return
        widths = self._col_widths(head, rows, len(aligns), avail)
        anchor = {"left": "w", "center": "center", "right": "e"}
        frame = tk.Frame(t, bg=self.th["border"])
        rows_all = [(True, 0, head)] + [(False, i + 1, r)
                                        for i, r in enumerate(rows)]
        for is_head, off, row in rows_all:
            for ci in range(len(aligns)):
                content = row[ci] if ci < len(row) else ""
                sp = [x for x in parse_inline(content) if not x.get("nl")]
                text = plain(sp)
                styles = sp[0]["styles"] if sp and text == sp[0]["text"] else []
                mono = "code" in styles
                if "\n" in text:
                    text = text.replace("\n", " ")
                fkey = ("mono" if mono else "body") + ("b" if is_head or
                                                       "bold" in styles else "")
                bg = (self.th["th"] if is_head else
                      (self.th["stripe"] if off % 2 else self.th["cell"]))
                lbl = tk.Label(frame, text=text, font=str(self.cell_fonts[fkey]),
                               bg=bg, fg=self.th["done"] if "strike" in styles
                               else self.th["fg"], anchor=anchor[aligns[ci]],
                               justify={"left": "left", "center": "center",
                                       "right": "right"}[aligns[ci]],
                               padx=8, pady=4, bd=1, relief="solid",
                               wraplength=max(widths[ci] - CELL_PAD, 24))
                lbl.grid(row=off, column=ci, sticky="nsew")
                if mono:
                    lbl.configure(highlightthickness=0)
        for ci, w in enumerate(widths):
            frame.columnconfigure(ci, minsize=w, weight=0)
        frame.pack_forget()
        t.window_create("end", window=frame, padx=0, pady=8)
        t.insert("end", "\n", ("blank", self._lm_tag(lm)))
        self.cell_frames.append(frame)

    def _col_widths(self, head, rows, ncols, avail):
        f = self.cell_fonts["body"]
        fb = self.cell_fonts["bodyb"]
        nat = [max(fb.measure(c), f.measure(c)) for c in head[:ncols]]
        nat += [0] * (ncols - len(nat))
        for r in rows:
            for ci in range(min(ncols, len(r))):
                txt = plain([s for s in parse_inline(r[ci]) if not s.get("nl")])
                nat[ci] = max(nat[ci], f.measure(txt), fb.measure(txt))
        need = [n + CELL_PAD for n in nat]
        if sum(need) <= avail:
            return need
        out = [0] * ncols
        active = list(range(ncols))
        budget = avail
        while active:
            share = budget / float(len(active))
            keep = [c for c in active if need[c] <= share]
            if not keep:
                for c in active:
                    out[c] = max(int(share), 44)
                break
            for c in keep:
                out[c] = need[c]
                budget -= need[c]
            active = [c for c in active if c not in keep]
        return out

    def _table_as_text(self, it, lm, qtags):
        t = self.text
        style = ["para"] + qtags + [self._lm_tag(lm)]
        rows = [it["head"]] + it["rows"]
        for ri, row in enumerate(rows):
            cells = [plain([s for s in parse_inline(c) if not s.get("nl")])
                     for c in row]
            t.insert("end", "  ".join(cells) + "\n",
                     tuple(style + (["f_bold"] if ri == 0 else [])))
            if ri == 0:
                t.insert("end", "-" * 40 + "\n", tuple([self._lm_tag(lm),
                                                        "muted"]))

    # ------------------------------------------------------------ 交互 ----
    def _tag_at(self, ev):
        try:
            idx = self.text.index("@%d,%d" % (ev.x, ev.y))
        except (tk.TclError, ValueError):
            return None
        for tg in self.text.tag_names(idx):
            if tg in self.links:
                return tg
        return None

    def _on_click(self, ev):
        tag = self._tag_at(ev)
        if not tag:
            return
        self._open_url(self.links[tag])

    def _open_url(self, url):
        if not url:
            return
        if url.startswith("#"):
            name = url[1:]
            line = self.anchors.get(name)
            if line is None:
                self.status.set("文内没有这个锚点：" + name)
                return
            self.jump_line(line)
            return
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", url):
            try:
                webbrowser.open(url)
                self.status.set("已用默认浏览器打开：" + url)
            except Exception as e:
                self.status.set("打开失败：%s" % e)
            return
        p = self._media_path(url)
        if p and os.path.exists(p):
            try:
                os.startfile(p)
                self.status.set("已打开：" + p)
            except OSError as e:
                self.status.set("打开失败：%s" % e)
        else:
            self.status.set("找不到：" + (p or url))

    def _set_cursor(self, name):
        if getattr(self, "_cursor", None) == name:
            return
        self._cursor = name
        try:
            self.text.configure(cursor=name)
        except tk.TclError:
            pass

    def _on_motion(self, ev):
        tag = self._tag_at(ev)
        if tag:
            self._set_cursor("hand2")
            self._tip(self.links[tag] or "链接")
        else:
            self._hide_tip()

    def _tip(self, text):
        tip = getattr(self, "_tipwin", None)
        if tip is None or not tip.winfo_exists():
            tip = self._tipwin = tk.Toplevel(self.root)
            tip.wm_overrideredirect(True)
            self._tip_lbl = tk.Label(tip, text="", justify="left", bd=1,
                                     relief="solid", font=("Segoe UI", 9),
                                     padx=6, pady=3)
            self._tip_lbl.pack()
            self._tip_text = None
            self._tip_shown = False
        if getattr(self, "_tip_text", None) == text and self._tip_shown:
            return
        self._tip_lbl.configure(text=text[:400])
        self._tip_text = text
        tip.wm_geometry("+%d+%d" % (self.root.winfo_pointerx() + 14,
                                    self.root.winfo_pointery() + 18))
        tip.deiconify()
        tip.lift()
        self._tip_shown = True
        job = getattr(self, "_tip_job", None)
        if job:
            self.root.after_cancel(job)
        self._tip_job = self.root.after(4000, self._hide_tip)

    def _hide_tip(self, event=None):
        job = getattr(self, "_tip_job", None)
        if job:
            self.root.after_cancel(job)
            self._tip_job = None
        self._tip_text = None
        tip = getattr(self, "_tipwin", None)
        if tip is not None and getattr(self, "_tip_shown", False):
            self._tip_shown = False
            try:
                tip.withdraw()
            except tk.TclError:
                pass
        self._set_cursor("xterm")

    def _yview(self, *args):
        self.text.yview(*args)

    def _yview_set(self, *args):
        try:
            self.ysb.set(*args)
        except tk.TclError:
            pass
        self._sync_toc()

    def _sync_toc(self):
        now = time.time()
        if now - self.last_sync < 0.12:
            return
        self.last_sync = now
        if self.sync_job:
            self.root.after_cancel(self.sync_job)
        self.sync_job = self.root.after(120, self._sync_now)

    def _on_destroy(self, event=None):
        if getattr(self, "sync_job", None):
            try:
                self.root.after_cancel(self.sync_job)
            except tk.TclError:
                pass
            self.sync_job = None

    def _alive(self):
        try:
            return bool(self.text.winfo_exists())
        except tk.TclError:
            return False

    def _current_heading(self):
        """当前小节 = 视口内最靠上的那个标题；视口里一个标题都没有时
        （正在读长章节的中段），退回视口上方最近的那个。"""
        t = self.text
        try:
            top = int(t.index("@0,6").split(".")[0])
            bot = int(t.index("@0,%d" % max(t.winfo_height() - 4, 8))
                      .split(".")[0])
        except (tk.TclError, ValueError):
            return None
        above = None
        for line, lv, title, anchor in self.headings:
            if line > bot:
                break
            if line >= top:
                return (line, title)
            above = (line, title)
        return above

    def _sync_now(self):
        if not self._alive():
            return
        cur = self._current_heading()
        if not cur or cur[0] == self._cur_head:
            return
        self._cur_head = cur[0]
        iid = self.iid_by_line.get(cur[0])
        if iid and self.toc.exists(iid):
            try:
                self._auto_sel = iid
                self.toc.selection_set(iid)
                self.toc.see(iid)
            except tk.TclError:
                pass
        if self.path:
            self.status.set("%s  ·  %s" % (os.path.basename(self.path),
                                           cur[1]))

    def _toc_click(self, ev):
        sel = self.toc.selection()
        if not sel or sel[0] not in self.toc_map:
            return
        if sel[0] == getattr(self, "_auto_sel", None):
            self._auto_sel = None
            return
        self.jump_line(self.toc_map[sel[0]])

    def jump_line(self, line):
        t = self.text
        line = max(line, 1)
        t.see("%d.0" % line)
        if t.winfo_ismapped():
            try:
                top = int(t.index("@0,6").split(".")[0])
            except (tk.TclError, ValueError):
                top = line
            if line != top:
                t.yview_scroll(line - top, "units")
        t.tag_remove("target", "1.0", "end")
        t.tag_add("target", "%d.0" % line, "%d.end+1c" % line)
        self.root.after(1000, self._clear_target)

    def _clear_target(self):
        if self._alive():
            self.text.tag_remove("target", "1.0", "end")

    def _page(self, d):
        self.text.yview_scroll(int(d * self.text.winfo_height() / 30), "units")

    def _over_text(self, w):
        try:
            while w is not None:
                if w is self.text:
                    return True
                w = getattr(w, "master", None)
        except tk.TclError:
            return False
        return False

    def _on_wheel(self, ev):
        if not self._over_text(ev.widget):
            return None
        if not ev.delta:
            return "break"
        notch = int(ev.delta / 120) or (1 if ev.delta > 0 else -1)
        self.text.yview_scroll(-notch * WHEEL_LINES, "units")
        return "break"

    def _on_zoom(self, ev):
        if not self._over_text(ev.widget):
            return None
        self.change_font(1 if ev.delta > 0 else -1)
        return "break"

    def _on_escape(self, event=None):
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
        elif self.findbar.winfo_ismapped():
            self.hide_find()
        else:
            self.text.tag_remove("target", "1.0", "end")

    def _on_copy(self, event=None):
        t = self.text
        if t.tag_ranges("sel"):
            text = t.get("sel.first", "sel.last")
        else:
            text = self.raw
        t.clipboard_clear()
        t.clipboard_append(text)
        self.status.set("已复制 %d 字符" % len(text))
        return "break"

    def _on_resize(self, ev):
        if ev.widget is not self.text:
            return
        px = max(ev.width - 44, 200)
        if abs(px - self.width_px) < 30:
            return
        self.width_px = px
        job = getattr(self, "_resize_job", None)
        if job:
            self.root.after_cancel(job)
        self._resize_job = self.root.after(320, self._rerender)

    def _rerender(self):
        self._rerender_now()

    def _rerender_now(self):
        self._render(self.raw)

    def change_font(self, d):
        self.font_size = min(max(self.font_size + d, 7), 32)
        self._save_settings()
        self._rerender_now()

    def _toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self._apply_theme()
        self._save_settings()
        self._rerender_now()

    def _set_toc_visible(self):
        try:
            self.pane.paneconfigure(self.toc_frame,
                                    hide=not self.toc_visible)
        except tk.TclError:
            pass

    def _toggle_toc(self):
        self.toc_visible = not self.toc_visible
        self._set_toc_visible()
        self._save_settings()

    def _toggle_wrap(self):
        self.wrap_on = not self.wrap_on
        self.text.configure(wrap="word" if self.wrap_on else "none")
        self._save_settings()
        self._rerender_now()

    def _toggle_src(self):
        self.show_src = not self.show_src
        self.btns["src"].configure(text="渲染" if self.show_src else "源码")
        self._save_settings()
        self._rerender_now()

    def _toggle_full(self):
        self.root.attributes("-fullscreen",
                             not self.root.attributes("-fullscreen"))

    # ------------------------------------------------------------ 查找 ----
    def show_find(self):
        self.findbar.pack(fill="x", before=self.body)
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")
        if self.find_var.get():
            self.do_find()

    def hide_find(self, event=None):
        self.findbar.pack_forget()
        self.text.tag_remove("found", "1.0", "end")
        self.text.tag_remove("found_cur", "1.0", "end")
        self.hits = []
        self.hit_pos = -1
        self.text.focus_set()

    def _on_find_key(self, ev):
        if ev.keysym in ("Return", "KP_Enter", "Shift_R", "Control_L"):
            return
        if self.find_job:
            self.root.after_cancel(self.find_job)
        self.find_job = self.root.after(200, self.do_find)

    def do_find(self, *args):
        self.find_job = None
        t = self.text
        q = self.find_var.get()
        t.tag_remove("found", "1.0", "end")
        t.tag_remove("found_cur", "1.0", "end")
        self.hits = []
        self.hit_pos = -1
        if not q:
            self.find_lbl.configure(text="")
            return
        start = "1.0"
        guard = 0
        while guard < 4000:
            guard += 1
            idx = t.search(q, start, nocase=not self.case_var.get(),
                           stopindex="end")
            if not idx:
                break
            end = "%s+%dc" % (idx, len(q))
            t.tag_add("found", idx, end)
            self.hits.append(idx)
            start = end
        self.find_lbl.configure(text="共 %d 处" % len(self.hits))
        if self.hits:
            self.goto_hit(1)

    def goto_hit(self, d):
        if not self.hits:
            self.do_find()
        if not self.hits:
            self.find_lbl.configure(text="没有找到")
            return
        self.hit_pos = (self.hit_pos + d) % len(self.hits)
        t = self.text
        t.tag_remove("found_cur", "1.0", "end")
        idx = self.hits[self.hit_pos]
        end = "%s+%dc" % (idx, len(self.find_var.get()))
        t.tag_add("found_cur", idx, end)
        line = max(int(idx.split(".")[0]) - 3, 1)
        try:
            top = int(t.index("@0,6").split(".")[0])
        except (tk.TclError, ValueError):
            top = 1
        if abs(line - top) > 6:
            t.yview_scroll(line - top, "units")
        self.find_lbl.configure(text="%d / %d" % (self.hit_pos + 1,
                                                  len(self.hits)))

    # ------------------------------------------------------------ 杂项 ----
    def _recent_menu(self):
        menu = tk.Menu(self.root, tearoff=0)
        items = [r for r in self.recent if os.path.isfile(r)]
        if not items:
            menu.add_command(label="（还没有最近文件）")
        for r in items:
            menu.add_command(label=os.path.basename(r),
                             command=lambda p=r: self.open_file(p))
        if items:
            menu.add_separator()
            menu.add_command(label="清空最近文件列表", command=self._clear_recent)
        b = self.btns["recent"]
        try:
            menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            menu.grab_release()

    def _clear_recent(self):
        self.recent = []
        self._save_settings()

    def _copy_all(self):
        if not self.raw:
            return
        self.text.clipboard_clear()
        self.text.clipboard_append(self.raw)
        self.status.set("已复制全文 %d 字符" % len(self.raw))

    def _open_editor(self):
        if not self.path:
            return
        exe = self._find_editor()
        try:
            if exe:
                subprocess.Popen([exe, self.path])
                self.status.set("已在编辑器中打开：%s" % os.path.basename(exe))
            else:
                os.startfile(self.path)
                self.status.set("已用系统默认程序打开")
        except OSError as e:
            self.status.set("打开编辑器失败：%s" % e)

    def _find_editor(self):
        if not hasattr(self, "_editor_cache"):
            exe = None
            for name in ("code", "code.cmd", "notepad++", "notepad.exe"):
                exe = shutil.which(name)
                if exe:
                    break
            self._editor_cache = exe
        return self._editor_cache

    def _help(self):
        if self._helpwin is not None and self._helpwin.winfo_exists():
            self._helpwin.lift()
            return
        box = self._helpwin = tk.Toplevel(self.root)
        box.title("Markdown 查看器 使用说明")
        box.geometry("700x560")
        box.configure(bg=self.th["bg"])
        t = tk.Text(box, wrap="word", font=(self.ui, 10), padx=14, pady=10,
                    relief="flat", bg=self.th["bg"], fg=self.th["fg"],
                    insertbackground=self.th["fg"], highlightthickness=1,
                    highlightbackground=self.th["border"])
        t.pack(fill="both", expand=True)
        t.tag_configure("title", font=(self.ui, 14, "bold"),
                        foreground=self.th["heading"], spacing3=8)
        t.tag_configure("sec", font=(self.ui, 11, "bold"),
                        foreground=self.th["heading"], spacing1=10, spacing3=2)
        t.tag_configure("key", font=(self.mono, 10))
        for ln in HELP_MD.split("\n"):
            head = ln.rstrip()
            if not head:
                t.insert("end", "\n")
                continue
            if head == HELP_MD.split("\n")[0]:
                t.insert("end", head + "\n", ("title",))
            elif not head.startswith((" ", "\t")) and not head[0].isascii() \
                    and head in SECTIONS:
                t.insert("end", head + "\n", ("sec",))
            else:
                t.insert("end", head + "\n")
        t.configure(state="disabled")
        tk.Button(box, text="关闭（Esc）", command=box.destroy, pady=4,
                  relief="flat", bg=self.th["btn_bg"]).pack(fill="x")
        box.bind("<Escape>", lambda e: box.destroy())
    _helpwin = None


SECTIONS = ("常用操作", "支持的语法", "显示限制", "怎么用文件关联打开")


HELP_MD = """Markdown 查看器

常用操作
Ctrl+O 打开文件   F5 重新读取（自动判断编码）   Ctrl+F 查找（回车下一个）
Ctrl + / Ctrl - 调字号（或 Ctrl+滚轮）   Alt+左/右 切换同目录的上一份/下一份
F11 全屏   Esc 关闭查找条   Ctrl+C 复制选中文字（没选中则复制全文）
工具栏“编码”在中文乱码时手动切换 utf-8 / gbk / big5 / utf-16
工具栏“源码”切回原始 Markdown，用来核对缩进和没生效的语法
链接可直接点击：网址用浏览器打开，文内锚点跳转，图片/附件用系统关联程序打开

支持的语法
标题 # 到 ######，也支持下方 === / --- 的下划线式标题
粗体 **x**、斜体 *x*、粗斜体 ***x***、删除线 ~~x~~、高亮 ==x==、行内代码 `x`
无序 / 有序 / 嵌套列表、任务列表 - [x]、引用块 >（可嵌套）、分隔线 --- 或 ***
围栏代码块 ```语言（Python / C / Shell / BAT / SQL / YAML / JSON / Java / JS / Pascal / Lua 有着色）
GFM 表格（含 :---: 对齐），图片 ![](相对路径或绝对路径)，自动链接 <https://...>
脚注 [^标签] 与 [^标签]: 定义，YAML 头部元信息，原始 HTML 以灰色显示

显示限制
表格按真实控件绘制，窗口过窄时自动改为纯文本行；JPG/WEBP 需要安装 Pillow
才能显示，PNG/GIF/BMP 直接支持。正文中所有源文件换行都按换行渲染，
所以 Markdown 里手动换行和硬换行的效果一致。

怎么用文件关联打开
把 .md 文件拖到 mdview.pyw 图标上即可打开；
运行同目录的 install_md_menu.bat 后，任意 .md 文件右键菜单里会出现
“用 Markdown 查看器打开”，不需要改动 .md 的默认打开方式。
"""


def _bind_tip(app, w, text):
    state = {}

    def enter(e):
        state["job"] = app.root.after(450, lambda: show())

    def show():
        tip = state.get("tip")
        if tip is None or not tip.winfo_exists():
            tip = state["tip"] = tk.Toplevel(app.root)
            tip.wm_overrideredirect(True)
            state["lbl"] = tk.Label(tip, text=text, bd=1, relief="solid",
                                    font=("Segoe UI", 9), padx=6, pady=3,
                                    bg=app.th["tip_bg"])
            state["lbl"].pack()
        tip.wm_geometry("+%d+%d" % (app.root.winfo_pointerx() + 12,
                                    app.root.winfo_pointery() + 20))
        tip.deiconify()

    def leave(e):
        if state.get("job"):
            app.root.after_cancel(state["job"])
        tip = state.get("tip")
        if tip is not None and tip.winfo_exists():
            tip.withdraw()
    if text:
        w.bind("<Enter>", enter)
        w.bind("<Leave>", leave)


def _lang_keys(lang):
    base = re.split(r"[,{:\[]", (lang or "").lower())[0].strip()
    alias = {"python": ("py", "python"), "sh": ("sh", "bash", "shell", "console",
                                                "zsh", "bat", "batch", "dos",
                                                "cmd", "powershell", "ps1"),
             "c": ("c", "h", "cpp", "cc", "cxx", "hpp", "cs"),
             "java": ("java", "kotlin", "scala"),
             "js": ("js", "javascript", "ts", "typescript", "json"),
             "pascal": ("pascal", "delphi", "pas"), "sql": ("sql",),
             "lua": ("lua",)}
    keys = set()
    for name, al in alias.items():
        if base == name or base in al:
            keys |= {w.lower() for w in KEYWORDS[name].split()}
    return keys


KEYWORDS = {
    "python": "def class return if elif else for while in not and or try except "
              "finally import from as with lambda yield pass break None True "
              "False global raise assert del async await self print len range",
    "sh": "if then else elif fi for while do done case esac function return "
          "exit local export set echo cd source in select until break continue "
          "rem call goto pushd popd start shift",
    "c": "int char void float double long short unsigned struct typedef static "
         "const return if else for while switch case break continue default "
         "sizeof enum union do goto extern class public private protected this "
         "new delete template namespace using true false NULL nullptr bool",
    "java": "public private protected class interface extends implements static "
            "final void int long double float boolean char return if else for "
            "while do switch case break continue new this try catch finally "
            "throw throws import package null true false abstract synchronized",
    "js": "function return if else for while do var let const new this class "
          "extends super try catch finally throw typeof instanceof null "
          "undefined true false async await import export default switch case "
          "break continue of in delete yield",
    "pascal": "program unit begin end var const type procedure function if then "
              "else case of while do for to downto repeat until record array "
              "string integer boolean real nil uses interface implementation",
    "sql": "select from where insert into values update set delete join left "
           "right inner outer on group by order having create alter drop table "
           "view index as and or not null distinct limit offset union all case "
           "when then else end count sum avg max min",
    "lua": "function end if then else elseif for while do return local and or "
           "not nil true false repeat until in pairs ipairs",
}

STR_RE = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"?|'(?:[^'\\\n]|\\.)*'?")
NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


def _split_code(ln, keys):
    """切一行代码为 (文本, 标签)：注释 codecom、字符串 costr、关键字 codehl。"""
    if not ln.strip():
        return [(ln, None)]
    pat = (r"(#(?!include\b|define\b|undef\b|ifdef\b|ifndef\b|endif\b|elif\b"
           r"|pragma\b|error\b|warning\b|line\b)[^\n]*|//[^\n]*"
           r"|--[ \t][^\n]*|\bREM\b[^\n]*)"
           r"|(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
           r"|([A-Za-z_][\w.]*|\d[\w.]*)")
    pieces = []
    for m in re.finditer(pat, ln):
        if m.group(1):
            pieces.append((m.group(1), "codecom"))
        elif m.group(2):
            pieces.append((m.group(2), "costr"))
        elif m.group(3).lower() in keys:
            pieces.append((m.group(3), "codehl"))
    if not pieces:
        return [(ln, None)]
    out = []
    pos = 0
    for txt, tg in pieces:
        s = ln.find(txt, pos)
        if s < 0:
            continue
        if s > pos:
            out.append((ln[pos:s], None))
        out.append((txt, tg))
        pos = s + len(txt)
    if pos < len(ln):
        out.append((ln[pos:], None))
    return out or [(ln, None)]


def _human(n):
    if n < 1024:
        return "%d B" % n
    if n < 1048576:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


WELCOME_MD = """# Markdown 查看器

离线运行的 .md 阅读器，只用 Python 自带 tkinter，不装任何第三方库。

## 怎么打开文件

1. 点工具栏 **打开**，或把 `.md` 文件**拖到 `mdview.pyw` 图标**上；
2. 运行同目录 `install_md_menu.bat` 后，任意 `.md` 右键即可 **用 Markdown 查看器打开**。

快捷键：`Ctrl+O` 打开 · `Ctrl+F` 查找 · `F5` 重载 · `Ctrl + / -` 字号 · `Alt+←/→` 同目录切换 · `F11` 全屏

## 语法示例

| 类别 | 写法 | 显示效果 |
| :--- | :--- | :--- |
| 强调 | `**粗体**` `~~删除~~` | **粗体** ~~删除~~ |
| 代码 | 两个反引号包住的 print(1) | 见左侧行内代码 |
| 链接 | `[百度](https://www.baidu.com)` | [百度](https://www.baidu.com) |
| 对齐 | 表格第三列右对齐 | 12345 |

- [x] 已完成事项显示为勾选框
- [ ] 未完成事项显示为方框
  - 嵌套列表自动缩进
  1. 有序子列表也可以
  2. 序号按原文保留

> 引用块左侧带底色，
> 支持多行与嵌套。
>
> > 这是第二层引用。

```python
def read_point(addr):
    # 围栏代码块按语言着色
    total = 0  # 注释为灰色
    for i in range(3):
        total += i
    return total + addr
```

正文里点这个脚注 [^1] 会跳到底部：

[^1]: 第一条脚注的内容，会统一收集到文末的“脚注”一节。

![示例图片](pic/demo.png)
"""


def dump_main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for p in argv:
        with open(p, "rb") as f:
            items = parse_document(f.read().decode("utf-8", "replace"))
        print("==== %s  %d items" % (p, len(items)))
        for it in items:
            k = it["kind"]
            if k == "heading":
                print("H%d  %-46s #%s" % (it["level"], it["title"][:46],
                                          it["anchor"]))
            elif k == "para":
                print("P    %s" % plain(it["spans"])[:72])
            elif k == "li":
                print("LI   lvl=%d %-3s %-3s %s" % (
                    it["level"], it["marker"],
                    "chk" if it.get("checked") else ("off" if it.get("checked")
                                                     is False else "---"),
                    plain(it["spans"])[:52]))
            elif k == "code":
                print("CODE [%s] %d 行" % (it.get("lang"),
                                           it["text"].count("\n") + 1))
            elif k == "table":
                print("TBL  %d 列 %d 行 %s" % (len(it["aligns"]),
                                               len(it["rows"]), it["aligns"]))
            elif k == "image":
                print("IMG  %s" % it["url"])
            elif k == "hr":
                print("HR")
            elif k == "footdef":
                print("FOOT %d %s %s" % (it["num"], it["label"],
                                          plain(it["spans"])[:40]))
            elif k == "meta":
                print("META %s" % it["meta"])
            else:
                print("%-5s %s" % (k, plain(it.get("spans", []))[:60]
                                   or it.get("text", "")[:60]))


def main():
    args = sys.argv[1:]
    if args and args[0] == "--dump":
        dump_main(args[1:] or [__file__])
        return 0
    targets = [a for a in args if not a.startswith("-")]
    root = tk.Tk()
    Viewer(root, targets)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

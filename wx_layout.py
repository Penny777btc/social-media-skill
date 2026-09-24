#!/usr/bin/env python3
"""meiia-txt：公众号排版渲染 / 打包 / 检查（v3.1 规范）

  assets  把固定开篇图 / 结尾图从 --stickers 文件夹复制到排版 MD 同级的「图片和附件」
  render  排版 MD -> 公众号 HTML（全内联样式、图片 base64 内嵌）
  pack    排版 MD + 引用到的图片 -> zip（保证相对路径可用）
  check   对比原文与排版 MD 的文字、检查 HTML 是否违规、估算段落行数

排版 MD 约定（同时就是交付的 MD 文件）：
  # 标题            只留在 MD 里，HTML 不输出（公众号标题栏另填）
  标题后紧跟的 >     导语，14px #999999，不加框
  ## / ###          一级标题 / 二级标题
  > 全部行都加粗     总结卡（全文仅第一个生效，其余按引用框）
  > 其他             引用框
  【……】整段        虚线占位框（图片占位、补图、投票等）
  【此句未写完，请补全】  行内强调色标注
  ![说明](图片和附件/x.png)  居中图片 + 说明作图注（--no-caption 可排除）
  ![开篇图](图片和附件/meiia_open.png) / ![结尾图](图片和附件/meiia_end.png)
                    固定贴纸，110px 宽居中、无图注；开篇图在「大家好」前，结尾图在「下期再见」后，
                    位置不对或缺失时自动放好（--no-stickers 关闭）
  1. / - 列表        p + 手写序号 / 圆点
  整段只有网址        14px #999999 纯文本链接
  **整段短标签**      ≤16 字且不以句末标点结尾 -> #333333 加粗，不用强调色
  **其他加粗**        强调色加粗
"""
import argparse, base64, hashlib, html, mimetypes, ntpath, os, re, sys, zipfile
from pathlib import Path
from urllib.parse import unquote

MD_ESC = re.compile(r'\\([\\`*_{}\[\]()#+\-.!|>~])')   # 飞书导出的 1\. \+ github\.com 之类的转义

FONT = '-apple-system, "PingFang SC", "Microsoft YaHei", "Source Han Sans SC", sans-serif'
TYPES = {  # 行距, 段间距, 字间距
    'review': (1.75, 16, 1), 'tutorial': (1.75, 16, 1),
    'story': (2.0, 24, 1.5), 'news': (1.6, 12, 1),
}
H, B, A = '#333333', '#555555', '#999999'
DEFAULT_ACCENT = '#E62270'
DEFAULT_STICKERS = Path(__file__).resolve().parent.parent / 'assets'

# 固定开篇图 / 结尾图（美丫姐贴纸，330px 宽 3 倍图，显示 110px）
# 默认从 skill 的 assets/ 读取；必要时可用 --stickers 指定其他贴纸目录。
STICKER_W = 110
STICKERS = {
    'open': {'file': 'meiia_open.png', 'alt': '开篇图', 'md5': {'8156ad32fbf5cf013ad29aeb55f8b7eb', '0ff4b8cd2acaf9f11d34fcd4a3f09f25'}},
    'end': {'file': 'meiia_end.png', 'alt': '结尾图', 'md5': {'0eaaf1de587da084c80c8ac19d4cd322', '22807302f54b6ff6721df8b477e4b4e5'}},
}


def normalize_accent(value):
    """接受 #RRGGBB 或不透明的 #RRGGBBFF，内部统一使用六位颜色。"""
    if not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[fF]{2})?', value):
        raise argparse.ArgumentTypeError('重点色必须是 #RRGGBB 或不透明的 #RRGGBBFF')
    return value[:7].upper()


def safe_media_path(base, path):
    """将 Markdown 图片路径限制在其所在目录内，包括符号链接的目标。"""
    path = unquote(path)
    if not path or '\x00' in path or '\\' in path or os.path.isabs(path) or ntpath.isabs(path):
        raise ValueError(f'不安全的图片路径：{path!r}')
    if ntpath.splitdrive(path)[0] or any(part == '..' for part in Path(path).parts):
        raise ValueError(f'不安全的图片路径：{path!r}')
    root = os.path.realpath(base)
    target = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath((root, target)) != root:
        raise ValueError(f'图片路径超出 Markdown 所在目录：{path!r}')
    return target


def sticker_bytes(kind, stickers_dir, base):
    """按顺序找贴纸：skill assets/ 或 --stickers → 文章的「图片和附件」。"""
    s = STICKERS[kind]
    article_sticker = safe_media_path(base, f'图片和附件/{s["file"]}')
    for fp in [os.path.join(stickers_dir, s['file']) if stickers_dir else '', article_sticker]:
        if fp and os.path.isfile(fp):
            return Path(fp).read_bytes()
    d = safe_media_path(base, '图片和附件')
    if os.path.isdir(d):
        for f in os.listdir(d):
            fp = safe_media_path(base, f'图片和附件/{f}')
            if os.path.isfile(fp) and hashlib.md5(Path(fp).read_bytes()).hexdigest() in s['md5']:
                return Path(fp).read_bytes()
    sys.exit(f'[error] 找不到{s["alt"]} {s["file"]}：请检查 skill/assets 或用 --stickers 指向贴纸目录')


def sticker_kind(fp):
    """按文件名或原图 md5 判断是不是开篇/结尾贴纸"""
    name = os.path.basename(fp)
    digest = hashlib.md5(open(fp, 'rb').read()).hexdigest() if os.path.exists(fp) else ''
    for k, s in STICKERS.items():
        if name == s['file'] or digest in s['md5'] or name.split('.')[0] in s['md5']:
            return k
    return None


def rgb(c):
    c = c.lstrip('#'); return ','.join(str(int(c[i:i + 2], 16)) for i in (0, 2, 4))


# ---------------- 解析 ----------------
def parse(md):
    lines = md.replace('\r\n', '\n').split('\n')
    blocks, i = [], 0
    while i < len(lines):
        l = lines[i].rstrip()
        if not l.strip():
            i += 1; continue
        if l.lstrip().startswith('>'):
            q = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                s = lines[i].lstrip()[1:].strip()
                if s: q.append(s)
                i += 1
            blocks.append(['quote', q]); continue
        if re.match(r'^\d+\\?[.、)] ', l):
            items = []
            while i < len(lines) and re.match(r'^\d+\\?[.、)] ', lines[i]):
                n, t = re.match(r'^(\d+)\\?[.、)] (.*)$', lines[i]).groups(); items.append((n, t.strip())); i += 1
            blocks.append(['ol', items]); continue
        if re.match(r'^[-*+] ', l):
            items = []
            while i < len(lines) and re.match(r'^[-*+] ', lines[i]):
                items.append(lines[i][2:].strip()); i += 1
            blocks.append(['ul', items]); continue
        m = re.match(r'^(#{1,6})\s+(.+)$', l)
        if m:
            blocks.append(['h%d' % len(m.group(1)), m.group(2).strip()]); i += 1; continue
        m = re.match(r'^!\[(.*?)\]\((.+?)\)\s*$', l)
        if m:
            blocks.append(['img', (m.group(1), m.group(2))]); i += 1; continue
        if re.match(r'^<?https?://\S+?>?$', l.strip()):
            blocks.append(['url', MD_ESC.sub(r'\1', l.strip().strip('<>'))]); i += 1; continue
        if re.match(r'^【.*】$', l.strip()):
            blocks.append(['ph', l.strip()]); i += 1; continue
        blocks.append(['p', l.strip()]); i += 1
    return blocks


# ---------------- 渲染 ----------------
def render(args):
    src = args.md
    base = os.path.dirname(os.path.abspath(src))
    C, S = args.accent, f'{args.size}px'
    RGB = rgb(C)
    lh, gap, ls = TYPES[args.type]
    F = f'font-family:{FONT.replace(chr(34), chr(39))};'
    no_cap = set(x.strip() for x in (args.no_caption or '').split(',') if x.strip())

    P = f'margin:0 0 {gap}px;font-size:{S};line-height:{lh};letter-spacing:{ls}px;color:{B};text-align:{args.align};{F}'
    AUX = f'margin:0 0 {gap}px;font-size:14px;line-height:1.75;letter-spacing:1px;color:{A};word-break:break-all;{F}'
    CAP = f'margin:-12px 0 20px;font-size:14px;line-height:1.75;letter-spacing:1px;color:{A};text-align:center;{F}'

    def inline(t):
        t = html.escape(MD_ESC.sub(r'\1', t), quote=False)
        t = t.replace('【此句未写完，请补全】', f'<span style="color:{C};font-weight:bold;">【此句未写完，请补全】</span>')
        t = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)',
                   lambda m: f'{m.group(1)} <span style="font-size:14px;color:{A};word-break:break-all;">{m.group(2)}</span>', t)
        return re.sub(r'\*\*(.+?)\*\*', lambda m: f'<strong style="color:{C};">{m.group(1)}</strong>', t)

    def is_label(t):
        m = re.fullmatch(r'\*\*(.+?)\*\*', t)
        return bool(m) and len(m.group(1)) <= 16 and not re.search(r'[。！？!?…]$', m.group(1))

    def img(alt, path):
        fp = safe_media_path(base, path)
        kind = sticker_kind(fp)
        if kind:
            return sticker(kind)
        if not os.path.exists(fp):
            print(f'[warn] 图片不存在，改为占位：{path}', file=sys.stderr)
            return placeholder(f'【图片：{alt or path}】')
        mime = mimetypes.guess_type(fp)[0] or 'image/png'
        data = base64.b64encode(open(fp, 'rb').read()).decode()
        out = (f'<section style="margin:20px 0;text-align:center;line-height:0;">'
               f'<img src="data:{mime};base64,{data}" alt="{html.escape(alt)}" '
               f'style="display:inline-block;max-width:100%;height:auto;vertical-align:top;border-radius:6px;"></section>')
        if alt and alt not in no_cap:
            out += f'<p style="{CAP}">{html.escape(alt)}</p>'
        return out

    def sticker(kind):
        s = STICKERS[kind]
        b64 = base64.b64encode(sticker_bytes(kind, args.stickers, base)).decode()
        return (f'<section style="margin:20px 0;text-align:center;line-height:0;">'
                f'<img src="data:image/png;base64,{b64}" alt="{s["alt"]}" '
                f'style="display:inline-block;width:{STICKER_W}px;max-width:100%;height:auto;vertical-align:top;"></section>')

    def placeholder(t):
        return (f'<section style="margin:20px 0;padding:28px 15px;border:1px dashed {A};border-radius:6px;text-align:center;">'
                f'<p style="margin:0;font-size:14px;line-height:1.75;color:{A};{F}">{html.escape(t)}</p></section>')

    blocks = parse(open(src, encoding='utf-8').read())
    if not args.no_stickers:                       # 开篇图放「大家好」前，结尾图放「下期再见」后
        old = [(i, sticker_kind(safe_media_path(base, v[1]))) for i, (k, v) in enumerate(blocks) if k == 'img']
        old = [(i, kd) for i, kd in old if kd]
        before = [(kd, i) for i, kd in old]
        blocks = [b for i, b in enumerate(blocks) if i not in {j for j, _ in old}]
        hi = next((i for i, (k, v) in enumerate(blocks) if k == 'p' and v.lstrip('*').startswith('大家好')), None)
        if hi is None:                                 # 没有「大家好」：放导语后
            hi = 1 if blocks and blocks[0][0] == 'h1' else 0
            if len(blocks) > hi and blocks[hi][0] == 'quote': hi += 1
            print('[warn] 没找到「大家好」段，开篇图放在导语后')
        blocks.insert(hi, ['img', ('开篇图', STICKERS['open']['file'])])
        bi = max((i for i, (k, v) in enumerate(blocks) if k == 'p' and '下期再见' in v), default=None)
        if bi is None:
            bi = max((i for i, (k, v) in enumerate(blocks) if k == 'p' and '再见' in v), default=None)
            if bi is not None: print('[info] 没有「下期再见」，结尾图放在最后一处「再见」段后')
        if bi is None:
            bi = len(blocks) - 1
            print('[warn] 没找到「再见」段，结尾图放在文末')
        blocks.insert(bi + 1, ['img', ('结尾图', STICKERS['end']['file'])])
        after = [(sticker_kind(safe_media_path(base, v[1])), i) for i, (k, v) in enumerate(blocks)
                 if k == 'img' and sticker_kind(safe_media_path(base, v[1]))]
        if before != after:
            print('[info] HTML 已按规则放置开篇图/结尾图；MD 里请同样写：「大家好」前一行 ![开篇图](图片和附件/meiia_open.png)，'
                  '「下期再见」后一行 ![结尾图](图片和附件/meiia_end.png)')
    levels = sorted({int(k[1]) for k, _ in blocks if k[0] == 'h' and k != 'h1'})
    if len(levels) > 2:
        sys.exit(f'[error] 标题超过 2 级：{levels}')
    lv1 = levels[0] if levels else 2
    out, after_title, card_used = [], False, False
    for k, v in blocks:
        if k == 'h1':
            after_title = True; continue
        if k == 'quote' and after_title:           # 导语
            out += [f'<p style="{AUX}">{inline(s)}</p>' for s in v]
            after_title = False; continue
        after_title = False
        if k.startswith('h'):
            if int(k[1]) == lv1:
                out.append(f'<section style="margin:36px 0 18px;"><p style="margin:0;font-size:20px;font-weight:bold;'
                           f'line-height:1.5;letter-spacing:1px;color:{H};{F}">{inline(v)}</p>'
                           f'<section style="margin:8px 0 0;width:32px;height:3px;background:{C};"></section></section>')
            else:
                out.append(f'<p style="margin:28px 0 14px;padding-left:10px;border-left:3px solid {C};font-size:17px;'
                           f'font-weight:bold;line-height:1.5;letter-spacing:1px;color:{H};{F}">{inline(v)}</p>')
        elif k == 'p':
            if is_label(v):
                out.append(f'<p style="{P}"><strong style="color:{H};">{html.escape(v[2:-2])}</strong></p>')
            else:
                out.append(f'<p style="{P}">{inline(v)}</p>')
        elif k == 'url':
            out.append(f'<p style="{AUX}">{html.escape(v)}</p>')
        elif k == 'ph':
            out.append(placeholder(v))
        elif k == 'img':
            out.append(img(*v))
        elif k in ('ol', 'ul'):
            items = v if k == 'ol' else [('•', t) for t in v]
            for j, (n, t) in enumerate(items):
                mb = gap if j == len(items) - 1 else 6
                mark = f'{n}.' if k == 'ol' else n
                out.append(f'<p style="margin:0 0 {mb}px;font-size:{S};line-height:{lh};letter-spacing:{ls}px;color:{B};{F}">'
                           f'<span style="color:{C};font-weight:bold;">{mark}</span> {inline(t)}</p>')
        elif k == 'quote':
            if not card_used and all(re.fullmatch(r'\*\*.+\*\*', s) for s in v):
                card_used = True
                inner = '<br>'.join(html.escape(s[2:-2]) for s in v)
                out.append(f'<section style="margin:0 0 20px;padding:18px 15px;border-radius:8px;background:rgba({RGB},0.08);'
                           f'text-align:center;"><p style="margin:0;font-size:17px;font-weight:bold;line-height:1.9;color:{H};{F}">'
                           f'{inner}</p></section>')
            else:
                inner = ''.join(f'<p style="margin:0;font-size:{S};line-height:1.75;letter-spacing:1px;color:{B};{F}">{inline(s)}</p>' for s in v)
                out.append(f'<section style="margin:0 0 16px;padding:12px 15px;border-left:3px solid {C};'
                           f'background:rgba({RGB},0.06);">{inner}</section>')
    title = next((v for k, v in blocks if k == 'h1'), '公众号排版')
    body = '\n'.join(out)
    doc = (f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title></head>\n'
           f'<body style="margin:0;padding:20px 16px;background:#ffffff;">\n'
           f'<section style="max-width:677px;margin:0 auto;{F}font-size:{S};color:{B};">\n{body}\n</section>\n</body></html>\n')
    open(args.out, 'w', encoding='utf-8').write(doc)
    print(f'[ok] {args.out}  {os.path.getsize(args.out) / 1e6:.1f} MB')
    big = os.path.getsize(args.out) > 15e6
    if big: print('[warn] HTML 超过 15MB，粘贴进公众号可能较慢或失败，可考虑压缩 GIF 或在编辑器里手动上传大图')


# ---------------- 贴纸文件 ----------------
def assets(args):
    base = os.path.dirname(os.path.abspath(args.md))
    d = safe_media_path(base, '图片和附件')
    os.makedirs(d, exist_ok=True)
    for k, s in STICKERS.items():
        fp = safe_media_path(base, f'图片和附件/{s["file"]}')
        if not os.path.exists(fp):
            data = sticker_bytes(k, args.stickers, base)
            open(fp, 'wb').write(data)
        print(f'[ok] {fp}')


# ---------------- 打包 ----------------
def pack(args):
    base = os.path.dirname(os.path.abspath(args.md))
    md = open(args.md, encoding='utf-8').read()
    imgs = sorted(set(re.findall(r'!\[.*?\]\((.+?)\)', md)))
    with zipfile.ZipFile(args.out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(args.md, os.path.basename(args.md))
        for p in imgs:
            fp = safe_media_path(base, p)
            archive_name = unquote(p)
            if os.path.exists(fp): z.write(fp, archive_name)
            else: print(f'[warn] 缺图：{p}', file=sys.stderr)
    print(f'[ok] {args.out}  {len(imgs)} 张图')


# ---------------- 检查 ----------------
def plain(md):
    md = MD_ESC.sub(r'\1', md)
    md = re.sub(r'!\[.*?\]\(.*?\)', '', md)
    md = re.sub(r'^【.*】$', '', md, flags=re.M)
    md = re.sub(r'^\s*(#{1,6}\s+|>\s?|\d+[.、)]\s|[-*+]\s)', '', md, flags=re.M)
    md = md.replace('**', '').replace('<http', 'http')
    md = re.sub(r'(https?://\S+?)>', r'\1', md)
    return re.sub(r'\s', '', md)


def wlen(t):
    t = re.sub(r'!\[.*?\]\(.*?\)|\*\*', '', t)
    return sum(1 if ord(ch) > 0x2E80 else 0.5 for ch in t)


def check(args):
    structure_ok = True
    text_review = False
    formatted = open(args.md, encoding='utf-8').read()
    base = os.path.dirname(os.path.abspath(args.md))
    for path in re.findall(r'!\[.*?\]\((.+?)\)', formatted):
        safe_media_path(base, path)
    a, b = plain(open(args.src, encoding='utf-8').read()), plain(formatted)
    if a == b:
        print('[ok] 文字与原文一致（忽略空白、Markdown 符号、【】占位）')
    else:
        import difflib
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        text_review = True
        print('[review] 文字与原文存在差异，请逐处核对；有意新增的标题或提示写进说明，无意改动的文字请改回：')
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != 'equal':
                print(f'  {op}: 「{a[max(0,i1-8):i2+8]}」 → 「{b[max(0,j1-8):j2+8]}」')
    per_line = 21 if args.size <= 15 else 20
    for n, l in enumerate(formatted.split('\n'), 1):
        if not l.strip() or l.startswith(('#', '!', '【')): continue
        lines = wlen(l) / per_line
        if lines > 5: print(f'[error] 第 {n} 行约 {lines:.1f} 行（>5）：{l[:24]}…'); structure_ok = False
        elif lines > 3: print(f'[warn] 第 {n} 行约 {lines:.1f} 行（>3，若无句末标点可保留）：{l[:24]}…')
    if args.html:
        h = open(args.html, encoding='utf-8').read()
        body = re.sub(r'src="data:[^"]+"', 'src=""', h.split('<body', 1)[1])
        for pat, msg in [(r'<style', '<style> 标签'), (r'\sclass=', 'class'), (r'\sid=', 'id'), (r'<div', 'div'),
                         (r'<ul|<ol|<li', 'ul/ol/li'), (r'<h[1-6]', 'h 标签'), (r'position:|float:|display:\s*(flex|grid)', '定位/浮动/flex/grid'),
                         (r'#000000|color:\s*#000\b|color:\s*black', '纯黑')]:
            if re.search(pat, body, re.I): print(f'[error] HTML 含 {msg}'); structure_ok = False
        colors = set(re.findall(r'(?<![-\w])color:\s*(#[0-9A-Fa-f]{3,6})', body))
        extra = {c.upper() for c in colors} - {H, B, A, args.accent.upper()}
        if extra: print(f'[error] 出现规范外文字颜色：{extra}'); structure_ok = False
        n_strong = len(re.findall(r'<strong style="color:' + re.escape(args.accent), body, re.I))
        print(f'[info] 强调色加粗 {n_strong} 处，正文约 {len(b)} 字（参考：每 300 字约 1 处 ≈ {len(b)//300} 处）')
        for p in re.findall(r'<p [^>]*>(.*?)</p>', body):
            if len(re.findall(r'<strong style="color:' + re.escape(args.accent), p, re.I)) > 1:
                print(f'[warn] 同一段有多处强调色：{re.sub("<[^>]+>", "", p)[:30]}…')
    if not structure_ok:
        print('[fail] 结构或样式检查未通过')
        raise SystemExit(1)
    print('[review] 结构通过/文字待核对' if text_review else '[ok] 检查通过')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    r = sp.add_parser('render'); r.add_argument('md'); r.add_argument('--out', required=True)
    r.add_argument('--accent', type=normalize_accent, default=DEFAULT_ACCENT)
    r.add_argument('--size', type=int, default=16, choices=[15, 16])
    r.add_argument('--type', default='review', choices=list(TYPES)); r.add_argument('--no-caption', default='')
    r.add_argument('--align', default='justify', choices=['justify', 'left'])
    r.add_argument('--no-stickers', action='store_true', help='不自动补开篇图/结尾图')
    r.add_argument('--stickers', default=DEFAULT_STICKERS, help='贴纸文件夹，默认为 skill/assets')
    s = sp.add_parser('assets'); s.add_argument('md'); s.add_argument('--stickers', default=DEFAULT_STICKERS)
    p = sp.add_parser('pack'); p.add_argument('md'); p.add_argument('--out', required=True)
    c = sp.add_parser('check'); c.add_argument('src'); c.add_argument('md'); c.add_argument('--html')
    c.add_argument('--accent', type=normalize_accent, default=DEFAULT_ACCENT)
    c.add_argument('--size', type=int, default=16)
    a = ap.parse_args()
    try:
        {'render': render, 'pack': pack, 'check': check, 'assets': assets}[a.cmd](a)
    except ValueError as exc:
        sys.exit(f'[error] {exc}')

#!/usr/bin/env python3
"""
孙茜作品集 · 网页版 —— 图片导出脚本

把 PDF 的每一页渲染成 WebP，长页自动切片，供 index.html 滚动展示。

用法：
    python3 tools/build_tiles.py

想调画质、换一版 PDF，改下面「参数」区再重跑即可（可反复重跑，会先清空旧图）。
依赖 PyMuPDF 和 Pillow：
    pip3 install --user pymupdf Pillow
"""

import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

try:
    import fitz  # PyMuPDF
    from PIL import Image
except ImportError:
    sys.exit("缺少依赖，请先运行：pip3 install --user pymupdf Pillow")

# ── 参数 ──────────────────────────────────────────────────────────
SRC_PDF = Path("/Users/zaizai/Documents/作品集/全作品集/pdf/9.20/孙茜-作品集.pdf")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "img"

SCALE = 1.0          # 1.0 = 1920px 宽。想更清晰改成 1.5，体积约翻倍
WEBP_QUALITY = 76    # 实测：q82 = 12.1MB，q76 = 9.4MB，两者在 100% 原始像素下几乎看不出差别
                     # （也测过 AVIF：同画质只小 8%，不值得多一套文件和兼容风险）
TILE_H = 2000        # 切片高度。别超过 2000 —— iPhone Safari 解码超大图会失败
BAND_H = 10000       # 渲染时按此高度分带以控制内存，必须是 TILE_H 的整数倍

OG_SIZE = (1200, 630)  # 链接分享预览卡尺寸（微信 / 邮件 / 招聘系统）

# ── 页面 → 章节映射 ────────────────────────────────────────────────
# (PDF 页码, 输出子目录, 单独成图时的文件名)
# 给了文件名 = 该页整页出一张图；给了 None = 该页进对应章节的长图序列，自动切片
PAGES = [
    (1,  "ch0", "cover"),   # 封面
    (2,  "ch0", "about"),   # 关于我
    (3,  "ch0", "toc"),     # 目录
    (4,  "ch1", None),      # 01 APP&AI智能体 —— 章节分隔页
    (5,  "ch1", None),      # 驾享租 · 汽车租赁 APP
    (6,  "ch1", None),      # 时愈 · 职场健康 APP
    (7,  "ch1", None),      # AI 衣橱智搭 APP
    (8,  "ch2", None),      # 02 数据可视化 —— 章节分隔页
    (9,  "ch2", None),
    (10, "ch3", None),      # 03 图标&字体 —— 章节分隔页
    (11, "ch3", None),
    (12, "ch4", None),      # 04 平面&电商 —— 章节分隔页
    (13, "ch4", None),
    (14, "ch4", None),
    (15, "ch4", None),
    (16, "ch4", None),
    (17, "ch4", None),
    (18, "ch4", None),      # 电商设计 · 头戴式耳机
    (19, "ch0", "end"),     # 尾页 Thanks Watching
]


def render_band(doc, page_no, y0_px, y1_px):
    """渲染某页 [y0_px, y1_px) 这段像素高度，返回 PIL Image。"""
    page = doc[page_no - 1]
    scale = SCALE
    clip = fitz.Rect(
        page.rect.x0,
        page.rect.y0 + y0_px / scale,
        page.rect.x1,
        page.rect.y0 + y1_px / scale,
    )
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def save_webp(img, path):
    img.save(path, "WEBP", quality=WEBP_QUALITY, method=6)


def edge_bg(img):
    """取图片左边缘的众数颜色，作为这张图加载完成前的占位底色。

    作品集里深色页和白底页混在一起（比如第 04 章前半是黑底、后半是白底），
    统一给一个占位色必然闪错，所以逐张算。
    """
    w, h = img.size
    strip = img.crop((0, 0, min(4, w), h))
    return "#%02X%02X%02X" % Counter(strip.getdata()).most_common(1)[0][0]


def main():
    if not SRC_PDF.exists():
        sys.exit(f"找不到源文件：{SRC_PDF}")

    doc = fitz.open(SRC_PDF)
    print(f"源文件：{SRC_PDF.name}")
    print(f"页数：{doc.page_count}，渲染倍率：{SCALE}x，WebP 画质：{WEBP_QUALITY}")
    print(f"输出到：{OUT_DIR}\n")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    manifest = {}
    pages = {}
    total_bytes = 0
    t0 = time.time()

    for page_no, chapter, standalone in PAGES:
        if page_no > doc.page_count:
            sys.exit(f"第 {page_no} 页不存在，PDF 只有 {doc.page_count} 页")

        page = doc[page_no - 1]
        px_h = round(page.rect.height * SCALE)
        px_w = round(page.rect.width * SCALE)

        if standalone:
            dest_dir = OUT_DIR / chapter
            dest_dir.mkdir(parents=True, exist_ok=True)
            img = render_band(doc, page_no, 0, px_h)
            out = dest_dir / f"{standalone}.webp"
            save_webp(img, out)
            size = out.stat().st_size
            total_bytes += size
            pages[standalone] = {"src": f"assets/img/{chapter}/{out.name}",
                                 "w": img.width, "h": img.height, "bg": edge_bg(img)}
            print(f"  p{page_no:>2}  {px_w}x{px_h}  →  {chapter}/{out.name}  {size/1024:.0f} KB")
            continue

        # 长页：分带渲染，带边界与切片边界对齐，保证接缝无痕
        tiles = []
        band_start = 0
        while band_start < px_h:
            band_end = min(band_start + BAND_H, px_h)
            band = render_band(doc, page_no, band_start, band_end)
            # 把这一带切成 TILE_H 高的瓦片
            offset = 0
            while offset < band.height:
                piece = band.crop((0, offset, band.width, min(offset + TILE_H, band.height)))
                tiles.append(piece)
                offset += TILE_H
            band_start = band_end

        dest_dir = OUT_DIR / chapter
        dest_dir.mkdir(parents=True, exist_ok=True)
        bucket = manifest.setdefault(chapter, [])
        for piece in tiles:
            # 编号在整个章节内连续，不能每页从 00 重新数——否则后面的页会覆盖前面的
            name = f"tile-{len(bucket):02d}.webp"
            out = dest_dir / name
            save_webp(piece, out)
            total_bytes += out.stat().st_size
            bucket.append({"src": f"assets/img/{chapter}/{name}",
                           "w": piece.width, "h": piece.height, "bg": edge_bg(piece)})
        print(f"  p{page_no:>2}  {px_w}x{px_h}  →  {chapter}  {len(tiles)} 张切片"
              f"（累计 {len(bucket)}）")

    # ── 分享预览卡（og:image）─────────────────────────────────────
    cover = Image.open(OUT_DIR / "ch0" / "cover.webp")
    cw, ch = cover.size
    tw, th = OG_SIZE
    ratio = max(tw / cw, th / ch)
    cover = cover.resize((round(cw * ratio), round(ch * ratio)), Image.LANCZOS)
    left = (cover.width - tw) // 2
    top = (cover.height - th) // 2
    og = cover.crop((left, top, left + tw, top + th))
    og.save(OUT_DIR / "og-cover.jpg", "JPEG", quality=88, optimize=True)
    og_size = (OUT_DIR / "og-cover.jpg").stat().st_size
    total_bytes += og_size
    print(f"\n  og-cover.jpg  {og_size/1024:.0f} KB")

    # ── 清单：供 main.js 构建长图序列 ──────────────────────────────
    js = "// 由 tools/build_tiles.py 自动生成，请勿手改。\n"
    js += "window.PORTFOLIO_TILES = " + json.dumps(manifest, ensure_ascii=False, indent=2) + ";\n"
    js += "window.PORTFOLIO_PAGES = " + json.dumps(pages, ensure_ascii=False, indent=2) + ";\n"
    (ROOT / "assets" / "js").mkdir(parents=True, exist_ok=True)
    (ROOT / "assets" / "js" / "manifest.js").write_text(js, encoding="utf-8")

    n_tiles = sum(len(v) for v in manifest.values())
    print(f"\n完成：{n_tiles} 张章节切片 + 4 张单页 + 1 张预览卡")
    print(f"全站图片总量：{total_bytes/1e6:.1f} MB")
    print(f"耗时：{time.time()-t0:.1f} 秒")


if __name__ == "__main__":
    main()

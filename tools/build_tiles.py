#!/usr/bin/env python3
"""
孙茜作品集 · 网页版 —— 图片导出脚本

把 PDF 的每一页渲染成 WebP，长页自动切片，供 index.html 滚动展示。

每张图会导出三档宽度（1440 / 1920 / 2880），再由 HTML 的 srcset 让浏览器
按自己的屏幕挑一档下载。这样手机只下小图（省流量、解码快），Retina 笔记本
拿到 2880 的真实像素（不糊）。原理见本文件末尾的说明。

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
SRC_PDF = Path("/Users/zaizai/Documents/作品集/全作品集/pdf/9.20/孙茜-UI设计师作品集（未压缩.pdf")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "img"

# 母版按 1.5 倍渲染 = 2880px 宽。不能低于 2880，
# 否则 Retina 屏（1440 CSS px × 2 倍像素）就得靠插值放大，画面发糊。
MASTER_SCALE = 1.5

# 每张图输出的三档宽度。浏览器按「屏幕 CSS 宽 × 像素比」选最小够用的一档：
#   手机 390px@3x  需要 1170  → 拿 1440
#   笔记本 1440@1x 需要 1440  → 拿 1440
#   台式 1920@1x   需要 1920  → 拿 1920
#   Retina 1440@2x 需要 2880  → 拿 2880
TIER_WIDTHS = [1440, 1920, 2880]
DEFAULT_TIER = 1920          # 不支持 srcset 的老浏览器兜底用这档

# 2026-09-24 实测（第 5 页小字段，2880 档）：
#   q80 233 KB → q70 195 KB，省 16%，相对母版 PSNR 仍 50.2 dB
#   （40 dB 以上即视觉无损）；照片页省约 20%。
# 原来写 80 是怕 q76 压糊小字，实测证明这条担心过头了 —— WebP 在高质量段
# 的曲线很平，80 和 70 的差别小到看不出来。反过来也成立：**别指望靠降质量
# 大幅瘦身**。真正的大头是 2880 那一档本身（占全站 50%），要省得多得砍档位。
WEBP_QUALITY = 70

# 切片高度，单位是「母版像素」。3000 @2880 ≈ 2000 @1920，
# 即和旧版同样大小 —— 2880×3000 = 8.6M 像素，远低于 iOS Safari
# 约 16M 的解码上限，安全。
TILE_H = 3000
BAND_H = 15000               # 渲染分带高度，必须是 TILE_H 的整数倍

OG_SIZE = (1200, 630)        # 链接分享预览卡尺寸（微信 / 邮件 / 招聘系统）

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
    """按母版倍率渲染某页 [y0_px, y1_px) 这段高度，返回 PIL Image。"""
    page = doc[page_no - 1]
    clip = fitz.Rect(
        page.rect.x0,
        page.rect.y0 + y0_px / MASTER_SCALE,
        page.rect.x1,
        page.rect.y0 + y1_px / MASTER_SCALE,
    )
    pix = page.get_pixmap(matrix=fitz.Matrix(MASTER_SCALE, MASTER_SCALE), clip=clip)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def edge_bg(img):
    """取图片左边缘的众数颜色，作为这张图加载完成前的占位底色。

    作品集里深色页和白底页混在一起（比如第 04 章前半是黑底、后半是白底），
    统一给一个占位色必然闪错，所以逐张算。
    """
    w, h = img.size
    strip = img.crop((0, 0, min(4, w), h))
    return "#%02X%02X%02X" % Counter(strip.getdata()).most_common(1)[0][0]


def emit(master, dest_dir, stem):
    """把母版图写成三档 WebP，返回清单条目。"""
    srcs = []
    for w in TIER_WIDTHS:
        if w == master.width:
            tier = master
        elif w < master.width:
            tier = master.resize((w, max(1, round(master.height * w / master.width))),
                                 Image.LANCZOS)
        else:
            continue          # 母版没那么宽，跳过（正常不会发生）
        out = dest_dir / f"{stem}-{w}.webp"
        tier.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
        srcs.append((w, f"assets/img/{dest_dir.name}/{out.name}"))

    by_w = dict(srcs)
    default_w = DEFAULT_TIER if DEFAULT_TIER in by_w else srcs[-1][0]
    src_rel = by_w[default_w]
    # 清单里 w/h 用默认档的真实尺寸，供 <img width height> 占位、避免布局跳动
    dw, dh = (master.width, master.height) if default_w == master.width else (
        default_w, round(master.height * default_w / master.width))

    return {
        "src": src_rel,
        "srcset": ", ".join(f"{url} {w}w" for w, url in srcs),
        "hi": by_w[max(by_w)],          # 放大查看时用最大档，看得清细节
        "w": dw,
        "h": dh,
        "bg": edge_bg(master),
    }, [u for _, u in srcs]


def main():
    if not SRC_PDF.exists():
        sys.exit(f"找不到源文件：{SRC_PDF}")

    doc = fitz.open(SRC_PDF)
    print(f"源文件：{SRC_PDF.name}")
    print(f"页数：{doc.page_count}   母版倍率：{MASTER_SCALE}x   档位：{TIER_WIDTHS}   "
          f"WebP 画质：{WEBP_QUALITY}")
    print(f"输出到：{OUT_DIR}\n")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    manifest = {}
    pages = {}
    total_bytes = 0
    tier_bytes = {w: 0 for w in TIER_WIDTHS}
    t0 = time.time()

    for page_no, chapter, standalone in PAGES:
        if page_no > doc.page_count:
            sys.exit(f"第 {page_no} 页不存在，PDF 只有 {doc.page_count} 页")

        page = doc[page_no - 1]
        px_h = round(page.rect.height * MASTER_SCALE)
        px_w = round(page.rect.width * MASTER_SCALE)
        dest_dir = OUT_DIR / chapter
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 本页要输出的所有母版图（单页 = 一整张；长页 = 切片们）
        if standalone:
            masters = [render_band(doc, page_no, 0, px_h)]
            stems = [standalone]
            kind = f"{chapter}/{standalone}"
        else:
            masters, stems = [], []
            bucket_len = len(manifest.get(chapter, []))
            band_start = 0
            while band_start < px_h:
                band_end = min(band_start + BAND_H, px_h)
                band = render_band(doc, page_no, band_start, band_end)
                offset = 0
                while offset < band.height:
                    masters.append(band.crop(
                        (0, offset, band.width, min(offset + TILE_H, band.height))))
                    offset += TILE_H
                band_start = band_end
            # 编号在整个章节内连续，不能每页从 00 重新数——否则后面的页会覆盖前面的
            stems = [f"tile-{bucket_len + i:02d}" for i in range(len(masters))]
            kind = f"{chapter}  {len(masters)} 张切片"

        bucket = manifest.setdefault(chapter, []) if not standalone else None
        for master, stem in zip(masters, stems):
            entry, urls = emit(master, dest_dir, stem)
            if standalone:
                pages[standalone] = entry
            else:
                bucket.append(entry)
            for u in urls:
                sz = (ROOT / u).stat().st_size
                total_bytes += sz
                tier_bytes[int(u.rsplit("-", 1)[1].split(".")[0])] += sz

        print(f"  p{page_no:>2}  {px_w}x{px_h}  →  {kind}（累计 {len(bucket) if bucket else 1}）")

    # ── 分享预览卡（og:image）─────────────────────────────────────
    cover = Image.open(OUT_DIR / "ch0" / "cover-2880.webp")
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
    n_files = n_tiles * len(TIER_WIDTHS) + len(pages) * len(TIER_WIDTHS) + 1
    print(f"\n完成：{n_tiles} 张章节切片 + {len(pages)} 张单页，每张 {len(TIER_WIDTHS)} 档")
    print(f"文件数：{n_files}")
    print(f"仓库图片总量：{total_bytes/1e6:.1f} MB")
    for w in TIER_WIDTHS:
        print(f"    {w:>4} 档：{tier_bytes[w]/1e6:>5.1f} MB   "
              f"（{'手机 / 笔记本' if w == 1440 else '台式' if w == 1920 else 'Retina 屏'}）")
    print(f"耗时：{time.time()-t0:.1f} 秒")


if __name__ == "__main__":
    main()

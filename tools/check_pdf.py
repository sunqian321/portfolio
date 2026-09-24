#!/usr/bin/env python3
"""
换新 PDF 之前先跑这个 —— 判断这份 PDF 够不够清晰。

为什么要先查：
    网页版的图是拿 PDF **重新渲染**成 2880px 宽的母版，所以 PDF 里嵌入图的
    分辨率就是清晰度的天花板。如果 PDF 是「压缩导出」的（导出时把图降采样到
    96~150 DPI），渲染时就得放回去，二次损失 —— 那就是画面发糊的来源。

判断标准：
    满幅的图，宽度至少要 2880px（= 页面宽 1920 × 母版倍率 1.5）。
    达不到就会显示「不够」。

用法：
    python3 tools/check_pdf.py [PDF路径]

不给路径就用 build_tiles.py 里配的那份。
"""

import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("缺少依赖，请先运行：pip3 install --user pymupdf")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tiles import SRC_PDF, MASTER_SCALE, PAGES  # noqa: E402


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_PDF
    if not path.exists():
        sys.exit(f"找不到文件：{path}")

    size_mb = path.stat().st_size / 1e6
    doc = fitz.open(path)
    w_pt, h_pt = doc[0].rect.width, doc[0].rect.height
    need = w_pt * MASTER_SCALE

    print(f"文件：{path.name}")
    print(f"大小：{size_mb:.1f} MB   （大小不影响清晰度，也不进仓库，随便大）")
    print(f"页数：{doc.page_count}")
    print(f"页面：{w_pt:.0f} × {h_pt:.0f} pt")
    print(f"母版倍率 {MASTER_SCALE}x  →  满幅图至少要有 {need:.0f}px 宽\n")

    rows, short = [], []
    for i in range(doc.page_count):
        page = doc[i]
        imgs = page.get_images(full=True)
        if not imgs:
            rows.append((i + 1, "纯矢量", "", "", False))
            continue
        # get_images 返回 (xref, smask, 宽, 高, ...)，注意 im[1] 是 smask 不是宽度
        xref, _smask, iw, ih = max(imgs, key=lambda im: im[2] * im[3])[:4]
        rects = page.get_image_rects(xref)
        pw = max((r.width for r in rects), default=0) if rects else 0
        if not pw:
            rows.append((i + 1, f"{iw}×{ih}", "?", "?", False))
            continue
        # 这张图铺在 pw 点宽的位置上，最终要渲染成 pw × 1.5 像素
        need_here = pw * MASTER_SCALE
        ok = iw >= need_here * 0.95
        short.append(i + 1) if not ok else None
        rows.append((i + 1, f"{iw}×{ih}", f"{pw:.0f} pt",
                     f"{iw / need_here * 100:.0f}%", not ok))

    print(f"{'页':>4}  {'最大嵌入图':>14}  {'铺开的宽度':>10}  {'够清晰度':>8}")
    for no, dim, pw, pct, bad in rows:
        mark = "  ← 不够" if bad else ""
        print(f"{no:>4}  {dim:>14}  {pw:>10}  {pct:>8}{mark}")

    print()
    if short:
        print(f"⚠️  {len(short)} 页的图不够清晰，会被放大：第 {'、'.join(map(str, short))} 页")
        print("    重做这几页时，把里面铺满整页的图导成更大尺寸（≥ "
              f"{need:.0f}px 宽），或导出 PDF 时别勾压缩。")
    else:
        print("✅ 全部页面的图都够清晰，直接跑 build_tiles.py 就行。")

    # 页数/顺序变了的话要同步改 build_tiles.py 里的 PAGES 表
    listed = len(PAGES)
    if doc.page_count != listed:
        print(f"\n⚠️  PDF 有 {doc.page_count} 页，但 build_tiles.py 的 PAGES 表里写着 "
              f"{listed} 条 —— 页数变了，得先更新那张表（第几页属于哪个章节）。")
    else:
        print(f"\n页数与 PAGES 表一致（{listed} 条），不用改脚本。")


if __name__ == "__main__":
    main()

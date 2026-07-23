import os
import glob
import argparse
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

INPUT_FOLDER = "./tailfin"       # folder of source .webp tail logos, named by IATA code
OUTPUT_FOLDER = "./icons"        # where the converted 8x8 .gif files go


def convert(path, out_path, mode="emblem"):
    """
    Converts a source tail logo (.webp) into a high-quality, crisp 8x8 GIF icon for AWTRIX.
    
    Modes:
      - 'emblem': Smart emblem focus (default). Zooms into the actual logo mark inside the tailfin,
                  making symbols (birds, cranes, flags, kapok flowers) large and legible on an 8x8 display.
      - 'fin':    Smart tailfin. Preserves the full tailfin shape while trimming empty margins and centering.
      - 'tight':  Strict bounding box crop of non-transparent content.
    """
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]

    # 1. Base content bounding box (non-white, opaque)
    nonwhite = np.any(rgb < 245, axis=2)
    content = (alpha > 15) & nonwhite
    ys, xs = np.where(content)
    if len(xs) == 0:
        bbox = (0, 0, img.width, img.height)
    else:
        pad = 1
        bbox = (
            int(max(xs.min() - pad, 0)),
            int(max(ys.min() - pad, 0)),
            int(min(xs.max() + pad, img.width)),
            int(min(ys.max() + pad, img.height)),
        )
    cropped = img.crop(bbox)

    # 2. Smart detail/emblem crop if in emblem or fin mode
    if mode in ("emblem", "fin") and cropped.width > 20 and cropped.height > 20:
        c_arr = np.array(cropped)
        c_rgb = c_arr[:, :, :3]
        c_alpha = c_arr[:, :, 3]

        gray = Image.fromarray(c_rgb).convert("L")
        edges = np.array(gray.filter(ImageFilter.FIND_EDGES))
        
        # Erode inner alpha mask to ignore outer boundary of fin against transparent background
        alpha_img = Image.fromarray(c_alpha)
        erosion_px = 19 if mode == "emblem" else 15
        inner_alpha = np.array(alpha_img.filter(ImageFilter.MinFilter(erosion_px))) > 150
        detail_edges = np.where(inner_alpha, edges, 0)

        e_ys, e_xs = np.where(detail_edges > 20)
        if len(e_xs) > 30:
            d_min_x, d_max_x = e_xs.min(), e_xs.max()
            d_min_y, d_max_y = e_ys.min(), e_ys.max()

            padding_ratio = 0.08 if mode == "emblem" else 0.15
            px = max(2, int((d_max_x - d_min_x) * padding_ratio))
            py = max(2, int((d_max_y - d_min_y) * padding_ratio))

            crop_box = (
                max(0, d_min_x - px),
                max(0, d_min_y - py),
                min(cropped.width, d_max_x + px + 1),
                min(cropped.height, d_max_y + py + 1),
            )
            cropped = cropped.crop(crop_box)

    # 3. Composite onto pure black background (AWTRIX display standard)
    black_bg = Image.new("RGBA", cropped.size, (0, 0, 0, 255))
    flat = Image.alpha_composite(black_bg, cropped).convert("RGB")

    # 4. Aspect-ratio preserving target dimensions (fit within 8x8 max)
    w, h = flat.size
    scale = min(8.0 / w, 8.0 / h)
    target_w = max(1, int(round(w * scale)))
    target_h = max(1, int(round(h * scale)))

    # 5. Pre-scaling contrast, color saturation boost & edge sharpening
    flat = ImageEnhance.Color(flat).enhance(1.9)
    flat = ImageEnhance.Contrast(flat).enhance(1.4)
    flat = flat.filter(ImageFilter.UnsharpMask(radius=2.2, percent=190, threshold=2))

    # 6. Multi-stage downscaling (High-res -> 32x32 intermediate -> UnsharpMask -> 8x8 target)
    mid = flat.resize((target_w * 4, target_h * 4), Image.Resampling.LANCZOS)
    mid = mid.filter(ImageFilter.UnsharpMask(radius=1.1, percent=150, threshold=1))
    small = mid.resize((target_w, target_h), Image.Resampling.BOX)

    # 7. Canvas centering on 8x8 black canvas
    canvas = Image.new("RGB", (8, 8), (0, 0, 0))
    ox = (8 - target_w) // 2
    oy = (8 - target_h) // 2
    canvas.paste(small, (ox, oy))

    # 8. Final contrast polish
    canvas = ImageEnhance.Contrast(canvas).enhance(1.2)

    # 9. Clean palette quantization WITHOUT dithering (prevents noisy pixel dots on 8x8 LED matrix)
    final_img = canvas.convert("P", palette=Image.ADAPTIVE, colors=256, dither=Image.Dither.NONE)
    final_img.save(out_path, "GIF")


def main():
    parser = argparse.ArgumentParser(description="Convert tailfin logos to 8x8 AWTRIX icons.")
    parser.add_argument("--mode", choices=["emblem", "fin", "tight"], default="emblem",
                        help="Cropping mode: 'emblem' (zoomed logo mark, default), 'fin' (full tailfin), 'tight' (tight crop)")
    parser.add_argument("--input", default=INPUT_FOLDER, help="Input directory containing .webp files")
    parser.add_argument("--output", default=OUTPUT_FOLDER, help="Output directory for generated .gif icons")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    print(f"Starting batch conversion for AWTRIX (Mode: {args.mode})...")
    converted = 0
    for path in sorted(glob.glob(os.path.join(args.input, "*.webp"))):
        filename = os.path.basename(path)
        iata = os.path.splitext(filename)[0].lower()
        out_path = os.path.join(args.output, f"{iata}_logo.gif")
        try:
            convert(path, out_path, mode=args.mode)
            print(f"OK  {filename} -> {iata}_logo.gif")
            converted += 1
        except Exception as e:
            print(f"FAIL {filename}: {e}")
    print(f"Done! Converted {converted} icons into {args.output}")


if __name__ == "__main__":
    main()


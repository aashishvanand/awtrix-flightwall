import os
import glob
import numpy as np
from PIL import Image, ImageEnhance

INPUT_FOLDER = "./tailfin"       # folder of source .webp tail logos, named by IATA code
OUTPUT_FOLDER = "./icons"        # where the converted 8x8 .gif files go

os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def convert(path, out_path):
    img = Image.open(path).convert("RGBA")

    # crop to the actual logo content first - the source images have padding,
    # and resizing that padding straight down to 8x8 washes the colors out
    arr = np.array(img)
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]
    nonwhite = np.any(rgb < 245, axis=2)
    content = (alpha > 10) & nonwhite
    ys, xs = np.where(content)
    if len(xs) == 0:
        bbox = (0, 0, img.width, img.height)
    else:
        pad = 4
        bbox = (
            int(max(xs.min() - pad, 0)),
            int(max(ys.min() - pad, 0)),
            int(min(xs.max() + pad, img.width)),
            int(min(ys.max() + pad, img.height)),
        )
    cropped = img.crop(bbox)

    # composite onto black, not white - AWTRIX glitches on transparent GIFs,
    # and black blends into the matrix's off-pixels instead of showing a bright square
    black_bg = Image.new("RGBA", cropped.size, (0, 0, 0, 255))
    flat = Image.alpha_composite(black_bg, cropped).convert("RGB")

    small = flat.resize((8, 8), Image.Resampling.LANCZOS)
    small = ImageEnhance.Color(small).enhance(2.2)
    small = ImageEnhance.Contrast(small).enhance(1.6)
    small = ImageEnhance.Brightness(small).enhance(1.05)

    final_img = small.convert("P", palette=Image.ADAPTIVE, colors=16)
    final_img.save(out_path, "GIF")


def main():
    print("Starting batch conversion for AWTRIX...")
    converted = 0
    for path in sorted(glob.glob(os.path.join(INPUT_FOLDER, "*.webp"))):
        filename = os.path.basename(path)
        iata = os.path.splitext(filename)[0].lower()
        out_path = os.path.join(OUTPUT_FOLDER, f"{iata}_logo.gif")
        try:
            convert(path, out_path)
            print(f"OK  {filename} -> {iata}_logo.gif")
            converted += 1
        except Exception as e:
            print(f"FAIL {filename}: {e}")
    print(f"Done! Converted {converted} icons into {OUTPUT_FOLDER}")


if __name__ == "__main__":
    main()

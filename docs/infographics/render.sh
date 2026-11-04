#!/usr/bin/env bash
# Renders source.html into the README's images: each panel, light and dark, at twice its size,
# trimmed to the panel. Needs Google Chrome and the workspace's Pillow (make sync).
#   docs/infographics/render.sh
set -euo pipefail
cd "$(dirname "$0")"
chrome=${CHROME:-google-chrome}
for panel in loop fidelity reply consent results; do
  for theme in light dark; do
    "$chrome" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
      --default-background-color=00000000 --window-size=1280,1200 --virtual-time-budget=3000 \
      --allow-file-access-from-files --screenshot="$PWD/$panel-$theme.png" \
      "file://$PWD/source.html?panel=$panel&theme=$theme" 2>/dev/null
    uv run python -c '
import sys
from pathlib import Path
from PIL import Image

png = Path(sys.argv[1])
with Image.open(png) as image:
    image.crop(image.getbbox()).save(png.with_suffix(".webp"), quality=90, method=6)
png.unlink()
' "$panel-$theme.png"
  done
done

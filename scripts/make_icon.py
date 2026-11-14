"""Convert the supplied app image to Windows icons and window RGBA pixels."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]
with Image.open(root / 'assets/app-icon.jpg') as source:
    image = source.convert('RGBA')
side = max(image.size)
square = Image.new('RGBA', (side, side), 'white')
square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
image = square
image.save(root / 'assets/app.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
(root / 'assets/app-icon.rgba').write_bytes(image.resize((128, 128), Image.Resampling.LANCZOS).tobytes())

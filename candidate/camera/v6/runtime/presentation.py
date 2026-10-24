"""One Unicode reading format shared by terminal and annotated margins."""
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ORDER = ('fuel', 'front', 'battery', 'coolant', 'rear', 'oil')
ROWS = (('fuel', 'coolant'), ('front', 'rear'), ('battery', 'oil'))
MANILA = ZoneInfo('Asia/Manila')
FONT = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
HEADER_HEIGHT, FOOTER_HEIGHT = 74, 170


def header_lines(timestamp, *, capture_available=True):
    if not capture_available:
        return ('CENTRABUS', 'N/A')  # no capture timestamp exists
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('Capture timestamp must be timezone aware')
    return ('CENTRABUS', timestamp.astimezone(MANILA).strftime('%Y-%m-%d %H:%M:%S PHT'))


def value_text(reading):
    value = reading.get('value')
    if reading.get('status') != 'DETECTED' or value is None:
        return 'N/A'
    unit = reading['unit']
    return f'{value:.2f} kg/cm²' if unit in ('kg/cm2', 'kg/cm²') else f'{value:.1f}%'


def reading_text(gauge, reading):
    fields = [value_text(reading)]
    if gauge in ('battery', 'coolant', 'oil'):
        fields.append(reading.get('indicative_condition') or 'N/A')
    angle = reading.get('angle') if reading.get('status') == 'DETECTED' else None
    fields.append(f'{angle:.2f}°' if angle is not None else 'N/A')
    return f'{gauge.upper()}: ' + ' | '.join(fields)


@lru_cache(maxsize=32)
def font(size):
    # Fail explicitly if Unicode rendering is unavailable; never replace °/².
    return ImageFont.truetype(str(FONT), size)


def text_margin(width, height, entries):
    """Render margins only. Never resample or redraw the dashboard pixels."""
    margin = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(margin)
    for x, y, text, size, available_width, color in entries:
        while size > 8 and draw.textlength(text, font=font(size)) > available_width:
            size -= 1
        draw.text((x, y), text, font=font(size), fill=color, anchor='lt')
    return cv2.cvtColor(np.asarray(margin), cv2.COLOR_RGB2BGR)


def apply_margins(image, frame_height, readings, timestamp):
    width = image.shape[1]
    title, captured = header_lines(timestamp)
    image[:HEADER_HEIGHT] = text_margin(width, HEADER_HEIGHT, [
        (12, 7, title, 25, width-24, (255, 230, 0)),
        (12, 41, captured, 21, width-24, (255, 255, 255)),
    ])
    entries = []
    for row, pair in enumerate(ROWS):
        for col, gauge in enumerate(pair):
            entries.append((12 + col*(width//2), 14+row*48,
                            reading_text(gauge, readings[gauge]), 23,
                            width//2-24, (255, 255, 255)))
    image[HEADER_HEIGHT+frame_height:] = text_margin(width, FOOTER_HEIGHT, entries)
    return image

from PIL import Image
import os

SRC_DIR = '/mnt/user-data/uploads'
OUT_DIR = '/mnt/user-data/outputs'

ROD_H = 42
N_STEPS = 16          # medium speed (between the fast/slow test versions)
HOLD_MS = 575
STEP_MS = 60
PAUSE_ROLLED_MS = 200


def load_flag(name):
    return Image.open(f'{SRC_DIR}/guildflag-{name}.png').convert('RGBA')


def pad(img, w, h):
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    x = (w - img.width) // 2
    canvas.paste(img, (x, 0), img)  # top-aligned (rod at top)
    return canvas


def rod_of(img):
    return img.crop((0, 0, img.width, ROD_H))


def cloth_of(img):
    return img.crop((0, ROD_H, img.width, img.height))


def build_frame(W, H, rod_img, cloth_img, cloth_h, visible_frac):
    frame = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    frame.paste(rod_img, (0, 0), rod_img)
    new_h = max(1, int(cloth_h * visible_frac))
    if new_h > 0:
        squashed = cloth_img.resize((W, new_h), Image.LANCZOS)
        frame.paste(squashed, (0, ROD_H), squashed)
    return frame


def to_transparent_p(img_rgba):
    alpha = img_rgba.split()[3]
    rgb = img_rgba.convert('RGB')
    p = rgb.convert('P', palette=Image.ADAPTIVE, colors=255)
    mask = alpha.point(lambda a: 255 if a <= 128 else 0)
    trans_index = 255
    p.paste(trans_index, mask=mask)
    return p, trans_index


def make_roll_gif(name_from, name_to, out_name=None):
    start = load_flag(name_from)
    end = load_flag(name_to)

    W = max(start.width, end.width)
    H = max(start.height, end.height)

    start = pad(start, W, H)
    end = pad(end, W, H)

    # Shared pole so it never shifts between the two flags
    shared_rod = rod_of(start)
    start_cloth = cloth_of(start)
    end_cloth = cloth_of(end)
    cloth_h = H - ROD_H

    frames_rgba = []
    durations = []

    def add(frame, dur):
        frames_rgba.append(frame)
        durations.append(dur)

    # Hold start flag (built with shared rod so it matches roll frames exactly)
    add(build_frame(W, H, shared_rod, start_cloth, cloth_h, 1.0), HOLD_MS)

    # Roll start flag up into the pole
    for i in range(1, N_STEPS + 1):
        frac = 1 - (i / N_STEPS)
        add(build_frame(W, H, shared_rod, start_cloth, cloth_h, frac), STEP_MS)

    # Brief pause at bare pole
    add(build_frame(W, H, shared_rod, start_cloth, cloth_h, 0.0), PAUSE_ROLLED_MS)
    add(build_frame(W, H, shared_rod, end_cloth, cloth_h, 0.0), PAUSE_ROLLED_MS)

    # Unroll end flag down from the pole
    for i in range(1, N_STEPS + 1):
        frac = i / N_STEPS
        add(build_frame(W, H, shared_rod, end_cloth, cloth_h, frac), STEP_MS)

    # Hold end flag
    add(build_frame(W, H, shared_rod, end_cloth, cloth_h, 1.0), HOLD_MS)

    p_frames = []
    trans_idx = 255
    for f in frames_rgba:
        p_img, trans_idx = to_transparent_p(f)
        p_frames.append(p_img)

    out_name = out_name or f'guildswap_{name_from}_{name_to}.gif'
    out_path = os.path.join(OUT_DIR, out_name)
    p_frames[0].save(
        out_path,
        save_all=True,
        append_images=p_frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        transparency=trans_idx,
        optimize=False,
    )
    return out_path


if __name__ == '__main__':
    os.makedirs(OUT_DIR, exist_ok=True)
    pairs = [
        ('ally', 'enemy'),
        ('ally', 'neutral'),
        ('enemy', 'ally'),
        ('enemy', 'neutral'),
        ('neutral', 'ally'),
        ('neutral', 'enemy'),
    ]
    for a, b in pairs:
        path = make_roll_gif(a, b)
        print('saved', path)

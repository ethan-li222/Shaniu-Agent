"""Fixed trusted avatar catalog shared by the server and isolated media worker."""
from .config import ROOT

DEFAULT_AVATAR = 'wav2lip_avatar_long_hair_girl'
SHANIU_AVATAR = 'wav2lip_avatar_shaniu'
SHANIU_OUTFITS = {'cool': '银色战衣', 'happy': '红色战衣', 'gentle': '红色战衣', 'shy': '红色战衣'}
AVATARS = [
    dict(id=DEFAULT_AVATAR, name='长发女生'),
    dict(id='wav2lip_avatar_female_model', name='女性模特'),
    dict(id='wav2lip_avatar_glass_man', name='眼镜男生'),
    dict(id=SHANIU_AVATAR, name='傻妞 · 原剧换装'),
]
DOWNLOADABLE_AVATARS = [item for item in AVATARS if item['id'] != 'wav2lip_avatar_shaniu']


def avatar_root(avatar_id):
    if avatar_id not in {item['id'] for item in AVATARS}:
        raise ValueError('未知人物形象')
    return ROOT / 'data/avatar' / avatar_id


def avatar_ready(avatar_id):
    root = avatar_root(avatar_id)
    return (root / 'coords.pkl').is_file() and all(
        (root / name).is_dir() and any(p.suffix.lower() in {'.jpg', '.jpeg', '.png'} for p in (root / name).iterdir())
        for name in ('full_imgs', 'face_imgs'))


def avatar_asset_root(avatar_id, emotion='cool'):
    root = avatar_root(avatar_id)
    custom = ROOT / 'data/avatar/emotions' / emotion if avatar_id == DEFAULT_AVATAR else root / 'emotions' / emotion
    if emotion not in {'cool', 'happy', 'gentle', 'shy'}:
        raise ValueError('未知表情状态')
    return custom if all((custom / p).exists() for p in ('coords.pkl', 'full_imgs', 'face_imgs')) else root


def avatar_preview(avatar_id, emotion='cool'):
    root = avatar_asset_root(avatar_id, emotion)
    preview = root / 'preview.jpg'
    if preview.is_file():
        return preview
    images = sorted(p for p in (root / 'full_imgs').iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png'})
    return images[0] if images else None

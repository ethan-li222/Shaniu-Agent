import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    database: Path = ROOT / "data/shaniu.sqlite3"
    provider: str = "demo"
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = field(default="", repr=False)
    model: str = "qwen-plus"
    stream: bool = True
    max_tokens: int = 1024
    tool_max_tokens: int = 4096
    avatar_path: Path = ROOT.parent / "AIAvatar"
    media_python: Path = ROOT / '.venv-media/Scripts/python.exe'
    tts_provider: str = 'windows'
    tts_voice: str = 'zh-CN-XiaoxiaoNeural'
    tts_fallback: bool = True
    tts_natural: bool = True
    moneyprinter_enabled: bool = False
    moneyprinter_url: str = 'http://127.0.0.1:8080'

    @property
    def speech_config(self):
        return dict(provider=self.tts_provider, voice=self.tts_voice, fallback=self.tts_fallback, natural=self.tts_natural)

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        settings = cls(
            provider=os.getenv("SHANIU_PROVIDER", "demo"),
            api_base=os.getenv("SHANIU_API_BASE", cls.api_base).rstrip("/"),
            api_key=os.getenv("SHANIU_API_KEY", ""),
            model=os.getenv("SHANIU_MODEL", "qwen-plus"),
            stream=os.getenv("SHANIU_STREAM", "true").lower() == "true",
            max_tokens=int(os.getenv("SHANIU_MAX_TOKENS", "1024")),
            tool_max_tokens=int(os.getenv('SHANIU_TOOL_MAX_TOKENS', '4096')),
            avatar_path=(ROOT / os.getenv("SHANIU_AIAVATAR_PATH", "../AIAvatar")).resolve(),
            tts_provider=os.getenv('SHANIU_TTS_PROVIDER', 'windows').lower(),
            tts_voice=os.getenv('SHANIU_TTS_VOICE', 'zh-CN-XiaoxiaoNeural'),
            tts_fallback=os.getenv('SHANIU_TTS_FALLBACK', 'true').lower() == 'true',
            tts_natural=os.getenv('SHANIU_TTS_NATURAL', 'true').lower() == 'true',
            moneyprinter_enabled=os.getenv('SHANIU_MPT_ENABLED', 'false').lower() == 'true',
            moneyprinter_url=os.getenv('SHANIU_MPT_URL', cls.moneyprinter_url).rstrip('/'),
        )
        if settings.provider not in {"demo", "compatible"}:
            raise ValueError("SHANIU_PROVIDER 必须是 demo 或 compatible")
        if settings.tts_provider not in {'windows', 'edge'}:
            raise ValueError('SHANIU_TTS_PROVIDER 必须是 windows 或 edge')
        if settings.provider == "compatible" and not settings.api_key:
            raise ValueError("真实模型模式需要配置 SHANIU_API_KEY")
        if not 64 <= settings.max_tokens <= 8192:
            raise ValueError("SHANIU_MAX_TOKENS 必须在 64–8192 之间")
        if not 256 <= settings.tool_max_tokens <= 8192:
            raise ValueError('SHANIU_TOOL_MAX_TOKENS 必须在 256–8192 之间')
        if settings.api_base.endswith('/chat/completions'):
            settings.api_base = settings.api_base.removesuffix('/chat/completions')
        parsed = urlsplit(settings.api_base)
        if settings.provider == 'compatible' and not (
            parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'})
        ):
            raise ValueError('远程模型接口必须使用 HTTPS；本地模型可用 HTTP')
        return settings

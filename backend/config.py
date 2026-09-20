"""应用配置。

集中管理所有环境变量与路径（目标文档 §20：环境变量集中管理、配置模型统一）。
优先级：环境变量（LECTURELY_ 前缀）> .env 文件 > 默认值。
运行时用户在设置页修改的配置存于 settings 表，与此处静态配置叠加。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Lecturely-App 根目录（backend/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LECTURELY_", env_file=str(BASE_DIR / ".env"), extra="ignore"
    )

    # 服务
    host: str = "127.0.0.1"
    port: int = 8765
    api_token: str = ""  # 绑定非 localhost 时必须设置

    # 路径
    data_dir: Path = DEFAULT_DATA_DIR

    # 模型下载镜像（huggingface.co 不可达时使用 hf-mirror）
    hf_endpoint: str = "https://hf-mirror.com"

    # 上传限制
    upload_max_mb: int = 512

    # ASR 默认（轻量默认配置，目标文档 §11；CPU 实时优先 base，可在设置页升级 small）
    asr_model: str = "base"
    asr_backend: str = "faster-whisper"
    asr_policy: str = "localagreement"
    asr_device: str = "auto"  # auto | cpu | cuda
    asr_compute_type: str = "int8"

    # 目录派生属性
    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_dir / 'lecturely.db'}"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.database_dir,
            self.recordings_dir,
            self.exports_dir,
            self.models_dir,
            self.uploads_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def is_local_only(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")


@lru_cache
def get_config() -> AppConfig:
    cfg = AppConfig()
    cfg.ensure_dirs()
    return cfg

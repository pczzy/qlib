from pathlib import Path
import tarfile
from loguru import logger

from utils import get_local_data_date

# 路径配置：自动识别不同机器的 Home 目录
HOME = Path.home()
# 源目录: ~/.qlibAssistant/mlruns
TARGET_PARENT = HOME / ".qlibAssistant"
SOURCE_DIR = TARGET_PARENT / "mlruns"
# 存放压缩包的目录: ../model_pkl
BACKUP_DIR = Path("../model_pkl").resolve()


def compress_mlruns(provider_uri: str) -> None:
    """压缩 ~/.qlibAssistant/mlruns 到 ../model_pkl/mlruns_yyyyMMdd_HHmm.tar.gz"""
    if not SOURCE_DIR.exists():
        print(f"⚠️ 找不到路径: {SOURCE_DIR}，跳过压缩。")
        return

    # 确保目标文件夹存在
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # 精确到分钟的时间戳（来自本地数据日历）
    timestamp = get_local_data_date(provider_uri).strip()
    archive_path = BACKUP_DIR / f"mlruns_{timestamp}.tar.gz"

    print(f"📦 正在打包: {SOURCE_DIR} -> {archive_path.name}")
    with tarfile.open(archive_path, "w:gz") as tar:
        # arcname="mlruns" 保证解压后文件夹名字正确
        tar.add(SOURCE_DIR, arcname=SOURCE_DIR.name)
    print("✅ 压缩完成。")


def decompress_mlruns() -> None:
    """解压 ../model_pkl/ 下所有 mlruns_*.tar.gz 到 ~/.qlibAssistant/"""
    if not BACKUP_DIR.exists():
        print(f"⚠️ 备份目录 {BACKUP_DIR} 不存在。")
        return

    # 确保父目录 ~/.qlibAssistant 存在
    TARGET_PARENT.mkdir(parents=True, exist_ok=True)

    # 获取目录下所有匹配的压缩包
    archives = list(BACKUP_DIR.glob("mlruns_*.tar.gz"))

    if not archives:
        print("ℹ️ 没有发现可供恢复的压缩包。")
        return

    for archive in archives:
        print(f"📂 正在解压: {archive.name} -> {TARGET_PARENT}")
        with tarfile.open(archive, "r:gz") as tar:
            # 解压到 ~/.qlibAssistant/，会自动还原 mlruns 文件夹
            tar.extractall(path=TARGET_PARENT)

    print("✅ 恢复完成。")


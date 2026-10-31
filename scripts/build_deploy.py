"""一键生成 PythonAnywhere 部署包。

为什么要这个脚本：
    改完代码忘了重新打包，直接上传旧 zip —— 线上还是旧版本，
    但本地看起来一切正常。这种错很难发现，所以固定成一条命令：

        python scripts/build_deploy.py

    它会把当前代码 + 预建索引 + 别名表 + 页面打进 tmp/litrag-deploy.zip，
    并打印每个关键文件的哈希，方便和线上比对。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "litrag-deploy.zip"

SINGLE_FILES = [
    "wsgi.py",
    "app.py",
    ".index/index.json",
    "data/doc_aliases.json",
    "data/radar_items.json",
    "data/schools.json",
]
GLOBS = [
    "litrag/*.py",
    "data/docs/*.md",
    "web/*.html",
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in SINGLE_FILES:
            path = ROOT / name
            if path.exists():
                archive.write(path, name)
            else:
                print(f"[警告] 缺少文件：{name}")

        for pattern in GLOBS:
            for path in ROOT.glob(pattern):
                if path.is_file():
                    archive.write(path, str(path.relative_to(ROOT)).replace("\\", "/"))

    with zipfile.ZipFile(OUT) as archive:
        names = archive.namelist()
        print(f"打包完成：{OUT}")
        print(f"大小：{OUT.stat().st_size / 1024:.0f} KB   文件数：{len(names)}\n")
        print("关键文件指纹（和线上对不上，就说明包是旧的）：")
        for name in ["web/index.html", "app.py", "litrag/query.py", "data/radar_items.json", "data/schools.json", ".index/index.json"]:
            if name not in names:
                print(f"  {name:24s} 不在包里")
                continue
            digest = hashlib.sha256(archive.read(name)).hexdigest()[:16]
            print(f"  {name:24s} {digest}")

    print("\n下一步：上传到 PythonAnywhere → Files → Upload a file → 覆盖")
    print("      然后在 Bash 里执行：unzip -o -q litrag-deploy.zip -d litrag")
    print("      最后 Web → Reload")


if __name__ == "__main__":
    main()

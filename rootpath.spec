# PyInstaller spec of the rootpath binary: one file that carries Python, the package and its dependencies,
# and the files Rootpath reads at run time, laid out under sys._MEIPASS as in the repo (rootpath/paths.py).
# scripts/build_binary.py builds it.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

from rootpath import paths

repo = Path(SPECPATH)
console = repo / "web" / "dist"

# The demo without what it writes, the agent's prompts, and the console page with its assets.
datas = [(str(path), str(Path("demo", rel).parent)) for rel, path in paths.demo_files(repo / "demo").items()]
datas += [
    (str(repo / "prompts"), "prompts"),
    (str(console / "console.html"), "web/dist"),
    (str(console / "assets"), "web/dist/assets"),
]
# Package versions read at run time: `rootpath --version`, FastMCP's on import, the FalkorDB and Redis clients'.
for dist in ("rootpath", "mcp", "falkordb", "redis"):
    datas += copy_metadata(dist)

a = Analysis(
    [str(repo / "scripts" / "binary_main.py")],
    pathex=[str(repo)],
    datas=datas,
    # The CLI imports its commands' modules by name (`rootpath record`, uvicorn's "rootpath.web:app").
    hiddenimports=collect_submodules("rootpath"),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="rootpath",
    console=True,
    upx=False,
)

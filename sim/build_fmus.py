"""Export each FWS component as an FMI 2.0 co-simulation FMU with OpenModelica.

Usage:  python sim/build_fmus.py
Needs OpenModelica (omc). Set OMC=path\\to\\omc.exe if it is not in the default location.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_FILE = HERE / "modelica" / "FWS.mo"
OUT = HERE / "fmus"
COMPONENTS = [
    "GB300Load", "TCSLoop", "CDU", "Pipe",
    "StratosGIGA2Pump", "LoopHydraulics", "Chiller", "BMSController",
]
DEFAULT_OMC = r"C:\Program Files\OpenModelica1.26.3-64bit\bin\omc.exe"


def main() -> int:
    omc = os.environ.get("OMC") or shutil.which("omc") or DEFAULT_OMC
    if not Path(omc).exists():
        print(f"omc not found ({omc}); set OMC", file=sys.stderr)
        return 1
    OUT.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="fws_fmu_"))
    lines = [
        'loadModel(Modelica, {"4.1.0"}); getErrorString();',
        f'loadFile("{MODEL_FILE.as_posix()}"); getErrorString();',
        'setCommandLineOptions("-d=-disableDirectionalDerivatives"); getErrorString();',
    ]
    for c in COMPONENTS:
        lines.append(
            f'buildModelFMU(FWS.{c}, version="2.0", fmuType="cs", fileNamePrefix="FWS_{c}", '
            f'platforms={{"static"}}); getErrorString();'
        )
    script = work / "export.mos"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copy(script, HERE / "export.mos")
    res = subprocess.run([omc, str(script)], cwd=work, capture_output=True, text=True)
    print(res.stdout[-6000:])
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
    ok = True
    for c in COMPONENTS:
        f = work / f"FWS_{c}.fmu"
        if f.exists():
            shutil.copy(f, OUT / f.name)
            print(f"  built {f.name}  ({f.stat().st_size // 1024} kB)")
        else:
            ok = False
            print(f"  MISSING FWS_{c}.fmu", file=sys.stderr)
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

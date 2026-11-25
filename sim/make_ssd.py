"""Write sim/FWS_Loop1.ssd (SSP 1.0 system structure) from sim/system.py.

Component names are aif_location values (stable functional locations), not USD prim paths:
the USD binding (usd/simulation.usda) maps prims to these components.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from system import COMPONENTS, CONNECTIONS, RESISTANCE_SUM  # noqa: E402

SSD = "http://ssp-standard.org/SSP1/SystemStructureDescription"
SSC = "http://ssp-standard.org/SSP1/SystemStructureCommon"
ET.register_namespace("ssd", SSD)
ET.register_namespace("ssc", SSC)


def q(ns, tag):
    return f"{{{ns}}}{tag}"


def main():
    root = ET.Element(q(SSD, "SystemStructureDescription"), version="1.0", name="FWS_Loop1")
    system = ET.SubElement(root, q(SSD, "System"), name="AIF-01/FWS/L1")
    elements = ET.SubElement(system, q(SSD, "Elements"))
    for name, (fmu_type, prim, loc, params) in COMPONENTS.items():
        comp = ET.SubElement(elements, q(SSD, "Component"), name=name,
                             source=f"fmus/FWS_{fmu_type}.fmu", type="application/x-fmu-sharedlibrary")
        if isinstance(params, str):
            pb = ET.SubElement(comp, q(SSD, "ParameterBindings"))
            ET.SubElement(pb, q(SSD, "ParameterBinding"), source=params)
        ann = ET.SubElement(ET.SubElement(comp, q(SSC, "Annotations")), q(SSC, "Annotation"),
                            type="org.example.usdBinding")
        ET.SubElement(ann, "usdBinding", aif_location=loc, usdPrim=prim)
    conns = ET.SubElement(system, q(SSD, "Connections"))
    for src, dst in CONNECTIONS:
        sn, sv = src.split(".")
        dn, dv = dst.split(".")
        ET.SubElement(conns, q(SSD, "Connection"), startElement=sn, startConnector=sv,
                      endElement=dn, endConnector=dv)
    note = ET.SubElement(ET.SubElement(system, q(SSC, "Annotations")), q(SSC, "Annotation"),
                         type="org.example.sumNode")
    ET.SubElement(note, "sum", target="Hydraulics.R", terms=" + ".join(RESISTANCE_SUM))
    ET.indent(root)
    out = HERE / "FWS_Loop1.ssd"
    ET.ElementTree(root).write(out, xml_declaration=True, encoding="UTF-8")
    print("wrote", out)


if __name__ == "__main__":
    main()

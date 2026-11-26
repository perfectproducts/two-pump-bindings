# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Source-neutral asset view (the "passport" a client shows for one physical asset).

Both metadata sources (AAS server, OpenUSD twin documents) first flatten their data to the same
element map, then build the view here, so a client cannot tell them apart except by `source`.

Element map: {"Nameplate/SerialNumber": Element, "Nameplate/ManufacturerName@en": Element, ...}
  where paths are idShort paths and `src` says whether the value is the instance's own ("instance")
  or comes from the product type ("type": AAS derivedFrom / USD specializes), i.e. inferred.
"""
from dataclasses import dataclass
import re
from typing import Optional


@dataclass
class Element:
    model: str                       # Property | MultiLanguageProperty | File | SubmodelElementCollection | Submodel
    value: Optional[str] = None      # files: content type
    src: str = 'instance'            # instance | type
    unit: Optional[str] = None
    file: Optional[str] = None       # opaque file key (served by the client at /asset-file?k=<key>)
    href: Optional[str] = None       # linked file: absolute URL (e.g. a manufacturer document), not proxied


NAMEPLATE = (
    ('Manufacturer', 'Nameplate/ManufacturerName@en'),
    ('Address', 'Nameplate/AddressInformation'),                       # composed from Street, Zipcode, ...
    ('GTIN', 'TechnicalData/TechnicalPropertyAreas/*/GTIN'),           # found in the technical property areas
    ('Product', 'Nameplate/ManufacturerProductDesignation@en'),
    ('Product type', 'Nameplate/ManufacturerProductType'),
    ('Article no.', 'Nameplate/ProductArticleNumberOfManufacturer'),
    ('Serial no.', 'Nameplate/SerialNumber'),
    ('Year of construction', 'Nameplate/YearOfConstruction'),
    ('Date of manufacture', 'Nameplate/DateOfManufacture'),
    ('ID link (IEC 61406)', 'Nameplate/URIOfTheProduct'),
)
PCF = 'CarbonFootprint/ProductCarbonFootprints'


def _items(elements, prefix):
    """List item keys under prefix (t0.. from the product type, s0.. from the serial), type first, in order."""
    return sorted(_children(elements, prefix), key=lambda k: (k[:1] != 't', int(k[1:]) if k[1:].isdigit() else 0, k))


def _address(get):
    """Nameplate 3.0 AddressInformation -> one line ('Wilopark 1, 44263 Dortmund, DE')."""
    p = {k: get(f'Nameplate/AddressInformation/{k}@en') for k in ('Street', 'Zipcode', 'CityTown', 'NationalCode')}
    if not any(p.values()):
        return None
    v = {k: (e.value if e else '') for k, e in p.items()}
    return {'label': 'Address', 'value': f"{v['Street']}, {v['Zipcode']} {v['CityTown']}, {v['NationalCode']}".strip(', '),
            'unit': None, 'src': next(e for e in p.values() if e).src}


def _label(id_short):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', id_short).replace('P C F', 'PCF')


def fmt(value):
    """Same text for the same number from either source ('309.0', 309.0 -> '309').
    Digit-only strings (article numbers, years) are identifiers and stay as they are."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f'{value:.12g}'
    if isinstance(value, str) and re.fullmatch(r'-?\d+\.\d+', value.strip()):
        return f'{float(value):.12g}'
    return value


def _row(label, e, unit=None):
    return {'label': label, 'value': fmt(e.value) if e.model == 'Property' else e.value,
            'unit': unit or e.unit, 'src': e.src}


def _children(elements, prefix):
    """Direct children idShorts of a path, in first-seen order."""
    seen = []
    for path in elements:
        if path.startswith(prefix + '/'):
            head = path[len(prefix) + 1:].split('/')[0].split('@')[0]
            if head not in seen:
                seen.append(head)
    return seen


def file_url(e):
    if e and e.href:
        return e.href
    return f'/asset-file?k={e.file}' if e and e.file else None


def build(asset_id, elements, source, trace, shell_id=None):
    """The passport view. source = {'kind': 'aas'|'twin', 'label': ..., 'detail': ...}."""
    if not elements:
        return {'asset_id': asset_id, 'status': 'unresolved', 'source': source, 'trace': trace}
    get = elements.get
    first = lambda prefix: next(iter(_items(elements, prefix)), None)   # noqa: E731
    areas = [f'TechnicalData/TechnicalPropertyAreas/{k}' for k in _items(elements, 'TechnicalData/TechnicalPropertyAreas')]
    gtin = next((get(f'{a}/GTIN') for a in areas if get(f'{a}/GTIN')), None)
    nameplate = []
    for label, path in NAMEPLATE:
        e = _address(get) if label == 'Address' else gtin if label == 'GTIN' else get(path)
        if e:
            nameplate.append(e if isinstance(e, dict) else _row(label, e))
    technical = [_row(_label(k), get(f'{a}/{k}')) for a in areas for k in _children(elements, a)
                 if get(f'{a}/{k}') and k != 'GTIN']
    technical.sort(key=lambda r: r['label'])     # source-neutral order (USD lists properties sorted)
    documents = []
    for k in _items(elements, 'HandoverDocumentation/Documents'):     # IDTA 02004-2: Documents/[]/DocumentVersions/[]
        base = f'HandoverDocumentation/Documents/{k}'
        ver = f'{base}/DocumentVersions/{first(f"{base}/DocumentVersions")}'
        f = get(f'{ver}/DigitalFiles/{first(f"{ver}/DigitalFiles")}')
        title = get(f'{ver}/Title@en')
        cls = get(f'{base}/DocumentClassifications/{first(f"{base}/DocumentClassifications")}/ClassName@en')
        documents.append({'title': title.value if title else k, 'class': cls.value if cls else None,
                          'url': file_url(f), 'src': (f or title or get(base)).src})
    markings, label = [], None
    for k in _items(elements, 'Nameplate/Markings'):
        name = get(f'Nameplate/Markings/{k}/MarkingName')
        if name and '61406' in str(name.value):          # the serial's ID-link marking: the DataMatrix label
            label = get(f'Nameplate/Markings/{k}/MarkingFile')
        elif name:
            markings.append(name.value)
    materials = []
    for k in sorted(_children(elements, 'MaterialComposition')):
        base = f'MaterialComposition/{k}'
        name, material, mass = get(f'{base}/ComponentName'), get(f'{base}/MaterialName'), get(f'{base}/ComponentMass')
        if name:
            materials.append({'component': name.value, 'material': getattr(material, 'value', None),
                              'mass': fmt(mass.value) if mass else None, 'unit': getattr(mass, 'unit', None),
                              'src': name.src})
    val = lambda path: getattr(get(path), 'value', None)   # noqa: E731
    sim = f'SimulationModels/{first("SimulationModels")}'          # IDTA 02005-1-1: SimulationModel01
    mfv = next((k for k in _children(elements, f'{sim}/ModelFile') if k.startswith('ModelFileVersion')), None)
    model = get(f'{sim}/ModelFile/{mfv}/DigitalFile') if mfv else None
    params = get(f'{sim}/ParamFile')                                # the serial's calibration (instance shell)
    notes = val(f'{sim}/ModelFile/{mfv}/ModelFileReleaseNotesTxt@en') or ''
    ports = [f'{sim}/Ports/{k}' for k in _children(elements, f'{sim}/Ports')]
    names = lambda c: ', '.join(val(f'{p}/VariableName') for p in ports if val(f'{p}/VariableCausality') == c) or None  # noqa: E731
    simulation = None if model is None else {
        'model': {'url': file_url(model), 'src': model.src,
                  'standard': val(f'{sim}/ModelFile/ModelFileType'),
                  'tool': (re.search(r'Generated by ([^;]+)', notes) or [None, None])[1],
                  'platform': (re.search(r'binaries: ([^.]+)', notes) or [None, None])[1],
                  'inputs': names('input'), 'outputs': names('output')},
        'parameters': None if params is None else {
            'url': file_url(params), 'src': params.src, 'applies_to': asset_id if params.src == 'instance' else None,
            'values': None},
    }
    # IDTA 02023-1-0: ProductCarbonFootprints/[]; the serial's own footprint (s*) wins over the type's (t*)
    pcfs = _items(elements, PCF)
    own = next((k for k in pcfs if k.startswith('s')), None)
    pk = f'{PCF}/{own or (pcfs[0] if pcfs else "")}'
    pcf = get(f'{pk}/PcfCO2eq') if pcfs else None
    phases = ', '.join(val(f'{pk}/LifeCyclePhases/{k}') for k in _items(elements, f'{pk}/LifeCyclePhases')) or None
    image = get(f'TechnicalData/GeneralInformation/ProductImages/{first("TechnicalData/GeneralInformation/ProductImages")}/ImageFile')
    designation = get('Nameplate/ManufacturerProductDesignation@en')
    serial = get('Nameplate/SerialNumber')
    return {
        'asset_id': asset_id, 'shell_id': shell_id, 'status': 'resolved', 'source': source, 'trace': trace,
        'title': designation.value if designation else asset_id,
        'serial': serial.value if serial else None,
        'image': {'url': file_url(image), 'src': image.src} if image else None,
        'id_label': {'url': file_url(label), 'src': label.src} if label else None,
        'nameplate': nameplate, 'technical': technical, 'documents': documents, 'markings': markings,
        'simulation': simulation, 'materials': materials,
        'pcf': None if pcf is None else {
            'value': fmt(pcf.value), 'unit': 'kg CO2e', 'src': pcf.src,
            'basis': 'instance (this serial)' if pcf.src == 'instance' else 'type (product average)',
            'phases': phases,
            'note': None if pcf.src == 'instance' else
            'No CarbonFootprint for this serial: product-type value shown (inferred).'},
        'counts': {'instance': sum(e.src == 'instance' for e in elements.values()),
                   'type': sum(e.src == 'type' for e in elements.values())},
    }

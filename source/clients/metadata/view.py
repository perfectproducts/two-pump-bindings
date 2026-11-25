# SPDX-License-Identifier: Apache-2.0
# New file for the two-pump adaptation of the NVIDIA Composable Bindings reference sample.

"""Source-neutral asset view (the "passport" a client shows for one physical asset).

Both metadata sources (AAS server, OpenUSD twin documents) first flatten their data to the same
element map, then build the view here, so a client cannot tell them apart except by `source`.

Element map: {"Nameplate/SerialNumber": Element, "Nameplate/ManufacturerName@en": Element, ...}
  where paths are idShort paths and `src` says whether the value is the instance's own ("instance")
  or comes from the product type ("type": AAS derivedFrom / USD reference), i.e. inferred.
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
    ('Address', 'Nameplate/AddressOfManufacturer@en'),
    ('GTIN', 'TechnicalData/GeneralInformation/GTIN'),
    ('Product', 'Nameplate/ManufacturerProductDesignation@en'),
    ('Product type', 'Nameplate/ManufacturerProductType'),
    ('Article no.', 'Nameplate/ProductArticleNumberOfManufacturer'),
    ('Serial no.', 'Nameplate/SerialNumber'),
    ('Year of construction', 'Nameplate/YearOfConstruction'),
    ('Date of manufacture', 'Nameplate/DateOfManufacture'),
    ('ID link (IEC 61406)', 'Nameplate/URIOfTheProduct'),
)
PCF = 'CarbonFootprint/ProductCarbonFootprint'


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
    nameplate = [_row(label, get(path)) for label, path in NAMEPLATE if get(path)]
    technical = [_row(_label(k), get(f'TechnicalData/TechnicalProperties/{k}'))
                 for k in _children(elements, 'TechnicalData/TechnicalProperties')
                 if get(f'TechnicalData/TechnicalProperties/{k}')]
    technical.sort(key=lambda r: r['label'])     # source-neutral order (USD lists properties sorted)
    documents = []
    for k in sorted(_children(elements, 'HandoverDocumentation')):
        base = f'HandoverDocumentation/{k}'
        f = get(f'{base}/DigitalFile')
        title = get(f'{base}/Title@en')
        cls = get(f'{base}/DocumentClassName')
        documents.append({'title': title.value if title else _label(k), 'class': cls.value if cls else None,
                          'url': file_url(f), 'src': (f or title or get(base)).src})
    markings = [get(f'Nameplate/Markings/{k}/MarkingName').value for k in sorted(_children(elements, 'Nameplate/Markings'))
                if get(f'Nameplate/Markings/{k}/MarkingName') and k != 'IdentificationLink']
    pcf = get(f'{PCF}/PCFCO2eq')
    image = get('TechnicalData/GeneralInformation/ProductImage')
    label = get('Nameplate/Markings/IdentificationLink/MarkingFile')
    designation = get('Nameplate/ManufacturerProductDesignation@en')
    serial = get('Nameplate/SerialNumber')
    return {
        'asset_id': asset_id, 'shell_id': shell_id, 'status': 'resolved', 'source': source, 'trace': trace,
        'title': designation.value if designation else asset_id,
        'serial': serial.value if serial else None,
        'image': {'url': file_url(image), 'src': image.src} if image else None,
        'id_label': {'url': file_url(label), 'src': label.src} if label else None,
        'nameplate': nameplate, 'technical': technical, 'documents': documents, 'markings': markings,
        'pcf': None if pcf is None else {
            'value': fmt(pcf.value), 'unit': 'kg CO2e', 'src': pcf.src,
            'basis': getattr(get(f'{PCF}/ValueBasis'), 'value', None),
            'phases': getattr(get(f'{PCF}/PCFLifePhase'), 'value', None),
            'note': None if pcf.src == 'instance' else
            'No CarbonFootprint for this serial: product-type value shown (inferred).'},
        'counts': {'instance': sum(e.src == 'instance' for e in elements.values()),
                   'type': sum(e.src == 'type' for e in elements.values())},
    }

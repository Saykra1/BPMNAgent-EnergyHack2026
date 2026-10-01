"""Validation of the produced file against the official BPMN 2.0 XSD (OMG, 20100524)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

SCHEMA_DIR = Path(__file__).parent / "schemas"


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "BPMN20.xsd")))


def validate_xsd(xml: str) -> list[str]:
    """Return a list of XSD errors (empty list = valid)."""
    try:
        doc = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError as e:
        return [f"XML не разбирается: {e}"]
    schema = _schema()
    if schema.validate(doc):
        return []
    return [f"строка {e.line}: {e.message}" for e in schema.error_log]

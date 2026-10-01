"""Validation of the produced file against the official BPMN 2.0 XSD (OMG, 20100524)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

SCHEMA_DIR = Path(__file__).parent / "schemas"


class _SchemaResolver(etree.Resolver):
    """Serve the bundled XSD files from memory: libxml2 is not given any file paths,
    so non-ASCII directories (e.g. on Windows) do not break schema loading."""

    def resolve(self, url, pubid, context):
        name = url.replace("\\", "/").rsplit("/", 1)[-1]
        f = SCHEMA_DIR / name
        if f.exists():
            return self.resolve_string(f.read_bytes(), context, base_url=f"bpmn-schema:/{name}")
        return None


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    parser = etree.XMLParser()
    parser.resolvers.add(_SchemaResolver())
    doc = etree.fromstring((SCHEMA_DIR / "BPMN20.xsd").read_bytes(), parser, base_url="bpmn-schema:/BPMN20.xsd")
    return etree.XMLSchema(etree.ElementTree(doc))


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

from __future__ import annotations

import httpx
import pytest

from genesis_evidence.literature.grobid import GrobidClient, GrobidError, GrobidTeiParser

GROBID_TEI = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a" type="main">Protein and healthy ageing</title></titleStmt>
      <sourceDesc><biblStruct><idno type="DOI">10.1000/grobid</idno></biblStruct></sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract><p>A randomized controlled trial in older adults.</p></abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div><head>Methods</head><p>Participants received protein or placebo.</p></div>
      <div><head>Results</head><p>Protein significantly improved strength.</p></div>
      <figure type="table">
        <label>Table 1</label><head>Baseline</head>
        <table><row><cell>Age</cell></row></table>
      </figure>
    </body>
    <back>
      <listBibl>
        <biblStruct>
          <analytic><title>Prior study</title></analytic>
          <idno type="DOI">10.1/ref</idno>
        </biblStruct>
      </listBibl>
    </back>
  </text>
</TEI>
"""


def test_grobid_tei_parser_preserves_structure() -> None:
    document = GrobidTeiParser().parse(GROBID_TEI)

    assert document.title == "Protein and healthy ageing"
    assert document.identifiers["doi"] == "10.1000/grobid"
    assert [section.title for section in document.sections] == ["Methods", "Results"]
    assert document.tables[0].caption == "Baseline"
    assert document.references[0].doi == "10.1/ref"
    assert document.to_dict()["source_format"] == "grobid_tei"


def test_grobid_client_posts_pdf_and_rejects_non_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/processFulltextDocument"
        assert request.method == "POST"
        assert b"%PDF-1.4" in request.content
        return httpx.Response(200, content=GROBID_TEI)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with GrobidClient("http://grobid.test", client=client) as grobid:
        assert grobid.process_pdf(b"%PDF-1.4\ntest") == GROBID_TEI
        with pytest.raises(GrobidError, match="not a PDF"):
            grobid.process_pdf(b"not-pdf")


def test_grobid_parser_rejects_entity_declarations() -> None:
    with pytest.raises(GrobidError, match="Entity"):
        GrobidTeiParser().parse(
            b'<!DOCTYPE TEI [<!ENTITY unsafe "x">]><TEI><text>&unsafe;</text></TEI>'
        )

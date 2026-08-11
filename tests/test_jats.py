from __future__ import annotations

import pytest

from genesis_evidence.literature.jats import JatsParseError, JatsParser, classify_license
from genesis_evidence.literature.models import RightsStatus

JATS_CC_BY = b"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front>
    <article-meta>
      <title-group><article-title>Nutrition and ageing</article-title></title-group>
      <abstract><p>This is the abstract.</p></abstract>
      <permissions>
        <license xlink:href="https://creativecommons.org/licenses/by/4.0/">
          <license-p>This is an open access article under the CC BY 4.0 license.</license-p>
        </license>
      </permissions>
    </article-meta>
  </front>
  <body>
    <sec><title>Introduction</title><p>Introductory text.</p></sec>
    <sec><title>Methods</title><p>Methods text.</p></sec>
  </body>
</article>
"""


def test_jats_parser_extracts_sections_and_commercially_usable_license() -> None:
    document = JatsParser().parse(JATS_CC_BY)

    assert document.title == "Nutrition and ageing"
    assert document.abstract == "This is the abstract."
    assert [section.title for section in document.sections] == [
        "Introduction",
        "Methods",
    ]
    assert document.license.code == "CC-BY"
    assert document.license.rights_status == RightsStatus.REDISTRIBUTABLE
    assert document.to_dict()["license"]["rights_status"] == "redistributable"


def test_noncommercial_license_is_not_approved_for_commercial_persistence() -> None:
    license_info = classify_license(
        "This is licensed CC BY-NC-ND 4.0",
        "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    )

    assert license_info.commercial_use_allowed is False
    assert license_info.rights_status == RightsStatus.METADATA_ONLY


def test_jats_parser_allows_external_doctype_but_rejects_entities() -> None:
    document = JatsParser().parse(
        b'<!DOCTYPE article PUBLIC "-//NLM//DTD JATS//EN" "JATS.dtd"><article />'
    )
    assert document.title == ""

    with pytest.raises(JatsParseError, match="Entity"):
        JatsParser().parse(b'<!DOCTYPE article [<!ENTITY xxe "unsafe">]><article>&xxe;</article>')

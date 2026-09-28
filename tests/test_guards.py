from pathlib import Path

import pytest

from scripts import check_patient_copy, check_scope


@pytest.mark.parametrize(
    ("guard", "content"),
    [
        (check_scope, "health_organization"),
        (check_patient_copy, "疾病诊断结果"),
    ],
)
def test_scope_and_patient_copy_guards_reject_forbidden_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard, content: str
) -> None:
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "page.py").write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit):
        guard.main()


def test_patient_copy_guard_scans_portal_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "page.py").write_text("COPY = '健康产品可以治愈风险'", encoding="utf-8")
    monkeypatch.setattr(check_patient_copy, "ROOT", tmp_path)

    with pytest.raises(SystemExit, match="page.py: 治愈"):
        check_patient_copy.main()

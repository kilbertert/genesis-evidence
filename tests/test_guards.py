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

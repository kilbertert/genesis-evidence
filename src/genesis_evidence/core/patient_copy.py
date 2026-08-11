"""Patient-visible wording guard shared by publication and CI."""

FORBIDDEN_PATIENT_TERMS = ("诊断", "确诊", "处方", "治愈", "根治", "排毒", "抗癌", "逆龄")


def validate_patient_copy(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("patient-visible content is required")
    matched = next((term for term in FORBIDDEN_PATIENT_TERMS if term in normalized), None)
    if matched:
        raise ValueError(f"patient-visible content contains forbidden term: {matched}")
    return normalized

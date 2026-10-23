"""Conservative supported constraints. Unknown never counts as satisfied."""
import re
from dataclasses import asdict, dataclass


@dataclass
class QueryConstraints:
    principal_required: bool = False
    minimum_risk_grade: int | None = None  # source 1=highest, 6=lowest
    product_type: str | None = None
    early_redemption_required: bool = False
    no_maturity: bool = False
    maximum_maturity_months: int | None = None
    ambiguous: bool = False

    def to_dict(self):
        return asdict(self)


def parse_constraints(question):
    compact = re.sub(r"\s+", "", question)
    value = QueryConstraints()
    warnings = []
    waived = re.search(r"원금(?:보장|손실방지)(?:은|이|을|는)?(?:필요없|불필요|안해도|상관없)", compact)
    if not waived and (re.search(r"원금보장(?:이|은|을)?(?:필요|필수|원해|되는|상품|해줘)", compact) or
                       re.search(r"원금(?:손실|손해)(?:은|을|이)?(?:피|없|안|싫)", compact)):
        value.principal_required = True
    if "원금비보장" in compact or "원금보장불필요" in compact:
        value.principal_required = False
    if re.search(r"(?:고위험|높은위험)(?:은|을)?(?:피|제외|싫|안)", compact):
        value.minimum_risk_grade = 4
    grade = re.search(r"위험등급([1-6])등급이상", compact)
    if grade:
        value.minimum_risk_grade = int(grade[1])
    if "안정" in compact or "보수적" in compact:
        warnings.append("안정적 성향만으로 위험등급을 확정하지 않습니다. 위험등급 조건을 별도로 선택하세요.")
    types = [kind for kind in ("주식형", "채권형", "재간접", "단기금융", "파생형", "퇴직연금") if kind in compact]
    if len(types) == 1 and not re.search(r"(?:말고|제외|아닌)", compact):
        value.product_type = types[0]
    elif types:
        value.ambiguous = True
        warnings.append("상품유형 조건이 여러 개이거나 부정형입니다. 유형 선택에서 명확히 지정하세요.")
    value.early_redemption_required = bool(re.search(r"중도환매(?:가|는|를)?(?:가능|필요|필수|원해)", compact))
    if "중도환매" in compact and re.search(r"(?:필요없|불필요|상관없)", compact):
        value.early_redemption_required = False
    value.no_maturity = bool(re.search(r"만기(?:가|는)?없(?:는|어야|어도)", compact)) and "없어도" not in compact
    term = re.search(r"(\d+)(년|개월)이(?:내|하)", compact)
    if term:
        value.maximum_maturity_months = int(term[1]) * (12 if term[2] == "년" else 1)
    if value.no_maturity and value.maximum_maturity_months:
        value.ambiguous = True
        warnings.append("만기 없음과 최대 만기 조건이 충돌합니다.")
    return value, warnings


def check_product(product, constraints):
    attrs = product["normalized_attributes"]
    value = lambda field: attrs.get(field, {}).get("normalized_value")
    issues = []
    if constraints.ambiguous:
        issues.append("조건 해석 확인 필요")
    if constraints.principal_required and value("principal_protected") is not True:
        issues.append("원금보장 미충족 또는 확인 불가")
    risk = value("product_risk_grade")
    if constraints.minimum_risk_grade is not None and (risk is None or risk["grade"] < constraints.minimum_risk_grade):
        issues.append("위험등급 조건 미충족 또는 확인 불가")
    if constraints.product_type and product["product_type"] != constraints.product_type:
        issues.append("상품유형 미충족")
    liquidity = value("liquidity") or {}
    if constraints.early_redemption_required and liquidity.get("early_redemption_allowed") is not True:
        issues.append("중도환매 가능 여부 미확인 또는 불가")
    maturity = value("maturity") or {}
    if constraints.no_maturity and maturity.get("has_maturity") is not False:
        issues.append("만기 없음 조건 미충족 또는 확인 불가")
    if constraints.maximum_maturity_months is not None:
        months = maturity.get("duration_months")
        if months is None or months > constraints.maximum_maturity_months:
            issues.append("최대 만기 조건 미충족 또는 확인 불가")
    return issues

NOTICE = "본 서비스는 제공 자료에 근거한 금융상품 정보 탐색 도구이며 전문적인 투자 자문을 대체하지 않습니다. 원금 손실이 발생할 수 있고 과거 성과는 미래 수익을 보장하지 않습니다. 가입 전 최신 공식 설명서와 본인의 적합성을 확인하세요."
NO_EVIDENCE = "현재 확보한 자료에서는 확인되지 않습니다."


def sensitive_question(question):
    import re
    # Avoid sending obvious contact/identity numbers through model requests.
    return bool(re.search(r"\b\d{6}[- ]?[1-4]\d{6}\b|\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", question))

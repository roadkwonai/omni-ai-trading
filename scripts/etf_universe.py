"""User-approved domestic ETF universe for the paper-only experiment."""

SYMBOLS = {
    'K200': '069500',
    'LEV': '122630',
    'INV': '114800',
    'KQ150': '229200',
    'KQ150LEV': '233740',
    'KQ150INV': '251340',
    'SEMILEV': '494310',
}

# Inverse ETFs; every other symbol is a long equity exposure.
SHORT = {'INV', 'KQ150INV'}

NAMES = {
    'K200': 'KODEX 200',
    'LEV': 'KODEX 레버리지',
    'INV': 'KODEX 인버스',
    'KQ150': 'KODEX 코스닥150',
    'KQ150LEV': 'KODEX 코스닥150레버리지',
    'KQ150INV': 'KODEX 코스닥150선물인버스',
    'SEMILEV': 'KODEX 반도체레버리지',
}

DETAILS = {
    'K200': 'KODEX 200: KOSPI 200 추종',
    'LEV': 'KODEX 레버리지: KOSPI 200 일간 변동의 약 2배',
    'INV': 'KODEX 인버스: KOSPI 200 선물 일간 변동의 약 -1배',
    'KQ150': 'KODEX 코스닥150: 코스닥 150 추종',
    'KQ150LEV': 'KODEX 코스닥150레버리지: 코스닥 150 일간 변동의 약 2배',
    'KQ150INV': 'KODEX 코스닥150선물인버스: 코스닥 150 선물 일간 변동의 약 -1배',
    'SEMILEV': 'KODEX 반도체레버리지: KRX 반도체 일간 변동의 약 2배',
}


# KRX short codes: six characters starting with a digit; listings since 2024 may carry a letter (e.g. 0153R0, 0162D0).
CODE_PATTERN = r'[0-9][0-9A-Z]{5}'


def is_code(value):
    import re
    return isinstance(value, str) and re.fullmatch(CODE_PATTERN, value) is not None

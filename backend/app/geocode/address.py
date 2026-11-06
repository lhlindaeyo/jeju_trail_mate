"""Korean (Jeju) address parsing shared by the local DB and the store matcher."""
from __future__ import annotations

import re

SIGUNGU_NAMES = ("제주시", "서귀포시")
_CITY_TOKENS = re.compile(r"(제주특별자치도|제주시|서귀포시)(?=\s|$)")
_ROAD_GLUED = re.compile(r"([가-힣]+로\d+번길|[가-힣0-9]+[로길])(\d+)(?:-(\d+))?")  # name+number in one token: 중앙로62번길5, 화순중앙로76
_ROAD_NAME = re.compile(r"([가-힣0-9]+로)(\d+번길)?|[가-힣0-9]+길")  # 중앙로, 516로, 신양로122번길, 문화길
_ALLEY = re.compile(r"\d+번길")
_NUMBER = re.compile(r"(\d+)(?:-(\d+))?")
_PLACE = re.compile(r"[가-힣0-9]*[가-힣](?:동|리|가)")


def parse_address(address: str) -> dict | None:
    """-> {"sigungu", "road": (name, main, sub)} or {"sigungu", "parcel": (emd/ri, san, bunji, ho)}.

    Works token by token, so 읍/면 prefixes ("성산읍 신양로122번길 30-8") are not glued onto the road name."""
    a = re.sub(r"\([^)]*\)", " ", address or "").replace(",", " ")
    sigungu = next((s for s in SIGUNGU_NAMES if re.search(rf"(?:^|\s){s}(?:\s|$)", a)), None)
    tokens = _CITY_TOKENS.sub(" ", a).split()

    for i, t in enumerate(tokens):
        if _ALLEY.fullmatch(t):
            continue  # "62번길" on its own is the second half of the previous token's name
        glued = _ROAD_GLUED.fullmatch(t)
        if glued:
            return {"sigungu": sigungu, "road": (glued.group(1), int(glued.group(2)), int(glued.group(3) or 0))}
        if not _ROAD_NAME.fullmatch(t):
            continue
        name, j = t, i + 1
        if j < len(tokens) and _ALLEY.fullmatch(tokens[j]) and not _ALLEY.search(name):
            name, j = name + tokens[j], j + 1  # "중앙로 62번길" -> 중앙로62번길
        if j < len(tokens) and (n := _NUMBER.fullmatch(tokens[j])):
            return {"sigungu": sigungu, "road": (name, int(n.group(1)), int(n.group(2) or 0))}

    for i, t in enumerate(tokens[:-1]):
        if _PLACE.fullmatch(t):
            n = re.fullmatch(r"(산)?(\d+)(?:-(\d+))?", tokens[i + 1])
            if n:
                return {"sigungu": sigungu, "parcel": (t, bool(n.group(1)), int(n.group(2)), int(n.group(3) or 0))}
    return None

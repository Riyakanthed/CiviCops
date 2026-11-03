"""Replaceable civic issue intelligence services used during report intake."""
from math import radians, sin, cos, sqrt, atan2

ROUTES = {
    "Road damage": "Public Works", "Pothole": "Public Works",
    "Public infrastructure": "Public Works", "Garbage & waste": "Sanitation",
    "Sanitation": "Sanitation", "Streetlight": "Electrical Services",
    "Water leakage": "Water Management", "Drainage": "Drainage",
    "Other": "Municipal Services",
}
KEYWORDS = {
    "Road damage": ("pothole", "road", "sidewalk", "pavement", "crack"),
    "Garbage & waste": ("garbage", "trash", "waste", "bin", "rubbish"),
    "Streetlight": ("streetlight", "street light", "lamp", "dark", "lighting"),
    "Drainage": ("drain", "flood", "blocked", "water pooling"),
    "Water leakage": ("leak", "pipe", "water", "burst"),
    "Sanitation": ("sanitation", "sewage", "dirty", "cleanliness"),
}

def classify(category: str | None, description: str) -> tuple[str, float]:
    """Baseline keyword classifier; swap with an image/text model behind this API."""
    text = description.lower()
    if category and category != "Other":
        return category, 0.82
    for label, words in KEYWORDS.items():
        if any(word in text for word in words):
            return label, 0.68
    return category or "Other", 0.31

def assess_priority(category: str, description: str, related_count: int = 0) -> tuple[int, str]:
    text = description.lower()
    score = 25 + min(related_count * 6, 24)
    if category in {"Water leakage", "Road damage", "Drainage"}: score += 18
    if any(word in text for word in ("danger", "injury", "urgent", "school", "flood", "exposed")): score += 25
    if any(word in text for word in ("large", "deep", "multiple", "heavy")): score += 10
    score = min(score, 100)
    return score, "High" if score >= 70 else "Medium" if score >= 45 else "Low"

def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    dlat, dlon = radians(lat2-lat1), radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return radius * 2 * atan2(sqrt(a), sqrt(1-a))

def route(category: str, latitude: float, longitude: float) -> str:
    """Location-aware routing seam; ward polygons can replace this fallback."""
    return ROUTES.get(category, "Municipal Services")

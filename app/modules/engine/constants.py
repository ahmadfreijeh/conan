"""All engine constants in one place, grouped by step.

Values that change per machine or per run (device, cutoffs, model names) live in .env
and come from app/core/config.py::get_settings(), not here.
"""

import re

# --- Payload (Step 2) ---------------------------------------------------------------
SCHEMA_VERSION = "1"

# --- Loader (Step 1) ----------------------------------------------------------------
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
WORKING_MAX_SIDE = 1280  # longest side of the smaller copy the models see
MAX_FILE_BYTES = 30 * 1024 * 1024  # 30 MB

# --- Metadata (Step 3) --------------------------------------------------------------
# EXIF tag ids (see the EXIF spec / PIL.ExifTags)
ORIENTATION = 0x0112
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
MAKE, MODEL, SOFTWARE, DATETIME = 271, 272, 305, 306
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 36867, 36881
FLASH, FOCAL_LENGTH, LENS_MODEL = 37385, 37386, 42036
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4
GPS_ALT_REF, GPS_ALT, GPS_DIRECTION = 5, 6, 17

# File name pattern -> source. Order matters: first match wins.
SOURCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("whatsapp", re.compile(r"^(IMG-\d{8}-WA\d+|WhatsApp Image)", re.I)),
    ("screenshot", re.compile(r"^(screenshot|screen shot)", re.I)),
    ("facebook", re.compile(r"^FB_IMG_", re.I)),
    ("telegram", re.compile(r"^photo_\d{4}-\d{2}-\d{2}", re.I)),
    ("camera", re.compile(r"^(IMG|DSC|DSCN|PXL|MVIMG|DCIM)[_-]?\d", re.I)),
]
# Sources that remove EXIF or never had camera EXIF.
STRIPPING_SOURCES = {"whatsapp", "screenshot", "facebook", "telegram"}

# --- Detection (Step 5) -------------------------------------------------------------
DETECT_MODEL_ID = "IDEA-Research/grounding-dino-tiny"
IOU_MERGE = 0.6  # same-label boxes overlapping more than this are duplicates
DETECT_CROP_PADDING = 0.10  # grow each crop by 10% per side for context
MIN_CROP_SIDE = 24  # px on the full image; smaller boxes aren't worth a crop

# What we ask the model to look for, grouped by what each tells us about the location.
# People and faces are left out on purpose (privacy). Pick groups with DETECT_GROUPS in .env.
LABEL_GROUPS: dict[str, list[str]] = {
    # Names, prices, addresses: the most valuable group (OCR zooms into these).
    "text": [
        "sign", "shop sign", "neon sign", "street name sign", "logo", "label", "poster",
        "banner", "billboard", "menu", "menu board", "chalkboard", "price list", "price tag",
        "receipt", "screen", "plaque", "sticker", "graffiti", "house number", "newspaper",
        "magazine", "book", "packaging", "business card",
    ],
    # Cuisine, local dishes and brands.
    "food": [
        "plate", "dish", "bowl", "cup", "mug", "glass", "wine glass", "beer glass", "bottle",
        "can", "teapot", "coffee pot", "tray", "cutlery", "chopsticks", "bread", "pizza",
        "burger", "sushi", "salad", "cake", "dessert", "fruit", "drink", "ice cream",
    ],
    # Small table details: restaurant tier and local habits.
    "table": [
        "napkin", "tablecloth", "salt shaker", "candle", "vase", "ashtray", "hookah", "coaster",
        "sugar packet", "straw", "takeaway cup", "paper bag", "food box", "placemat",
        "bill folder", "water jug", "olive oil bottle", "sauce bottle",
    ],
    # Decor and furniture: venue style, matches against candidate photos.
    "interior": [
        "window", "door", "arch", "lamp", "chandelier", "ceiling fan", "chair", "sofa",
        "bench", "table", "counter", "bar counter", "shelf", "mirror", "painting", "wall art",
        "photo frame", "tiles", "mosaic", "carpet", "rug", "curtain", "plant", "flower pot",
        "clock",
    ],
    # Type of venue.
    "venue": [
        "open kitchen", "coffee machine", "display fridge", "cash register", "pastry display",
        "wine rack", "bar stool", "stage", "fireplace", "awning", "parasol", "umbrella",
        "outdoor seating", "pergola", "terrace", "buffet", "aquarium",
    ],
    # Landmarks and building types.
    "buildings": [
        "building", "hotel", "skyscraper", "tower", "apartment block", "house", "villa",
        "mosque", "minaret", "church", "cathedral", "bell tower", "temple", "synagogue",
        "pagoda", "dome", "castle", "fortress", "palace", "ruins", "city wall", "city gate",
    ],
    # Architectural style: old town vs new district, regional styles.
    "architecture": [
        "balcony", "column", "arcade", "facade", "window shutters", "roof", "red tile roof",
        "flat roof", "chimney", "staircase", "stone wall", "brick wall", "wooden facade",
        "glass facade", "archway", "courtyard", "wrought iron railing", "mashrabiya",
    ],
    # Famous or rare structures and public places.
    "landmarks": [
        "bridge", "fountain", "statue", "monument", "obelisk", "clock tower", "lighthouse",
        "ferris wheel", "stadium", "harbor", "pier", "marina", "promenade", "public square",
        "park", "cable car", "billboard tower",
    ],
    # Street furniture: classic country clues.
    "street": [
        "street sign", "road sign", "stop sign", "traffic light", "street lamp", "bus stop",
        "crosswalk", "sidewalk", "curb", "bollard", "utility pole", "power lines",
        "fire hydrant", "mailbox", "phone booth", "trash bin", "parking meter",
        "road marking", "manhole cover", "bike rack", "kiosk", "market stall", "guard rail",
    ],
    # Shops and services.
    "commerce": [
        "shop", "storefront", "supermarket", "pharmacy sign", "atm", "gas station",
        "fuel pump", "vending machine", "shopping bag", "shopping cart", "market",
        "newsstand", "car rental sign", "taxi sign",
    ],
    # Rooftop gear differs a lot by country and climate.
    "rooftops": [
        "satellite dish", "solar water heater", "solar panel", "water tank", "rooftop antenna",
        "air conditioner", "rooftop terrace", "clothesline",
    ],
    # Plate style, taxi colors, transport type.
    "vehicles": [
        "car", "taxi", "bus", "double-decker bus", "tram", "train", "metro", "truck", "van",
        "pickup truck", "motorcycle", "scooter", "tuk-tuk", "bicycle", "boat", "ship", "ferry",
        "airplane", "license plate", "police car", "ambulance",
    ],
    # Terrain and water.
    "nature": [
        "mountain", "hill", "cliff", "sea", "beach", "river", "lake", "waterfall", "desert",
        "sand dune", "forest", "field", "snow", "rock", "island", "canal",
    ],
    # Plants: climate zone.
    "plants": [
        "palm tree", "pine tree", "cypress tree", "olive tree", "cactus", "bamboo",
        "banana tree", "birch tree", "tree", "bush", "flowers", "grass", "vineyard",
        "bougainvillea",
    ],
    # Animals that hint at region.
    "animals": [
        "dog", "cat", "camel", "horse", "donkey", "cow", "sheep", "goat", "pigeon", "seagull",
        "parrot", "monkey",
    ],
    # National and religious symbols.
    "symbols": [
        "flag", "coat of arms", "emblem", "map", "portrait", "cross", "crescent",
        "religious symbol", "calligraphy",
    ],
}  # fmt: skip

# label -> group (first group wins if a word appears twice)
LABEL_TO_GROUP: dict[str, str] = {}
for _group, _labels in LABEL_GROUPS.items():
    for _label in _labels:
        LABEL_TO_GROUP.setdefault(_label, _group)

# --- OCR (Step 6) -------------------------------------------------------------------
IOU_SAME_LINE = 0.5  # boxes overlapping more than this are the same text line
# Detected objects worth a closer OCR pass (text is often small inside them):
# everything in the "text" label group, plus these from other groups.
TEXT_GROUP = "text"
EXTRA_TEXT_LABELS = {
    "license plate", "street sign", "pharmacy sign", "car rental sign", "taxi sign",
}  # fmt: skip
OCR_CROP_MIN_SIDE = 64  # upscale smaller crops so tiny text is readable
OCR_CROP_PADDING = 0.10
TEXT_CROP_PADDING = 0.25  # padding around saved text crops, as a share of the line height
TEXT_CROP_MIN_HEIGHT = 48  # enlarge tiny text lines so they're readable when you open them

ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
LATIN = re.compile(r"[A-Za-z]")

# --- Run info -----------------------------------------------------------------------
TRACKED_PACKAGES = ("torch", "transformers", "paddlepaddle", "paddleocr", "pillow")

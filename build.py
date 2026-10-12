"""Сборка XML-фида (формат Циан 2.0) для объявлений КП «Счастье».

Фид подходит для ЦИАН и Домклика (Домклик принимает фиды в формате Циан).

    python build.py              # собрать docs/feed.xml и docs/photos.html
    python build.py --photos     # + скачать фото в docs/photos/

docs/photos.html — страница со всеми фото фида и их номерами (порядок как в фиде,
№1 — главное). Нужна, чтобы владелец мог сказать «дом 100 м², фото 7».
"""
import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
BASE_URL = "https://dpoleschukbm-creator.github.io/kp-schastye-feed"

PHONE = "9038018808"
ADDRESS = "Тверская область, Калининский муниципальный округ, деревня Крупшево"
SETTLEMENT = "Счастье"
LAT, LNG = "56.819479", "36.170467"
MAX_PHOTOS = 50

# --- чистка описаний -------------------------------------------------------
# В исходных текстах часть букв заменена латинскими двойниками (и наоборот).
CYR2LAT = dict(zip("аеорсухАВЕКМНОРСТХУ", "aeopcyxABEKMHOPCTXY"))
LAT2CYR = {v: k for k, v in CYR2LAT.items()}
CYR = re.compile(r"[а-яёА-ЯЁ]")
LAT = re.compile(r"[a-zA-Z]")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def fix_word(w: str) -> str:
    if not CYR.search(w) and all(c in LAT2CYR for c in w):
        # слово целиком из латинских двойников («B», «c», «M11») — это кириллица
        return "".join(LAT2CYR[c] for c in w)
    if not (CYR.search(w) and LAT.search(w)):
        return w
    cyr_only = any(CYR.match(c) and c not in CYR2LAT for c in w)
    lat_only = any(LAT.match(c) and c not in LAT2CYR for c in w)
    if cyr_only or not lat_only:
        return "".join(LAT2CYR.get(c, c) for c in w)
    return "".join(CYR2LAT.get(c, c) for c in w)


def clean_description(text: str) -> str:
    text = EMOJI.sub("", text)
    text = re.sub(r"[A-Za-zА-Яа-яЁё]+", lambda m: fix_word(m.group()), text)
    # Циан удаляет «/», «\», «№» и запрещает «&»
    text = text.replace("с/у", "санузлом").replace("&", " и ").replace("№", "")
    text = re.sub(r"\s*[/\\]\s*", ", ", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# --- фото --------------------------------------------------------------------
def download_photos(avito_id: str, urls: list[str], removed: list[str]) -> None:
    folder = DOCS / "photos" / avito_id
    folder.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(urls[:MAX_PHOTOS], 1):
        dest = folder / f"{i:02d}.jpg"
        if dest.name in removed:  # фото заменено своим, с Авито не докачивать
            continue
        if dest.exists() and dest.stat().st_size > 0:
            continue
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    dest.write_bytes(r.read())
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise RuntimeError(f"{avito_id} фото {i}: {e}") from e
                time.sleep(2)


def photo_urls(avito_id: str, removed: list[str]) -> list[str]:
    folder = DOCS / "photos" / avito_id
    seen, urls = set(), []
    for p in sorted(folder.glob("*.jpg")):
        if p.name in removed:
            continue
        digest = hashlib.md5(p.read_bytes()).hexdigest()
        if digest in seen:  # Циан запрещает дубли фото в объявлении
            continue
        seen.add(digest)
        urls.append(f"{BASE_URL}/photos/{avito_id}/{p.name}")
    return urls


# --- XML -----------------------------------------------------------------------
def tag(name: str, value, indent: int = 4) -> str:
    return f"{' ' * indent}<{name}>{escape(str(value))}</{name}>"


def build_object(o: dict, desc: str, photos: list[str]) -> str:
    is_house = o["category"] == "house"
    x = ["  <object>",
         tag("Category", "houseSale" if is_house else "landSale"),
         tag("ExternalId", f"kps-{o['avitoId']}"),
         tag("Description", desc),
         tag("Address", ADDRESS),
         "    <Coordinates>", tag("Lat", LAT, 6), tag("Lng", LNG, 6), "    </Coordinates>",
         "    <Phones>", "      <PhoneSchema>",
         tag("CountryCode", "+7", 8), tag("Number", PHONE, 8),
         "      </PhoneSchema>", "    </Phones>",
         tag("SettlementName", SETTLEMENT)]
    if is_house:
        x.append(tag("TotalArea", o["houseArea"]))
    x += [tag("HasElectricity", "true"), tag("HasWater", "true"),
          tag("HasGas", "true"), tag("HasDrainage", "true")]
    if is_house:
        x += [tag("WcLocationType", "indoors"), tag("RepairType", o.get("repair", "no"))]
        if o.get("bathhouse"):
            x.append(tag("HasBathhouse", "true"))

    x.append("    <Photos>")
    for i, url in enumerate(photos):
        x += ["      <PhotoSchema>", tag("FullUrl", url, 8),
              tag("IsDefault", "true" if i == 0 else "false", 8), "      </PhotoSchema>"]
    x.append("    </Photos>")

    if is_house:
        x += ["    <Building>", tag("FloorsCount", o["floors"], 6),
              tag("BuildYear", o["buildYear"], 6), tag("HeatingType", "autonomousGas", 6),
              "    </Building>"]
    x += ["    <Land>", tag("Area", o["landSotka"], 6), tag("AreaUnitType", "sotka", 6),
          tag("PermittedLandUseType", "individualHousingConstruction", 6),
          tag("LandCategory", "settlements", 6), "    </Land>",
          "    <Gas>", tag("Type", "main", 6), "    </Gas>",
          "    <Drainage>", tag("Type", "septicTank", 6), "    </Drainage>"]
    if is_house:
        x += ["    <Water>", tag("SuburbanWaterType", o["water"], 6), "    </Water>",
              "    <House>", "      <MaterialTypes>", tag("MaterialType", o["walls"], 8),
              "      </MaterialTypes>"]
        if o.get("terrace"):
            x.append(tag("HasTerrace", "true", 6))
        x += [tag("Condition", o.get("condition", "interiorDecorationRequired"), 6), "    </House>"]
    x += ["    <BargainTerms>", tag("Price", o["price"], 6), tag("Currency", "rur", 6),
          tag("MortgageAllowed", "true", 6), "    </BargainTerms>",
          "  </object>"]
    return "\n".join(x)


# --- страница с номерами фото --------------------------------------------------
def fmt_num(v) -> str:
    """93.6 -> «93,6», 100 -> «100»."""
    return (f"{v:g}" if isinstance(v, float) else str(v)).replace(".", ",")


def land_text(sotka) -> str:
    return f"{fmt_num(sotka / 100)} га" if sotka >= 100 else f"{fmt_num(sotka)} сот."


def short_name(o: dict) -> str:
    if o["category"] == "house":
        return f"Дом {fmt_num(o['houseArea'])} м²"
    return f"Участок {land_text(o['landSotka'])}"


PAGE_CSS = """
:root {
  --bg: #f3f6f2; --surface: #ffffff; --fg: #1b2620; --muted: #5d6b62; --line: #d9e1db;
  --accent: #23705f; --accent-ink: #ffffff; --badge: rgba(20, 28, 23, 0.78); --badge-ink: #ffffff;
  --font-display: "Unbounded", "Golos Text", system-ui, sans-serif;
  --font-body: "Golos Text", system-ui, -apple-system, "Segoe UI", sans-serif;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #121814; --surface: #1a221d; --fg: #e4ebe6; --muted: #9aa89f; --line: #2b362f;
    --accent: #5cbfa7; --accent-ink: #0d1a15; --badge: rgba(8, 12, 10, 0.8);
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font-family: var(--font-body); font-size: 15px; line-height: 1.5; }
.wrap { max-width: 1180px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 64px; }
header.top { display: grid; gap: 10px; max-width: 68ch; }
h1 { font-family: var(--font-display); font-weight: 700; font-size: clamp(22px, 4vw, 30px);
  line-height: 1.2; margin: 0; text-wrap: balance; }
.lead { margin: 0; color: var(--muted); }
.lead b { color: var(--fg); font-weight: 600; }
.lead a { color: var(--accent); }
nav.chips { position: sticky; top: 0; z-index: 5; background: var(--bg); margin-top: 20px;
  padding-block: 10px; border-bottom: 1px solid var(--line); }
.chips-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; }
.chip { flex: 0 0 auto; text-decoration: none; color: var(--fg); border: 1px solid var(--line);
  background: var(--surface); padding: 6px 12px; border-radius: 999px; font-size: 13px;
  font-weight: 500; white-space: nowrap; }
.chip:hover, .chip:focus-visible { border-color: var(--accent); color: var(--accent); outline: none; }
section.obj { margin-top: 36px; scroll-margin-top: 70px; }
.obj-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px; margin-bottom: 14px; }
.obj-head h2 { font-family: var(--font-display); font-weight: 500; font-size: 19px; margin: 0; }
.price { font-weight: 600; font-variant-numeric: tabular-nums; }
.meta { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
a.tile { position: relative; display: block; aspect-ratio: 4 / 3; max-width: 100%;
  border-radius: 6px; overflow: hidden; background: var(--line); outline-offset: 2px; }
a.tile img { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .2s ease; }
a.tile:hover img { transform: scale(1.04); }
a.tile:focus-visible { outline: 3px solid var(--accent); }
.num { position: absolute; top: 6px; left: 6px; min-width: 34px; height: 34px; padding: 0 8px;
  border-radius: 17px; display: grid; place-items: center; background: var(--badge);
  color: var(--badge-ink); font-family: var(--font-display); font-weight: 700; font-size: 16px;
  font-variant-numeric: tabular-nums; }
a.tile.main .num { background: var(--accent); color: var(--accent-ink); }
.main-tag { position: absolute; left: 6px; bottom: 6px; padding: 2px 8px; border-radius: 4px;
  background: var(--accent); color: var(--accent-ink); font-size: 12px; font-weight: 600; }
footer { margin-top: 48px; color: var(--muted); font-size: 13px; max-width: 68ch; }
@media (prefers-reduced-motion: reduce) {
  a.tile img { transition: none; } a.tile:hover img { transform: none; }
}
"""


def build_photos_page(items: list[tuple[dict, list[str]]]) -> str:
    """HTML-страница: все фото каждого объекта с номерами в порядке фида."""
    e = html.escape
    chips, sections = [], []
    for o, photos in items:
        aid, name = o["avitoId"], short_name(o)
        title = f"{name}, участок {land_text(o['landSotka'])}" if o["category"] == "house" else name
        price = f"{o['price']:,}".replace(",", " ") + " ₽"
        chips.append(f'<a class="chip" href="#o{aid}">{e(name)}</a>')
        tiles = []
        for i, url in enumerate(photos, 1):
            rel = url.removeprefix(BASE_URL + "/")
            main = i == 1
            label = f"{name}, фото {i}" + (", главное" if main else "")
            tiles.append(
                f'<a class="tile{" main" if main else ""}" href="{e(rel)}" target="_blank" '
                f'rel="noopener" aria-label="{e(label)}">'
                f'<img src="{e(rel)}" alt="" loading="lazy" decoding="async">'
                f'<span class="num">{i}</span>'
                + ('<span class="main-tag">главное</span>' if main else "")
                + "</a>")
        sections.append(
            f'<section class="obj" id="o{aid}">\n<div class="obj-head">'
            f"<h2>{e(title)}</h2><span class=\"price\">{price}</span>"
            f'<span class="meta">{len(photos)} фото · Авито {aid}</span></div>\n'
            f'<div class="grid">\n' + "\n".join(tiles) + "\n</div>\n</section>")
    return "\n".join([
        "<!doctype html>",
        '<html lang="ru">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex, nofollow">',
        "<title>Фото в фиде КП «Счастье»</title>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600'
        '&amp;family=Unbounded:wght@500;700&amp;display=swap">',
        f"<style>{PAGE_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        '<header class="top">',
        "<h1>Фото в фиде КП «Счастье»</h1>",
        '<p class="lead">Номер на фото — его место в объявлении на Домклике и ЦИАН. '
        "<b>Фото №1 — главное</b>, его видно в поиске. Нажмите на фото, чтобы открыть его целиком.</p>",
        '<p class="lead">Чтобы заменить фото, напишите, например: «Дом 100 м², фото 7». '
        'Сам фид: <a href="feed.xml">feed.xml</a>.</p>',
        "</header>",
        '<nav class="chips" aria-label="Объекты"><div class="chips-row">'
        + "".join(chips) + "</div></nav>",
        "<main>",
        *sections,
        "</main>",
        "<footer>Страница собирается вместе с фидом (build.py), порядок фото тот же, что в feed.xml. "
        "Повторяющиеся снимки в фид не попадают, поэтому их здесь тоже нет.</footer>",
        "</div>",
        "</body>",
        "</html>",
        "",
    ])


def write_lf(path: Path, text: str) -> None:
    """Всегда LF, на любом ПК и при любых настройках git (Path.write_text на Windows пишет CRLF)."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> None:
    objects = json.loads((ROOT / "data" / "objects.json").read_text(encoding="utf-8"))
    media = json.loads((ROOT / "data" / "avito_media.json").read_text(encoding="utf-8"))

    if "--photos" in sys.argv:
        for o in objects:
            download_photos(o["avitoId"], media[o["avitoId"]]["imgs"], o.get("removedPhotos", []))
            print(f"фото {o['avitoId']}: ok")

    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<feed>", "  <feed_version>2</feed_version>"]
    page_items = []
    for o in objects:
        desc = clean_description(media[o["avitoId"]]["desc"])
        for old, new in o.get("descReplace", []):
            if old not in desc:
                raise RuntimeError(f"{o['avitoId']}: в описании нет фрагмента «{old}»")
            desc = desc.replace(old, new)
        desc = clean_description(desc)  # «стало» из descReplace чистится так же, как исходный текст
        if not 15 <= len(desc) <= 7000:
            raise RuntimeError(f"{o['avitoId']}: длина описания {len(desc)} вне 15–7000")
        photos = photo_urls(o["avitoId"], o.get("removedPhotos", []))
        if not photos:
            raise RuntimeError(f"{o['avitoId']}: нет фото, запустите с --photos")
        parts.append(build_object(o, desc, photos))
        page_items.append((o, photos))
    parts.append("</feed>")

    DOCS.mkdir(exist_ok=True)
    write_lf(DOCS / "feed.xml", "\n".join(parts) + "\n")
    write_lf(DOCS / "photos.html", build_photos_page(page_items))
    write_lf(DOCS / ".nojekyll", "")
    print(f"docs/feed.xml: {len(objects)} объектов")
    print(f"docs/photos.html: {sum(len(p) for _, p in page_items)} фото")


if __name__ == "__main__":
    main()

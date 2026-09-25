"""Сборка XML-фида (формат Циан 2.0) для объявлений КП «Счастье».

Фид подходит для ЦИАН и Домклика (Домклик принимает фиды в формате Циан).

    python build.py              # собрать docs/feed.xml
    python build.py --photos     # + скачать фото в docs/photos/
"""
import hashlib
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
    text = re.sub(r"\s*/\s*", ", ", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


# --- фото --------------------------------------------------------------------
def download_photos(avito_id: str, urls: list[str]) -> None:
    folder = DOCS / "photos" / avito_id
    folder.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(urls[:MAX_PHOTOS], 1):
        dest = folder / f"{i:02d}.jpg"
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


def photo_urls(avito_id: str) -> list[str]:
    folder = DOCS / "photos" / avito_id
    seen, urls = set(), []
    for p in sorted(folder.glob("*.jpg")):
        digest = hashlib.md5(p.read_bytes()).hexdigest()
        if digest in seen:  # Циан запрещает дубли фото в объявлении
            continue
        seen.add(digest)
        urls.append(f"{BASE_URL}/photos/{avito_id}/{p.name}")
    return urls


# --- XML -----------------------------------------------------------------------
def tag(name: str, value, indent: int = 4) -> str:
    return f"{' ' * indent}<{name}>{escape(str(value))}</{name}>"


def build_object(o: dict, desc: str) -> str:
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
        x += [tag("WcLocationType", "indoors"), tag("RepairType", "no")]
        if o.get("bathhouse"):
            x.append(tag("HasBathhouse", "true"))

    photos = photo_urls(o["avitoId"])
    if not photos:
        raise RuntimeError(f"{o['avitoId']}: нет фото, запустите с --photos")
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
        x += [tag("Condition", "interiorDecorationRequired", 6), "    </House>"]
    x += ["    <BargainTerms>", tag("Price", o["price"], 6), tag("Currency", "rur", 6),
          tag("MortgageAllowed", "true", 6), "    </BargainTerms>",
          "  </object>"]
    return "\n".join(x)


def main() -> None:
    objects = json.loads((ROOT / "data" / "objects.json").read_text(encoding="utf-8"))
    media = json.loads((ROOT / "data" / "avito_media.json").read_text(encoding="utf-8"))

    if "--photos" in sys.argv:
        for o in objects:
            download_photos(o["avitoId"], media[o["avitoId"]]["imgs"])
            print(f"фото {o['avitoId']}: ok")

    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<feed>", "  <feed_version>2</feed_version>"]
    for o in objects:
        desc = clean_description(media[o["avitoId"]]["desc"])
        for old, new in o.get("descReplace", []):
            if old not in desc:
                raise RuntimeError(f"{o['avitoId']}: в описании нет фрагмента «{old}»")
            desc = desc.replace(old, new)
        if not 15 <= len(desc) <= 7000:
            raise RuntimeError(f"{o['avitoId']}: длина описания {len(desc)} вне 15–7000")
        parts.append(build_object(o, desc))
    parts.append("</feed>")

    DOCS.mkdir(exist_ok=True)
    (DOCS / "feed.xml").write_text("\n".join(parts) + "\n", encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"docs/feed.xml: {len(objects)} объектов")


if __name__ == "__main__":
    main()

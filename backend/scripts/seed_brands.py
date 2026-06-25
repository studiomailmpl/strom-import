"""
Seed script: Creates all brands found via Gmail image bank search
and populates their image bank URLs + notes.

Run from backend/:
  source venv/bin/activate
  python scripts/seed_brands.py
"""
import asyncio
import re
import uuid
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from app.core.database import async_session
from app.models.brand import Brand


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = slug.replace("°", "").replace("'", "").replace("\u2019", "")
    slug = slug.replace("\u00f8", "o").replace("\u00e5", "a").replace("\u00e6", "ae")
    slug = slug.replace("\u00fc", "u").replace("\u00e9", "e").replace("\u00ea", "e")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ── All brands from Gmail deep dive ────────────────────────────────

BRANDS = [
    # === ACTIVE IMAGE BANKS ===
    {
        "name": "66\u00b0 North",
        "website_url": "https://www.66north.com",
        "image_bank_url": "https://66north.room.datadwell.app/MgbfH5EEeu",
        "image_bank_type": "datadwell",
        "image_bank_notes": "Kontakt: Michelle Nielsen (michelle@66north.com). Korrekt link sendt 23/4-2026.",
    },
    {
        "name": "M\u00f8rch Fashion",
        "website_url": "https://www.morchfashion.com",
        "image_bank_url": "https://trendmark.image-bank.com/",
        "image_bank_type": "trendmark",
        "image_bank_notes": "Kontakt: Katharina Jensen (katharina@morchfashion.com, +45 31225056). COO.",
    },
    {
        "name": "Birkenstock",
        "website_url": "https://www.birkenstock.com",
        "image_bank_url": "https://birkenstock.canto.global/v/Europe/",
        "image_bank_type": "canto",
        "image_bank_notes": "Kundenummer: 0010116751. Klik 'Access Request'. Kontakt: Mathias Walentin Norr (Mathias.Norr@birkenstock.com, +45 31132344).",
    },
    {
        "name": "A.P.C.",
        "website_url": "https://www.apc.fr",
        "image_bank_url": "https://drive.google.com/drive/folders/1QWf2LGX7ehmMMO0YdEwsmxfI9tUOR8VE",
        "image_bank_type": "custom",
        "image_bank_notes": "Google Drive via NOW Agency. Anmod om adgang \u2192 godkendt inden 24t. Kontakt: Olivia Skaarup (olivia@nownowagency.com, +45 28309133).",
    },
    {
        "name": "Ragbag Studio",
        "website_url": "https://ragbagstudio.com",
        "image_bank_url": "https://app.traede.com/",
        "image_bank_type": "custom",
        "image_bank_notes": "Traede platform. Login: vp@stromstore.dk / Strandvejen169A! Kontakt: Anne Katrine Hviid Klevin (Ak@ragbagstudio.com, +45 53626636).",
    },
    {
        "name": "Sunflower",
        "website_url": None,
        "image_bank_url": "https://app.traede.com/",
        "image_bank_type": "custom",
        "image_bank_notes": "Traede platform (samme som Ragbag). Login: vp@stromstore.dk / Strandvejen169A!",
    },

    # === BEGRÆNSET / SPECIAL CASE ===
    {
        "name": "Comme des Gar\u00e7ons",
        "website_url": "https://www.comme-des-garcons.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Ingen packshots/kampagnebilleder. Kun Play-logo + brand deck (sendt via WeTransfer 24/4). Heart-emblemet m\u00e5 IKKE bruges som logo. Kontakt: Jiyoon Han (jiyoon.han@comme-des-garcons.com, +33 1 47 03 61 05).",
    },
    {
        "name": "Birrot",
        "website_url": "https://birrot.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Ingen officiel image bank. Hjemmesidebilleder fra birrot.com. Agentur The Market Agency unders\u00f8ger. Kontakt: Zandra Serler (zandra@themarket.dk, +45 53650028).",
    },

    # === VIDERESENDT INTERNT (afventer) ===
    {
        "name": "Dries Van Noten",
        "website_url": "https://www.driesvannoten.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Puig-koncernen. Harry Mundy videresendte til marketing-team 24/4. Kontakt: harry.mundy@puig.com.",
    },
    {
        "name": "Marni",
        "website_url": "https://www.marni.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Giulia Ciarliero CC\u2019ede Pia Ferrelli (Pia_Ferrelli@marni.com) \u2014 hj\u00e6lper n\u00e5r hun er tilbage. Kontakt: Giulia_Ciarliero@marni.com.",
    },

    # === INGEN SVAR ENDNU ===
    {
        "name": "Acne Studios",
        "website_url": "https://www.acnestudios.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Forespurgt 23/4. Ingen svar endnu. Kontakt: nina.bergsten@acnestudios.com.",
    },
    {
        "name": "Alis",
        "website_url": "https://www.alis.dk",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Forespurgt 23/4. Ingen svar endnu. Kontakt: sa@alis.dk (Sebastian).",
    },
    {
        "name": "Berner K\u00fchl",
        "website_url": "https://www.bernerkuhl.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Forespurgt 23/4. Ingen svar endnu. Kontakt: frederik@bernerkuhl.com.",
    },
    {
        "name": "Salomon",
        "website_url": "https://www.salomon.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Forespurgt via T-3 agentur 23/4. Ingen svar endnu. Kontakt: sf@t-3.dk.",
    },
    {
        "name": "New Balance",
        "website_url": "https://www.newbalance.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Forespurgt 23/4. Ingen svar endnu. Kontakt: Martin.Toft@newbalance.com.",
    },
    {
        "name": "Maison Margiela",
        "website_url": "https://www.maisonmargiela.com",
        "image_bank_url": None,
        "image_bank_type": None,
        "image_bank_notes": "Forespurgt 23/4. Ingen svar endnu. Kontakt: Irshana_Goulamabasse@margiela.com.",
    },
]


async def main():
    async with async_session() as session:
        # Find the organisation
        result = await session.execute(text("SELECT id, name FROM organisations LIMIT 1"))
        org_row = result.first()
        if not org_row:
            print("\u274c Ingen organisation fundet i databasen. K\u00f8r appen og opret en f\u00f8rst.")
            return

        org_id = org_row[0]
        org_name = org_row[1]
        print(f"\U0001f3e2 Organisation: {org_name} ({org_id})")

        # Get existing brand slugs
        result = await session.execute(
            select(Brand.slug, Brand.name).where(Brand.organisation_id == org_id)
        )
        existing = {row[0]: row[1] for row in result.all()}
        print(f"\U0001f4cb Eksisterende brands: {len(existing)}")

        created = 0
        updated = 0
        skipped = 0

        for brand_data in BRANDS:
            slug = _slugify(brand_data["name"])

            if slug in existing:
                # Update existing brand with image bank info if we have new data
                result = await session.execute(
                    select(Brand).where(
                        Brand.organisation_id == org_id,
                        Brand.slug == slug,
                    )
                )
                brand = result.scalar_one_or_none()
                if brand:
                    changed = False
                    if brand_data["image_bank_url"] and not brand.image_bank_url:
                        brand.image_bank_url = brand_data["image_bank_url"]
                        changed = True
                    if brand_data["image_bank_type"] and not brand.image_bank_type:
                        brand.image_bank_type = brand_data["image_bank_type"]
                        changed = True
                    if brand_data["image_bank_notes"] and not brand.image_bank_notes:
                        brand.image_bank_notes = brand_data["image_bank_notes"]
                        changed = True
                    if brand_data["website_url"] and not brand.website_url:
                        brand.website_url = brand_data["website_url"]
                        changed = True

                    if changed:
                        updated += 1
                        print(f"  \U0001f504 Opdateret: {brand_data['name']} (image bank info tilf\u00f8jet)")
                    else:
                        skipped += 1
                        print(f"  \u23ed\ufe0f  Sprunget over: {brand_data['name']} (allerede komplet)")
            else:
                # Create new brand
                brand = Brand(
                    id=uuid.uuid4(),
                    organisation_id=org_id,
                    name=brand_data["name"],
                    slug=slug,
                    image_bank_url=brand_data["image_bank_url"],
                    image_bank_type=brand_data["image_bank_type"],
                    image_bank_notes=brand_data["image_bank_notes"],
                    website_url=brand_data["website_url"],
                )
                session.add(brand)
                created += 1
                print(f"  \u2705 Oprettet: {brand_data['name']}" + (f" \u2014 {brand_data['image_bank_type']}" if brand_data['image_bank_type'] else ""))

        await session.commit()

        print(f"\n\U0001f389 F\u00e6rdig! Oprettet: {created}, Opdateret: {updated}, Sprunget over: {skipped}")
        print(f"   Total brands med image bank: {sum(1 for b in BRANDS if b['image_bank_url'])}")


if __name__ == "__main__":
    asyncio.run(main())

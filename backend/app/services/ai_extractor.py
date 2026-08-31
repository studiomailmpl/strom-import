"""
Claude Vision AI extraction — extracts product data from invoice images.
Ported from app.py — extract_products_with_ai, _normalize_color_name,
_normalize_vendor, _clean_description, _get_fallback_description.
"""

import asyncio
import json
import logging
import re
import time

import anthropic

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Anthropic errors that a retry cannot fix. Retrying a spend cap or a bad key
# just burns the backoff and reports the same failure three attempts later.
_NON_RETRYABLE_MARKERS = (
    "usage limits",
    "credit balance",
    "invalid_api_key",
    "authentication_error",
    "permission_error",
)


def is_retryable_api_error(error: Exception) -> bool:
    """
    Whether re-issuing the same Claude request could plausibly succeed.

    Rate limits (429) and server errors are worth retrying. A spend cap, an
    invalid key or a malformed request will fail identically every time.
    """
    status = getattr(error, "status_code", None)
    if status in (429, 500, 502, 503, 504, 529):
        return True
    if status in (401, 403, 404, 413):
        return False

    text = str(error).lower()
    if any(marker in text for marker in _NON_RETRYABLE_MARKERS):
        return False
    if status == 400:
        # 400s are caller errors; the only retryable flavour is an overload
        # that some gateways report with this code.
        return "overloaded" in text
    return True


def describe_api_error(error: Exception) -> str:
    """A message worth showing a user, instead of the raw API JSON."""
    text = str(error)
    lowered = text.lower()

    if "usage limits" in lowered or "credit balance" in lowered:
        when = ""
        match = re.search(r"regain access on ([0-9]{4}-[0-9]{2}-[0-9]{2}) at ([0-9:]+ ?UTC)", text)
        if match:
            when = f" Adgang vender tilbage {match.group(1)} kl. {match.group(2)}."
        return (
            "Anthropic-kontoens forbrugsgrænse er nået, så fakturaen kunne ikke "
            f"analyseres.{when} Hæv grænsen under Limits i Anthropic Console, "
            "eller vent og kør importen igen."
        )
    if "invalid_api_key" in lowered or "authentication_error" in lowered:
        return "Anthropic API-nøglen blev afvist — tjek ANTHROPIC_API_KEY."
    if "rate_limit" in lowered or getattr(error, "status_code", None) == 429:
        return "Anthropic API er overbelastet lige nu. Prøv importen igen om lidt."
    return text[:300]


async def extract_products_with_ai(
    pdf_text: str,
    existing_tags: list[str],
    *,
    api_key: str,
    pdf_images: list[str] | None = None,
    table_products: list[dict] | None = None,
    active_descriptions: list[dict] | None = None,
    brand_extraction_examples: dict[str, list[dict]] | None = None,
    historical_keywords: dict[str, list[str]] | None = None,
    model: str = "claude-sonnet-4-6",
    eur_to_dkk: float = 7.46,
    markup: float = 2.5,
) -> list[dict]:
    """
    Vision-first extraction:
    - Claude Vision reads the PDF images directly -> extracts ALL data (SKU, sizes, qty, prices, material)
    - If deterministic table_products exist, they OVERRIDE AI's sizes/quantities
    - Description style comes from real active product examples
    - Works with ANY invoice format from ANY brand
    """
    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=120.0,  # 120s timeout for large invoice extraction calls
    )

    tag_list = ", ".join(existing_tags) if existing_tags else "(ingen eksisterende tags)"

    # Build reference descriptions from active products
    description_examples = ""
    if active_descriptions:
        from bs4 import BeautifulSoup as _BS
        good_examples: list[dict] = []
        for ap in active_descriptions:
            html = ap.get("description_html", "")
            if not html or len(html) < 50:
                continue
            text = _BS(html, "html.parser").get_text(separator=" ").strip()
            if len(text) > 40:
                good_examples.append({
                    "title": ap.get("title", ""),
                    "type": ap.get("product_type", ""),
                    "vendor": ap.get("vendor", ""),
                    "description": text[:400],
                })
            if len(good_examples) >= 6:
                break

        if good_examples:
            description_examples = "\n\nEKSEMPLER PÅ BESKRIVELSER FRA AKTIVE PRODUKTER I BUTIKKEN (match denne stil og længde):\n"
            for ex in good_examples:
                description_examples += f'  - "{ex["title"]}" ({ex["vendor"]}, {ex["type"]}): {ex["description"]}\n'
            description_examples += "\nSkriv beskrivelser der matcher dette niveau af detalje og denne tone.\n"

    # Build brand-specific few-shot extraction examples
    brand_few_shot_section = ""
    if brand_extraction_examples:
        parts = []
        for brand_name, examples in brand_extraction_examples.items():
            if not examples:
                continue
            parts.append(f"\nTIDLIGERE VELLYKKEDE EKSTRAKTIONER FOR '{brand_name.upper()}' (brug som reference):")
            for ex in examples[:3]:  # Max 3 per brand
                parts.append(
                    f'  - SKU: {ex.get("style_code", "?")} → '
                    f'title: "{ex.get("title", "")}", '
                    f'type: "{ex.get("product_type", "")}", '
                    f'color: "{ex.get("color", "")}", '
                    f'details: "{(ex.get("details", "") or "")[:200]}"'
                )
            parts.append("Brug SAMME format, stil og detaljeniveau for nye produkter fra dette brand.\n")
        brand_few_shot_section = "\n".join(parts)

    # Build historical keyword section (Layer 2 — Search Console feedback)
    historical_keyword_section = ""
    if historical_keywords:
        hk_parts = [
            "\n\nSØGEORD DER HAR PERFORMET GODT I GOOGLE (brug som inspiration til seo_keywords):"
        ]
        for ptype, keywords in historical_keywords.items():
            if keywords:
                kw_str = ", ".join(f'"{kw}"' for kw in keywords[:5])
                hk_parts.append(f"  - {ptype}: {kw_str}")
        hk_parts.append(
            "Disse søgeord har fået rigtige klik fra Google. "
            "Brug lignende formuleringer for produkter i samme kategori, "
            "men TILPAS altid til det specifikke produkts egenskaber.\n"
        )
        historical_keyword_section = "\n".join(hk_parts)

    # Build pre-parsed product summary (if deterministic parser found data)
    table_summary = ""
    if table_products:
        table_summary = "\n\nDETERMINISTISK UDTRUKKET DATA (brug disse størrelser/antal/priser hvis de er tilgængelige):\n"
        for tp in table_products:
            sizes_str = ", ".join([f"{v['size']}({v['quantity']})" for v in tp['variants']]) if tp['variants'] else f"Total: {tp['total_qty']} (ingen størrelses-breakdown)"
            currency_note = f" ({tp.get('currency_detected', 'EUR')}→EUR)" if tp.get('currency_detected') == 'DKK' else ""
            extra = ""
            if tp.get("material_raw"):
                extra += f" | Materiale: {tp['material_raw']}"
            if tp.get("origin"):
                extra += f" | Oprindelse: {tp['origin']}"
            if tp.get("hs_code"):
                extra += f" | HS: {tp['hs_code']}"
            table_summary += f"  - {tp['style_code']} | {tp['designation']} | Farve: {tp['color_original']} | €{tp['cost_price_eur']:.2f}{currency_note} | {sizes_str}{extra}\n"
        table_summary += "\nKRITISK: Brug PRÆCIS disse størrelser, antal og priser. ÆNDR DEM IKKE.\n"

    system_prompt = f"""Du er en Shopify-produktekspert for STRØM (stromstore.dk), en premium skandinavisk modebutik.

DIN OPGAVE: Udtræk ALLE produkter fra denne leverandørfaktura og returnér struktureret JSON.

SE PÅ BILLEDERNE AF FAKTURAEN — de viser det præcise layout med tabeller, størrelser og priser.
Brug teksten som supplement til at bekræfte data.

FOR HVERT PRODUKT SKAL DU UDTRÆKKE:
1. style_code — artikelnummer/SKU fra fakturaen (PRÆCIS som det står)
2. title — produktnavn + original farvenavn i Title Case
3. vendor — brand/leverandør
4. variants — PRÆCISE størrelser og antal fra fakturaens tabel/størrelsesgrid
5. cost_price_eur — NETTO enhedspris i EUR EFTER eventuel rabat (se RABAT-regler nedenfor)
6. discount_pct — rabatprocent hvis angivet (0 hvis ingen rabat)
7. color — oversat til simpelt dansk/engelsk farvenavn
7. color_original — PRÆCIS farvenavn fra fakturaen
8. material — på dansk (cotton→bomuld, wool→uld osv.)
9. country_of_origin — ISO landekode
10. hs_code — toldkode hvis angivet

VALUTA: Tjek om fakturaen er i EUR eller DKK.
Hvis DKK → divider enhedsprisen med {eur_to_dkk} for at få EUR.
Tegn: "Total DKK" = DKK, "Price(EUR)" eller "drawn in: Euro" = EUR.

RABATTER OG NETTOPRIS — KRITISK:
Mange fakturaer har rabatter. Du SKAL finde og fratrække dem korrekt.
1. Led efter rabat-kolonner: "Discount", "Disc%", "Remise", "Rabat", "%", "Red."
2. Led efter nettopris-kolonner: "Net Price", "Net", "Netto", "After Discount"
3. Led efter samlet rabat i bunden: "Total Discount", "Rabat i alt", "Discount applied"
4. Led efter rabattekst i header/footer: "Season discount 20%", "10% on all items"
5. Tjek om linjetal stemmer: enhedspris × antal = linjetotal? Hvis ikke → der er rabat.

REGLER for cost_price_eur:
- cost_price_eur skal ALTID være den FAKTISKE nettopris EFTER rabat
- Hvis fakturaen viser BÅDE bruttopris og nettopris → brug NETTO
- Hvis fakturaen viser bruttopris + rabat% → beregn: brutto × (1 - rabat/100)
- Hvis der er en rabatkolonne med beløb → fratræk beløbet fra bruttoprisen
- Krydsvalidér: (nettopris × antal) bør matche linjetotalen — brug det som sanity check
- Sæt discount_pct til den fundne rabatprocent (0 hvis ingen rabat)

EKSEMPLER:
- Bruttopris 100€, Discount 20% → cost_price_eur: 80.0, discount_pct: 20
- Kolonnerne "Price: 50€" og "Net: 40€" → cost_price_eur: 40.0, discount_pct: 20
- "Season discount 15% applied" i footer → ganges på ALLE linjer: brutto × 0.85
- Ingen rabat nævnt → cost_price_eur: bruttoprisen, discount_pct: 0

FLERE FARVER = FLERE PRODUKTER — KRITISK:
- Hvis ét artikelnummer har FLERE farverækker (f.eks. "210 Blue" og "650 Burgundy" under samme item number),
  skal HVER farve blive et SELVSTÆNDIGT produkt med sit eget JSON-objekt.
- De deler style_code, men har forskellige: title, color, color_original, variants og evt. pris.
- Eksempel: "STRIPE LS POCKET TEE" (item 2064) med Blue OG Burgundy = 2 separate produkter.
- Tæl antal farve-blokke under hvert item number — det er antallet af produkter for det item.

STØRRELSER OG ANTAL — KRITISK:
- Læs PRÆCIST fra fakturaens størrelsesgrid/tabel
- Hver størrelse med sit antal: S(1), M(3), L(3), XL(1)
- "S/S" i et produktnavn betyder "Short Sleeve", IKKE en størrelse
- Hvis fakturaen kun har total antal uden størrelses-breakdown, angiv ALLE størrelser med qty 0 og marker total

TITEL-REGLER (KRITISK — LÆS GRUNDIGT):
- Format: "Produktnavn Farvenavn" i Title Case
- GODE EKSEMPLER: "New Base Shirt Blue", "Stable Shirt Off White", "Loose Dark Blue Vintage", "Wide Twist Dark Blue Vintage"
- DÅRLIGE EKSEMPLER (ALDRIG gør dette): "1318 210 Blue", "FN-MN-TSHI000689", "Shirt 001"
- "S/S" = Short Sleeve, "L/S" = Long Sleeve (del af produktnavnet, IKKE størrelse)
- ALDRIG brug artikelnummer/SKU/style_code som titel!
  SKU'er ser ud som: "FN-MN-TSHI000689", "AC-UX-SCAR00011", "RW-UX-TSHI000024", "1AC0BG", "1318", "5171" osv.
  Disse er KODER og skal KUN i "style_code"-feltet, ALDRIG i "title".
  OBS: Korte numre som "1318", "5171", "1325" er OGSÅ SKU'er/style codes — de er IKKE farver eller titler.
- ALDRIG inkludér numeriske farvekoder i titlen!
  Farvekoder som "900", "001", "0900", "1001", "210", "707", "720" er KODER og hører KUN i "color_original"-feltet.
  Brug det OVERSATTE farvenavn i titlen — f.eks. "Black" i stedet for "900", "Blue" i stedet for "210".
  Eksempel: Faktura siger "Jökla Jacket 900" → title: "Jökla Jacket Black", color_original: "900"
  Eksempel: Faktura siger "1318 NEW BASE SHIRT 210 Blue" → title: "New Base Shirt Blue", style_code: "1318", color_original: "210 Blue"
- Hvis fakturaen kun viser SKU uden produktnavn, skal du KONSTRUERE en meningsfuld titel:
  Kombiner: [Produkttype] [Farve] — f.eks. "T-Shirt White", "Scarf Cognac Brown"
  Eller brug: vendor + type + farve — f.eks. "Round Neck Tee Dusty White"
- Title SKAL altid være menneskeligt læsbart — aldrig en kode eller et nummer
- SELVTEST: Ville en kunde forstå produktnavnet? "New Base Shirt Blue" = JA. "1318 210" = NEJ.
- Titlen skal ALDRIG starte med et tal medmindre det er en del af produktnavnet (f.eks. "3-Pack Boxer")

FARVE ("color"-feltet): Oversæt til simpelt farvenavn:
  NOIR/BLACK → Sort, BLANC/WHITE → Hvid, BLEU → Blå, ROUGE → Rød
  GRIS → Grå, VERT → Grøn, MARRON → Brun, BEIGE → Beige
  DARK/DARK NAVY → Mørk, TIQ/TIQ DARK → Sort, 3ONXX → Sort

PRODUKTTYPE (engelsk): shirt/chemise → "Shirt", pantalon/trouser → "Trouser",
  t-shirt → "T-shirt", hoodie → "Hoodie", knit/pull → "Knit", jacket → "Jacket" osv.

KØN: Bestem fra fakturaen eller brand-kontekst. "Men", "Women" eller "Unisex".

SÆSON: "SS26", "FW26" etc. baseret på fakturadato eller reference.

PRODUKTBESKRIVELSE — DANSK ("details"):

TONE: Skandinavisk-minimalistisk. Skriv som en premium modebutik — præcist, underspillet, ingen superlaver.
Lad produktet tale gennem konkrete detaljer. Tænk COS, Arket, Totême — clean, faktuel, selvsikker.

KRITISK: "details" må ALDRIG nævne brand/vendor-navnet!
Appen tilføjer selv "[Titel] fra [Vendor]." foran, så du skal KUN skrive selve beskrivelsen.

ÅBNING — VARIER mellem disse mønstre (brug ALDRIG det samme for mere end 2 produkter i samme batch):
  A) "[Type] i [materiale] med [vigtigste kendetegn]."  — "Bucket bag i blødt ruskind med rummelig silhuet og justerbar skulderrem."
  B) "[Striktype/snit] i [materiale] med [fit]."  — "Finstrikket pullover i uld med dyb v-hals og regular fit."
  C) "[Materiale]-[type] med [primær detalje]."  — "Bomuldsskjorte med spidskrave og brystlomme."
  D) "[Type] med [silhuet/pasform] i [materiale]."  — "Jakke med oversized silhuet i vasket denim."

DEREFTER 2-3 sætninger med KONKRETE, SPECIFIKKE detaljer:
  - Lukning: knapper, lynlås, magnet, trykknapper, snøre, spænde
  - Konstruktion: antal lommer, foringtype, sømdetaljer, forstærkninger
  - Fit-detaljer: droppede skuldre, ribkant placering, slids, hem-form
  - Materiale-detaljer: stofvægt (let/mellemvægt/tung), finish (børstet, vasket, rå), striktype (fin, grov, ribstrik)

EKSEMPLER PÅ GOD EDITORIAL DANSK (stromstore.dk-niveau):
  - "Bucket bag i blødt ruskind med rummelig silhuet og justerbar skulderrem. Fire udvendige lommer med magnetlukning — to på forsiden, to på siderne. Indvendigt ét stort hovedrum og en mindre lomme med præget logo. Foret i bomuld."
  - "Finstrikket pullover i uld med dyb v-hals og regular fit. Ribstrik ved hals, manchetter og bundkant. Ren silhuet uden pynt."
  - "Bomuldsskjorte med spidskrave og knapper i front. Lange ærmer med manchetknapper og buet søm i bunden. Let, vasket kvalitet med blød finish."
  - "Oversized hoodie i børstet bomuldssweat. Kængurulomme foran og træksnor i hætten. Droppede skuldre og ribstrik ved ærmer og bund."
  - "Bukser i uld med mellemhøj talje og pressefolder. Rette ben med ren finish. Lynlås og hægte-lukning i livet. Sidelommer og én baglomme med knap."

PRODUKTBESKRIVELSE — ENGELSK ("details_en"):

"details_en" skal være en SELVSTÆNDIG, KOMPLET engelsk tekst — IKKE en oversættelse af den danske.
Skriv i SAMME editorial tone: præcis, clean, faktuel. Samme antal sætninger og detaljeniveau som den danske.

EKSEMPLER PÅ GOD EDITORIAL ENGELSK:
  - "Bucket bag in soft suede with a roomy silhouette and adjustable shoulder strap. Four exterior pockets with magnetic closures — two at the front, two at the sides. Cotton-lined interior with one main compartment and a smaller pocket with embossed logo."
  - "Fine-gauge wool pullover with a deep v-neck and regular fit. Ribbed trim at the neck, cuffs and hem. Clean silhouette, pared back to the essential."
  - "Cotton shirt with a pointed collar and button-through front. Long sleeves with button cuffs and a curved hem. Lightweight washed cotton with a soft hand feel."

REGLER FOR BEGGE BESKRIVELSER:
- ALDRIG nævn brand/vendor i "details" eller "details_en" — appen tilføjer det automatisk
- ALDRIG nævn størrelse, pris eller tilgængelighed
- ALDRIG brug: "fremgår ikke", "kan ikke udledes", "ikke oplyst", "ikke tilgængelig"
- ALDRIG brug: "perfekt til", "ideel til", "elegant", "raffineret", "tidløs", "æstetik", "udtryk"
- ALDRIG brug: "karakteristiske", "minimalistiske", "moderne udtryk", "klassisk stil"
- ALDRIG brug: "versatile", "effortless", "timeless", "sophisticated", "statement piece"
- KUN beskriv fysiske, konkrete egenskaber man kan se og røre
- Hvis materiale er ukendt: spring materialet over og start med snit/konstruktion
- Dansk: 2-4 sætninger. Engelsk: 2-4 sætninger (selvstændig tekst, IKKE en oversættelse).
- VARIER ÅBNINGEN — brug IKKE mønster A for alle produkter. Skift mellem A, B, C og D.
- Beskrivelsen SKAL indeholde MINDST 2 konkrete detaljer (lukning, lommer, krave, søm, fit osv.)
- Beskrivelsen må ALDRIG være en generisk one-liner som "Skjorte i bomuld." — der SKAL være detaljer.
- ALDRIG gentag titlen som første sætning — beskrivelsen skal TILFØJE information, ikke gentage.
- Hvis du mangler detaljer fra fakturaen: beskriv den typiske konstruktion for den produkttype
  (f.eks. en skjorte har typisk krave, knapper, manchetter — beskriv dem).
- ALDRIG start to på hinanden følgende produkters beskrivelser med det SAMME ord eller mønster.

SOLBRILLER/EYEWEAR:
- Brug ALTID "stel" (frame) — ALDRIG "stol" (chair). Et par solbriller har et STEL.
- Beskriv: stel-materiale, stel-form, glas-type, stænger. Varier mellem produkter.

SEO-SØGEORD ("seo_keywords"):
Angiv 2-3 søgeord som en dansk modekunde ville skrive i Google for at finde PRÆCIS dette produkt.
Søgeordene skal afspejle det SPECIFIKKE produkt — dets materiale, konstruktion og funktion.

METODE: Kig på hvad der gør produktet unikt:
- Hvad er materialet? → brug det i søgeordet ("ruskindstaske", "uldfrakke", "silkeskjorte")
- Hvad er den specifikke type? → brug det ("bucket bag", "v-hals pullover", "cargo bukser")
- Hvad er en konstruktionsdetalje? → brug det ("dobbeltknappet blazer", "polstret jakke", "flettet taske")

REGLER FOR seo_keywords:
- Skriv på DANSK — kunderne søger på dansk
- Søgeord 1: [DETTE brand] + [specifik produkttype] — f.eks. "acne studios ruskindstaske"
- Søgeord 2: [materiale/detalje] + [type] + [køn] — f.eks. "ruskind bucket bag dame"
- Søgeord 3: generisk + differentierende — f.eks. "designer taske med lommer"
- Byg søgeordene fra PRODUKTETS FAKTISKE EGENSKABER — materiale, lukning, silhuet, funktion
- ALDRIG brug: "køb", "online", "tilbud", "billig" — det er ikke premium-søgeadfærd
- ALDRIG gentag brand + titel 1:1 — søgeordene skal SUPPLERE titlen med nye vinkler
- ALDRIG brug ANDRE brands i søgeordene — brug KUN det brand der står på fakturaen. Aldrig "adidas", "tommy hilfiger", "nike", "new yorker" osv. medmindre det ER produktets brand
- ALDRIG brug tyske ord som "damen", "herren", "kaufen" — skriv KUN dansk
- ALDRIG gentag brand-navnet i søgeord 2 og 3 — brand er allerede i søgeord 1 og i meta title
- Stav brand-navnet PRÆCIST som det står på fakturaen (versaler, accenter osv.)

EKSEMPLER (bemærk: hvert produkt har unikke søgeord baseret på sine egenskaber):
  - Acne Studios ruskindstaske med magnetlommer → ["acne studios ruskindstaske", "ruskind bucket bag med lommer", "designer taske cognac"]
  - CDG finstrikket v-hals i uld → ["comme des garcons strik", "finstrikket pullover uld herre", "designer v-hals strik navy"]
  - Norse Projects bomuldsskjorte → ["norse projects skjorte", "premium bomuldsskjorte herre", "oxford skjorte slim fit"]
  - Acne Studios læderjakke med bælte → ["acne studios læderjakke", "bæltet læderjakke dame", "designer bikerjacket sort"]
{historical_keyword_section}
GRAMMATIK OG SPROGKVALITET — KRITISK:
- "stel" (ALDRIG "stol") — solbriller/briller har et STEL, ikke en stol
- "hætte" (ALDRIG "hatte") — en hoodie har en hætte
- "fremstillet" (ALDRIG "fremstiller") — passiv form
- "ribkant" / "ribstrik" (ALDRIG "ribkanter")
- "stænger" (solbrille-arme) — ALDRIG "ben" for solbriller
- "skåret" (ALDRIG "skaåret") — korrekt stavning
- "bomuld" (ALDRIG "bomud", "bomulding") — korrekt stavning af bomuld
- "lysegrå" (ALDRIG "lysegså", "lysegra") — korrekt stavning
- "profileret" (ALDRIG "profilaret") — korrekt stavning
- "udstående" (ALDRIG "udstaende") — korrekt stavning
- "afsluttet" (ALDRIG "afslutten") — korrekt bøjning
- "Denne nederdel" (IKKE "Dette nederdel") — nederdel er en-ord
- "Denne skjorte... Den er" (IKKE "Det er") — skjorte er en-ord
- "en let, luftig konstruktion" (IKKE "et let, luftigt konstruktion") — konstruktion er en-ord
- "vid pasform" (IKKE "vilde pasform") — "vid" = bred/wide
- "reverskrave" (ét ord, IKKE "reevers krave")
- ALDRIG brug ord du ikke er sikker på eksisterer — brug simple, kendte danske ord
- ALDRIG opfind nonsens-ord som "udprent", "udspændt" (for briller), "faldklinke", "kælig farvezone", "rævende"
- Undgå at modsige dig selv: skriv IKKE "struktureret pasform og afslappet pasform" i samme sætning
- Undgå at gentage den samme information med andre ord i samme sætning (f.eks. "bundkant, forneden")
- Brug KORREKT dansk grammatik: tjek at artikel (en/et) matcher substantivets køn
- Brug KORREKTE stavninger: ingen sammenblandinger af æ/ø/å med ae/oe/aa
- Skriv KUN sætninger du er 100% sikker på er korrekt dansk
{description_examples}
{brand_few_shot_section}
EKSISTERENDE TAGS: {tag_list}

{table_summary}

TAGS ("ai_tags"):
- Giv KUN tags der tilføjer reel værdi for navigation/filtrering
- ALDRIG medtag: farvenavne, materialnavne, engelske produkttyper, køn, vendor-navn
- GODE tags: "Basics", "Nyheder", "acne-products" (kun for Acne Studios)
- DÅRLIGE tags: "Cotton", "Sort", "Shirts", "Male", "T-Shirt", "Minimalist"

MATERIALE ("material"):
- Skriv ALTID på dansk: "100% bomuld", "95% bomuld, 5% elastan"
- Brug: bomuld, uld, silke, hør, polyamid, viskose, elastan, kashmir, polyester, nylon, læder
- Hvis materiale fremgår af fakturaen, brug det. Hvis ikke, lad feltet være tomt "".
- ALDRIG skriv "Ikke oplyst" — brug tom streng i stedet.

ORDRENUMMER, FAKTURANUMMER OG DATO:
Disse står i fakturaens hoved eller i blokken over hver produktlinje. Aflæs dem PRÆCIST — ciffer for ciffer.

- "invoice_number" (øverste niveau): fakturanummeret fra dokumenthovedet.
  Står typisk efter: "Facture N°", "Invoice N°", "Invoice No.", "Rechnung Nr.", "Faktura nr."
  Eksempel: "Facture N° 26046761 du 24/04/2026" → invoice_number = "26046761"
  Eksempel: "Invoice N° 90740886" → invoice_number = "90740886"

- "invoice_date" (øverste niveau): fakturadatoen, ALTID som ISO YYYY-MM-DD.
  Fakturaer bruger europæisk datoformat (dag først) — "24/04/2026" og "29.05.2026" er
  24. april og 29. maj, ALDRIG omvendt.
  Eksempel: "Facture N° 26046761 du 24/04/2026" → invoice_date = "2026-04-24"
  Eksempel: "Invoice date 29.05.2026" → invoice_date = "2026-05-29"

- "order_number" (pr. produkt): ordrenummeret som DEN ENKELTE produktlinje hører til.
  Står typisk efter: "Commande N°", "Order N°", "Order No.", "Auftrag", "PO"
  Eksempel: "Commande N° 2602129660 du 11/02/2026" → order_number = "2602129660"
  Eksempel: "Order N° :" på én linje og "1000067897" på næste → order_number = "1000067897"
  Én faktura kan dække FLERE ordrer — giv hvert produkt det ordrenummer der står i
  netop dens blok. Hvis hele fakturaen kun har ét ordrenummer, brug det på alle produkter.

- Forveksl ALDRIG ordrenummer med fakturanummer, kundenummer ("Customer N°"),
  leveringsnummer ("BL client N°", "SH.N°"), momsnummer ("VAT N°") eller varenummer.
- Hvis et nummer ikke findes på fakturaen: brug tom streng "". ALDRIG gæt eller opfind et nummer.

SÆSON ("season"):
- Skriv sæsonen PRÆCIST som den står på fakturaen — appen normaliserer den bagefter.
- Står typisk efter: "Saison", "Season", "Collection".
  Eksempel: "Saison : E26" → season = "E26"
  Eksempel: "Season: Pre-Spring 2027" → season = "Pre-Spring 2027"
- Oversæt eller omskriv den IKKE. Skriv "AV26" hvis der står "AV26", ikke "AW26".
- Hvis sæsonen ikke fremgår: brug tom streng "".

Brug submit_extracted_products-toolen til at returnere ALLE produkter som struktureret data.
Hvert produkt skal have alle felter udfyldt korrekt."""

    # Build user message with BOTH images and text
    user_content_parts: list[dict] = []

    # Truncate excessive PDF pages
    MAX_PDF_PAGES = 25
    if pdf_images and len(pdf_images) > MAX_PDF_PAGES:
        logger.warning(f"PDF has {len(pdf_images)} pages, truncating to {MAX_PDF_PAGES}")
        pdf_images = pdf_images[:MAX_PDF_PAGES]

    # Add PDF page images (Vision) — this is the PRIMARY source
    if pdf_images:
        for img_b64 in pdf_images:
            user_content_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            })

    # Add text as supplement
    user_content_parts.append({
        "type": "text",
        "text": f"""Udtræk ALLE produkter fra denne leverandørfaktura.
{table_summary}
FAKTURA-TEKST (supplement til billederne):
{pdf_text}""",
    })

    # ── Tool definition for structured output (Opt 7) ──
    # Using Anthropic's tool_use forces the model to output valid JSON
    # matching the schema — eliminates fragile regex/text parsing.
    _extraction_tool = {
        "name": "submit_extracted_products",
        "description": "Submit all extracted products from the invoice as structured data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "products": {
                    "type": "array",
                    "description": "Array of all products extracted from the invoice",
                    "items": {
                        "type": "object",
                        "properties": {
                            "style_code": {"type": "string", "description": "Article number/SKU exactly as shown on invoice"},
                            "title": {"type": "string", "description": "Product name + color in Title Case"},
                            "vendor": {"type": "string", "description": "Brand/vendor name"},
                            "product_type": {"type": "string", "description": "English product type: Shirt, Trouser, Hoodie, etc."},
                            "gender": {"type": "string", "enum": ["Men", "Women", "Unisex"]},
                            "color": {"type": "string", "description": "Simple Danish/English color name"},
                            "color_original": {"type": "string", "description": "Exact color name from invoice"},
                            "material": {"type": "string", "description": "Material in Danish, or empty string"},
                            "details": {"type": "string", "description": "Danish product description (3-5 sentences)"},
                            "details_en": {"type": "string", "description": "English product description"},
                            "country_of_origin": {"type": "string", "description": "ISO country code"},
                            "hs_code": {"type": "string", "description": "HS/tariff code if available"},
                            "season": {"type": "string", "description": "Season exactly as printed on the invoice, e.g. 'AV26', 'E26', 'Pre-Spring 2027'. Empty string if absent."},
                            "order_number": {"type": "string", "description": "Order number this line belongs to (Commande N°, Order N°, etc.). Empty string if absent."},
                            "invoice_number": {"type": "string", "description": "Invoice number this line belongs to, if the PDF covers more than one invoice. Otherwise empty string."},
                            "cost_price_eur": {"type": "number", "description": "Net unit price in EUR after discount"},
                            "discount_pct": {"type": "number", "description": "Discount percentage (0 if none)"},
                            "seo_keywords": {"type": "array", "items": {"type": "string"}, "description": "2-3 Danish search keywords based on this specific product's properties"},
                            "ai_tags": {"type": "array", "items": {"type": "string"}, "description": "Relevant navigation tags"},
                            "variants": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "size": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                    },
                                    "required": ["size", "quantity"],
                                },
                            },
                        },
                        "required": ["style_code", "title", "vendor", "product_type", "cost_price_eur", "variants"],
                    },
                },
                "invoice_number": {
                    "type": "string",
                    "description": "Invoice number from the document header (Facture N°, Invoice N°). Empty string if absent.",
                },
                "invoice_date": {
                    "type": "string",
                    "description": "Invoice date from the document header as ISO YYYY-MM-DD. Empty string if absent.",
                },
            },
            "required": ["products"],
        },
    }

    logger.info(f"Starting Claude API call (model={model}, images={len(pdf_images) if pdf_images else 0})")
    api_start = time.time()

    max_retries = 3
    for attempt in range(max_retries):
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=16384,
                messages=[
                    {
                        "role": "user",
                        "content": user_content_parts,
                    }
                ],
                system=system_prompt,
                tools=[_extraction_tool],
                tool_choice={"type": "tool", "name": "submit_extracted_products"},
            )
            break
        except Exception as e:
            if not is_retryable_api_error(e):
                # A spend cap or a rejected key will fail the same way every
                # time; retrying only delays the report.
                logger.error(f"Claude API call failed and is not retryable: {e}")
                raise
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt  # 1, 2, 4 seconds
            logger.warning(f"Claude API attempt {attempt + 1} failed: {e}, retrying in {wait_time}s")
            await asyncio.sleep(wait_time)

    api_elapsed = time.time() - api_start
    logger.info(f"Claude API call completed in {api_elapsed:.1f}s")
    if hasattr(message, 'usage') and message.usage:
        logger.info(f"Token usage: input={message.usage.input_tokens}, output={message.usage.output_tokens}")

    # Extract structured data from tool_use response
    raw_products = None
    ai_invoice_number = ""
    ai_invoice_date = ""
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_extracted_products":
            tool_input = block.input
            raw_products = tool_input.get("products", [])
            ai_invoice_number = (tool_input.get("invoice_number") or "").strip()
            ai_invoice_date = (tool_input.get("invoice_date") or "").strip()
            break

    # Fallback: if tool_use didn't work (shouldn't happen with tool_choice),
    # try legacy text parsing
    if raw_products is None:
        logger.warning("Tool use response not found, falling back to text parsing")
        response_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                response_text += block.text
        json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
        if json_match:
            raw_products = json.loads(json_match.group(1))
        else:
            start = response_text.find('[')
            if start != -1:
                depth = 0
                end = -1
                for i in range(start, len(response_text)):
                    if response_text[i] == '[':
                        depth += 1
                    elif response_text[i] == ']':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end != -1:
                    raw_products = json.loads(response_text[start:end + 1])
                else:
                    raise ValueError("Could not find matching ] in AI response")
            else:
                raise ValueError("No JSON array found in AI response")

    # -- Validate extracted products --
    validated = []
    for p in raw_products:
        # Required fields
        if not p.get("title") or not isinstance(p.get("title"), str):
            logger.warning(f"Skipping product with missing/invalid title: {p}")
            continue
        if not p.get("vendor"):
            logger.warning(f"Skipping product with missing vendor: {p.get('title', 'unknown')}")
            continue

        # Sanitize numeric fields
        try:
            if p.get("cost_price_eur") is not None:
                p["cost_price_eur"] = float(p["cost_price_eur"])
        except (ValueError, TypeError):
            p["cost_price_eur"] = None

        # Sanitize discount_pct
        try:
            p["discount_pct"] = float(p.get("discount_pct", 0) or 0)
            if p["discount_pct"] < 0 or p["discount_pct"] > 90:
                p["discount_pct"] = 0  # Sanity: unrealistic discount
        except (ValueError, TypeError):
            p["discount_pct"] = 0

        # Ensure variants is a list
        if not isinstance(p.get("variants"), list):
            p["variants"] = []

        # Validate variant quantities
        for v in p.get("variants", []):
            try:
                v["quantity"] = int(v.get("quantity", 0))
            except (ValueError, TypeError):
                v["quantity"] = 0

        # ── Fix SKU-as-title: detect and replace ──
        title = p.get("title", "")
        style_code = p.get("style_code", "")
        # Detect if title looks like a SKU/article code:
        # - Starts with the style_code
        # - Contains patterns like "XX-YY-ZZZZ000NNN" or all-uppercase codes
        title_is_sku = False
        if style_code and title.upper().startswith(style_code.upper()):
            title_is_sku = True
        elif re.match(r'^[A-Z]{2,4}[-_][A-Z]{2,4}[-_][A-Z]{3,5}\d{4,}', title):
            title_is_sku = True
        elif re.match(r'^[A-Z0-9]{4,}[-_][A-Z0-9]{2,}', title) and not any(c.islower() for c in title.split()[0]):
            title_is_sku = True

        if title_is_sku:
            # Build a meaningful title from available data
            product_type = p.get("product_type", "")
            color_orig = p.get("color_original", p.get("color", ""))
            # Strip the SKU prefix from the title to extract any trailing color/description
            remainder = title
            if style_code:
                remainder = re.sub(re.escape(style_code), '', title, flags=re.IGNORECASE).strip()
            # If remainder is just a color name, combine with product type
            if product_type and remainder:
                p["title"] = f"{product_type.title()} {remainder.title()}"
            elif product_type and color_orig:
                p["title"] = f"{product_type.title()} {color_orig.title()}"
            elif remainder:
                p["title"] = remainder.title()
            else:
                p["title"] = f"{p.get('vendor', 'Unknown')} {product_type.title()}"
            logger.info(f"Fixed SKU-as-title: '{title}' → '{p['title']}'")

        # ── Additional title quality checks ──
        title = p.get("title", "").strip()

        # Fix: Title starts with a number (likely SKU leaking in)
        # Exception: legitimate names like "3-Pack", "501 Jean", "7 For All Mankind"
        if title and re.match(r'^\d{3,}[\s]', title) and not re.match(r'^\d{1,3}[-]', title):
            # Try stripping leading numbers
            cleaned = re.sub(r'^\d+\s*', '', title).strip()
            if cleaned and len(cleaned) > 3:
                logger.info(f"Stripped leading numbers from title: '{title}' → '{cleaned}'")
                p["title"] = cleaned
                title = cleaned

        # Fix: Title is too short (< 3 chars) or is just a number
        if title and (len(title) < 3 or re.match(r'^\d+$', title)):
            product_type = p.get("product_type", "")
            color = p.get("color", p.get("color_original", ""))
            if product_type and color:
                p["title"] = f"{product_type.title()} {color.title()}"
            elif product_type:
                p["title"] = product_type.title()
            logger.info(f"Fixed too-short title: '{title}' → '{p['title']}'")

        # Fix: Title is identical to style_code (shouldn't happen after above, but safety net)
        if style_code and title.strip().lower() == style_code.strip().lower():
            product_type = p.get("product_type", "")
            color = p.get("color", "")
            if product_type:
                p["title"] = f"{product_type.title()} {color.title()}".strip()
                logger.info(f"Title was identical to SKU, reconstructed: '{title}' → '{p['title']}'")

        validated.append(p)
    raw_products = validated
    logger.info(f"Extracted {len(raw_products)} validated products from AI response")

    # -- Post-processing --

    # CRITICAL: Force-override variants with table-extracted data (100% accurate)
    if table_products:
        # Build lookup: style_code -> table product
        table_lookup = {tp["style_code"].upper(): tp for tp in table_products}

        for p in raw_products:
            ai_sku = (p.get("style_code") or "").upper()
            if ai_sku in table_lookup:
                tp = table_lookup[ai_sku]
                # Override variants with deterministic table data — BUT only if table actually has size data
                # (Carhartt text parser returns variants=[] with needs_size_lookup=True,
                #  in which case AI Vision data is more reliable)
                if tp["variants"]:
                    p["variants"] = tp["variants"]
                # If table has no variants but has total_qty and AI has no variants either,
                # create a single "One Size" variant as fallback
                elif not p.get("variants") and tp.get("total_qty", 0) > 0:
                    p["variants"] = [{"size": "One Size", "quantity": tp["total_qty"]}]
                # Override cost if AI got it wrong
                if tp["cost_price_eur"] > 0:
                    p["cost_price_eur"] = tp["cost_price_eur"]
                # Ensure color_original matches table
                if tp["color_original"] and not p.get("color_original"):
                    p["color_original"] = tp["color_original"]
                # Order/invoice/season read literally off the invoice text beat
                # anything Vision inferred, so they override rather than fill in.
                if tp.get("order_number"):
                    p["order_number"] = tp["order_number"]
                if tp.get("invoice_number"):
                    p["invoice_number"] = tp["invoice_number"]
                if tp.get("invoice_date"):
                    p["invoice_date"] = tp["invoice_date"]
                if tp.get("season_raw"):
                    p["season"] = tp["season_raw"]

        # Check if any table products are MISSING from AI output — add them
        ai_skus = {(p.get("style_code") or "").upper() for p in raw_products}
        for tp in table_products:
            if tp["style_code"].upper() not in ai_skus:
                # AI missed this product entirely — create a basic entry
                designation = tp["designation"]
                color_orig = tp["color_original"]
                raw_products.append({
                    "style_code": tp["style_code"],
                    "title": f"{designation.title()} {color_orig.title()}",
                    "vendor": "",  # Will be filled from PDF context
                    "product_type": designation.title(),
                    "gender": "Unisex",
                    "color": color_orig.title(),
                    "color_original": color_orig,
                    "material": "",
                    "details": "",
                    "details_en": "",
                    "country_of_origin": "",
                    "hs_code": "",
                    "season": tp.get("season_raw", ""),
                    "order_number": tp.get("order_number", ""),
                    "invoice_number": tp.get("invoice_number", ""),
                    "invoice_date": tp.get("invoice_date", ""),
                    "cost_price_eur": tp["cost_price_eur"],
                    "ai_tags": [],
                    "variants": tp["variants"],
                })

    for p in raw_products:
        # -- Order / invoice provenance --
        # Deterministic table data (set below by the caller-supplied table_products
        # merge) wins over the AI value; the invoice header fills any remaining gap.
        p["order_number"] = (p.get("order_number") or "").strip()
        p["invoice_number"] = (p.get("invoice_number") or "").strip() or ai_invoice_number
        p["invoice_date"] = (p.get("invoice_date") or "").strip() or ai_invoice_date

        # -- Season: keep what the invoice said, add the canonical form --
        season_raw = (p.get("season") or "").strip()
        p["season_raw"] = season_raw
        p["season_normalized"] = normalize_season(season_raw)

        # -- Normalize vendor name to match brand collection --
        p["vendor"] = _normalize_vendor(p.get("vendor", ""))

        # -- Normalize color name (fix spelling errors, compound names) --
        if p.get("color"):
            p["color"] = _normalize_color_name(p["color"])

        # -- Validate and fix color assignment --
        if p.get("color"):
            p["color"] = _validate_color(p["color"], p.get("color_original", ""), p.get("title", ""))

        # Fix descriptions: remove forbidden phrases, vendor refs, grammar
        vendor_name = p.get("vendor", "")
        for field in ("details", "details_en"):
            text = p.get(field, "")
            if text:
                text = _clean_description(text, vendor=vendor_name)
                p[field] = text

        # Ensure color_original exists
        if not p.get("color_original"):
            p["color_original"] = p.get("color", "")

        # Title sanity: strip numeric color codes from end of title
        # E.g. "Jökla Jacket 900" → "Jökla Jacket Black" (using translated color)
        title = p.get("title", "")
        color_orig = p.get("color_original", "")
        color_translated = p.get("color", "")
        if title and color_orig:
            # Check if title ends with the raw color_original and it's a numeric code
            stripped_orig = color_orig.strip()
            if stripped_orig and re.match(r'^\d{2,5}$', stripped_orig):
                # It's a numeric code like "900", "001", "0900"
                # Check if it appears at the end of the title
                pattern = re.compile(r'\s+' + re.escape(stripped_orig) + r'$', re.IGNORECASE)
                if pattern.search(title):
                    # Replace with translated color name if available, otherwise just remove
                    if color_translated and not re.match(r'^\d+$', color_translated.strip()):
                        new_title = pattern.sub(f' {color_translated.strip().title()}', title)
                    else:
                        new_title = pattern.sub('', title)
                    logger.info(f"Stripped numeric color code from title: '{title}' → '{new_title}'")
                    title = new_title
                    p["title"] = title

        # Also catch numeric codes anywhere in the title that match color_original
        if title and color_orig and re.match(r'^\d{2,5}$', color_orig.strip()):
            # Check for the code as a standalone word in the title (not just at end)
            code_pattern = re.compile(r'\b' + re.escape(color_orig.strip()) + r'\b')
            if code_pattern.search(title):
                if color_translated and not re.match(r'^\d+$', color_translated.strip()):
                    new_title = code_pattern.sub(color_translated.strip().title(), title)
                else:
                    new_title = code_pattern.sub('', title).strip()
                    new_title = re.sub(r'\s{2,}', ' ', new_title)  # Clean double spaces
                if new_title != title:
                    logger.info(f"Replaced numeric color code in title: '{title}' → '{new_title}'")
                    title = new_title
                    p["title"] = title

        # Title sanity: no ALL CAPS
        if title and title == title.upper() and len(title) > 3:
            p["title"] = title.title()

        # Validate SEO keywords against product data (brand-agnostic)
        if p.get("seo_keywords"):
            from app.services.product_enrichment import validate_seo_keywords
            p["seo_keywords"] = validate_seo_keywords(p["seo_keywords"], p)

        # Ensure cost_price_eur is a number
        try:
            p["cost_price_eur"] = float(p.get("cost_price_eur", 0))
        except (ValueError, TypeError):
            p["cost_price_eur"] = 0

        # Ensure discount_pct is a number
        try:
            p["discount_pct"] = float(p.get("discount_pct", 0) or 0)
        except (ValueError, TypeError):
            p["discount_pct"] = 0

        # Reject impossible discount values (>= 100% would cause division by zero)
        if p["discount_pct"] >= 100:
            logger.warning(f"Discount {p['discount_pct']}% is >= 100%% — resetting to 0")
            p["discount_pct"] = 0

        # Calculate gross_price_eur (pre-discount price)
        # If AI reported a discount, back-calculate gross from net
        if p["discount_pct"] > 0 and p["cost_price_eur"] > 0:
            divisor = 1 - p["discount_pct"] / 100
            if divisor <= 0:
                # Safety: should not happen after validation above, but guard anyway
                p["gross_price_eur"] = p["cost_price_eur"]
            else:
                p["gross_price_eur"] = round(p["cost_price_eur"] / divisor, 2)
        else:
            p["gross_price_eur"] = p["cost_price_eur"]  # No discount = gross equals net

        # Clean up variant quantities and fix invalid size labels
        for v in p.get("variants", []):
            try:
                v["quantity"] = int(v.get("quantity", 0))
            except (ValueError, TypeError):
                v["quantity"] = 0

            # ── Fix mis-parsed size labels ──
            size = v.get("size", "")
            if size:
                v["size"] = _fix_size_label(size)

    return raw_products


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


# Season codes that stand alone on an invoice. Matched against the season
# string with every non-letter stripped, so only exact hits count — "PE" must
# not fire on "PRESPRING".
_SEASON_EXACT_CODES = {
    "PS": "PS",   # Pre-Spring
    "PF": "PF",   # Pre-Fall
    "AW": "AW",   # Autumn/Winter
    "AV": "AW",   # seen on invoices as an Autumn/Winter code
    "FW": "AW",   # Fall/Winter
    "HW": "AW",   # Herbst/Winter (German)
    "AH": "AW",   # Automne/Hiver (French)
    "SS": "SS",   # Spring/Summer
    "PE": "SS",   # Printemps/Été (French)
    # Single-letter French season codes — American Vintage writes "Saison : E26"
    "E": "SS",    # Été (summer)
    "H": "AW",    # Hiver (winter)
}

# Word forms, checked in order. "PRE..." variants come first so that
# "Pre-Spring" never falls through to the plain "SPRING" rule.
_SEASON_WORD_PATTERNS = [
    ("PRESPRING", "PS"),
    ("PREFALL", "PF"),
    ("PREAUTUMN", "PF"),
    ("RESORT", "PF"),
    ("CRUISE", "PF"),
    ("FALLWINTER", "AW"),
    ("AUTUMNWINTER", "AW"),
    ("HERBSTWINTER", "AW"),
    ("AUTOMNEHIVER", "AW"),
    ("SPRINGSUMMER", "SS"),
    ("PRINTEMPSETE", "SS"),
    ("AUTUMN", "AW"),
    ("WINTER", "AW"),
    ("FALL", "AW"),
    ("SPRING", "SS"),
    ("SUMMER", "SS"),
]

_SEASON_YEAR_RE = re.compile(r"(?:19|20)(\d{2})(?!\d)|(?<!\d)(\d{2})(?!\d)")


def normalize_season(raw: str) -> str:
    """
    Map a season string from an invoice to a canonical "<CODE><YY>" form.

        "AV26" / "FW26" / "Fall/Winter 2026" / "Autumn/Winter 26"  -> "AW26"
        "SS27" / "Spring/Summer 2027" / "S/S 27"                   -> "SS27"
        "Pre-Spring 2027" / "PS27"                                 -> "PS27"
        "Pre-Fall 2026" / "PF26" / "Resort 2026"                   -> "PF26"

    Returns "" when the season or the year cannot be determined, so callers
    can fall back to the raw value rather than store a wrong guess.
    """
    if not raw:
        return ""

    # Strip everything but letters and digits: "Spring/Summer 2027" -> "SPRINGSUMMER2027"
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if not compact:
        return ""

    year_match = _SEASON_YEAR_RE.search(compact)
    if not year_match:
        return ""
    year = year_match.group(1) or year_match.group(2)

    letters = re.sub(r"[^A-Z]", "", compact)
    if not letters:
        return ""

    code = _SEASON_EXACT_CODES.get(letters)
    if code is None:
        for pattern, mapped in _SEASON_WORD_PATTERNS:
            if pattern in letters:
                code = mapped
                break

    if code is None:
        return ""

    return f"{code}{year}"


def _fix_size_label(size: str) -> str:
    """
    Fix mis-parsed size labels from AI extraction.
    Common errors: "K/LA" → "XL/A", OCR confusion between K↔X, etc.
    """
    if not size:
        return size

    s = size.strip()

    # ── Known bad patterns → corrections ──
    # "K/LA" is almost certainly "XL/A" or just "XL" (OCR/AI misread)
    size_corrections = {
        "K/LA": "XL/A",
        "k/la": "XL/A",
        "K/L": "XL",
        "k/l": "XL",
        # Common OCR confusions
        "0S": "OS",    # zero → O (One Size)
        "0NE": "ONE",
        "X5": "XS",    # 5 → S
        "5": "S",       # standalone 5 when context is letter sizes (only in combined)
    }
    if s in size_corrections:
        return size_corrections[s]
    if s.upper() in size_corrections:
        return size_corrections[s.upper()]

    # ── Validate combined sizes like "M/S", "L/M" — ensure both parts are real sizes ──
    VALID_LETTER_SIZES = {"XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "OS", "ONE SIZE"}
    if "/" in s:
        parts = [p.strip().upper() for p in s.split("/")]
        if len(parts) == 2:
            # Check if either part looks like a garbled size
            fixed_parts = []
            for part in parts:
                if part in VALID_LETTER_SIZES:
                    fixed_parts.append(part)
                elif part == "K" or part == "KL":
                    fixed_parts.append("XL")  # K is almost always misread X
                elif part == "KS":
                    fixed_parts.append("XS")
                elif part == "KLA" or part == "K/LA":
                    fixed_parts.append("XL")
                elif part == "LA":
                    # "LA" in a size context is probably "L" + artifact, or part of "XL/A" pattern
                    fixed_parts.append("L")
                else:
                    fixed_parts.append(part)  # Keep as-is if unknown
            return "/".join(fixed_parts)

    return s


def _normalize_color_name(color: str) -> str:
    """
    Normalize and fix common AI color translation errors for the Color-Name metafield.
    Ensures consistent, correct Danish color names.
    """
    if not color:
        return color

    c = color.strip()

    # -- Fix common AI misspellings --
    spelling_fixes = {
        "lysrød": "Lyserød",
        "mørk brun": "Mørkebrun",
        "mørk blå": "Mørkeblå",
        "mørk grøn": "Mørkegrøn",
        "mørk grå": "Mørkegrå",
        "lys blå": "Lyseblå",
        "lys grå": "Lysegrå",
        "lys grøn": "Lysegrøn",
        "lys brun": "Lysebrun",
        "lys rosa": "Lyserosa",
        "lys lilla": "Lyselilla",
    }

    c_lower = c.lower()
    for wrong, correct in spelling_fixes.items():
        if c_lower == wrong:
            return correct

    # -- Fix compound color names with "/" (e.g., "Sølv/Lysrød" -> "Sølv/Lyserød") --
    if "/" in c:
        parts = c.split("/")
        fixed_parts = []
        for part in parts:
            part = part.strip()
            part_lower = part.lower()
            # Check each part against spelling fixes
            fixed = False
            for wrong, correct in spelling_fixes.items():
                if part_lower == wrong:
                    fixed_parts.append(correct)
                    fixed = True
                    break
            if not fixed:
                # Ensure title case
                fixed_parts.append(part.capitalize() if part == part.lower() else part)
        return "/".join(fixed_parts)

    return c


def _validate_color(color: str, color_original: str, title: str) -> str:
    """
    Validate and fix color assignments.
    Catches: redundant colors (Blå/Blå/Marineblå), wrong translations,
    and known brand color names that shouldn't be translated literally.
    """
    if not color:
        return color

    c = color.strip()

    # ── Fix redundant color parts (e.g. "Blå/Blå/Marineblå" → "Marineblå") ──
    if "/" in c:
        parts = [p.strip() for p in c.split("/")]
        # Remove exact duplicates while preserving order
        seen = []
        for p in parts:
            p_lower = p.lower()
            # Skip if it's a less-specific version of another part
            is_subset = False
            for existing in seen:
                if p_lower in existing.lower() or existing.lower() in p_lower:
                    # Keep the more specific one
                    if len(p) > len(existing):
                        seen.remove(existing)
                        seen.append(p)
                    is_subset = True
                    break
            if not is_subset:
                seen.append(p)
        if len(seen) < len(parts):
            c = "/".join(seen) if len(seen) > 1 else seen[0] if seen else c

    # ── Fix known wrong translations of brand color names ──
    # "Simple Rinse" is a dark denim wash, NOT "Lysblå" (light blue)
    color_orig_lower = color_original.lower().strip() if color_original else ""
    DARK_WASH_NAMES = {"simple rinse", "dark rinse", "dark wash", "rinse", "raw denim", "indigo rinse"}
    if color_orig_lower in DARK_WASH_NAMES and c.lower() in ("lysblå", "lyseblå", "lys blå"):
        return "Mørk indigo"

    # "Moonbeam" = lys creme (not dark)
    LIGHT_COLOR_NAMES = {"moonbeam", "vanilla cream", "cream", "ivory", "ecru", "off-white", "eggshell"}
    DARK_COLOR_WORDS = {"sort", "mørk", "black", "dark", "navy"}
    if color_orig_lower in LIGHT_COLOR_NAMES and any(d in c.lower() for d in DARK_COLOR_WORDS):
        return "Lys creme"

    # "Seal Brown" = mørk brun
    DARK_BROWN_NAMES = {"seal brown", "dark brown", "chocolate", "espresso", "chocolate chip"}
    if color_orig_lower in DARK_BROWN_NAMES and c.lower() in ("brun", "lysebrun", "lys brun"):
        return "Mørkebrun"

    return c


def _normalize_vendor(vendor: str) -> str:
    """
    Normalize vendor name to match the official brand collection name in Shopify.
    This ensures Vendor field and Brand Collection are always consistent.
    Maps common AI abbreviations/variants to the canonical brand name.
    """
    if not vendor:
        return vendor

    v = vendor.strip()
    v_lower = v.lower()

    # -- Canonical vendor mapping --
    # Keys: lowercase partial match -> Value: official Shopify brand name
    vendor_map = {
        "flatlist": "Flatlist Eyewear",
        "monokel": "Monokel Eyewear",
        "american vintage": "American Vintage",
        "comme des garcons": "Comme des Garçons",
        "comme des garçons": "Comme des Garçons",
        "cdg": "Comme des Garçons",
        "acne studios": "Acne Studios",
        "acne": "Acne Studios",
        "a.p.c": "A.P.C.",
        "apc": "A.P.C.",
        "carhartt": "Carhartt WIP",
        "nn07": "NN07",
        "nn.07": "NN07",
        "new balance": "New Balance",
        "norse projects": "Norse Projects",
        "our legacy": "Our Legacy",
        "maison margiela": "Maison Margiela",
        "mm6": "MM6 Maison Margiela",
        "birkenstock": "Birkenstock",
        "modström": "Modström",
        "modstrom": "Modström",
        "salomon": "Salomon",
        "sunflower": "Sunflower",
        "service works": "Service Works",
        "alohas": "ALOHAS",
        "marni": "Marni",
        "mizuno": "Mizuno",
        "timberland": "Timberland",
        "66 north": "66°North",
        "toteme": "TOTEME",
        "totême": "TOTEME",
        "totème": "TOTEME",
        "parel studios": "Parel Studios",
        "hestra": "Hestra",
        "oamc": "OAMC",
        "sophie bille": "Sophie Bille Brahe",
        "sofie ladefoged": "Sofie Ladefoged",
        "dragon diffusion": "Dragon Diffusion",
        "berner kühl": "Berner Kühl",
        "berner kuhl": "Berner Kühl",
        "gabi": "GABI",
        "fichi": "Fichi",
        "flowerism": "Flowerism Studio",
        "saye": "SAYE",
        "closed": "CLOSED",
        "ragbag": "Ragbag",
        "cavalieri": "Cavalieri",
        "hay": "HAY",
    }

    # Try exact match first (case insensitive)
    if v_lower in vendor_map:
        return vendor_map[v_lower]

    # Try partial/contains match (longest match wins)
    # Use word boundaries for short keys to avoid false substring matches
    best_match = None
    best_len = 0
    for key, canonical in vendor_map.items():
        if len(key) <= 4:
            # Short keys like "hay", "acne", "cdg" — require word boundary match
            if re.search(rf'\b{re.escape(key)}\b', v_lower) and len(key) > best_len:
                best_match = canonical
                best_len = len(key)
        else:
            if key in v_lower and len(key) > best_len:
                best_match = canonical
                best_len = len(key)

    if best_match:
        return best_match

    # No mapping found — return as-is (title-cased for consistency)
    return v


def _clean_description(text: str, vendor: str = "") -> str:
    """Remove forbidden phrases, fix grammar, strip vendor references from AI-generated descriptions."""
    if not text:
        return text

    # -- Fix common grammar errors (hard fixes for AI mistakes) --
    grammar_fixes = [
        # CRITICAL: "stol" (chair) -> "stel" (frame) — extremely common AI error for eyewear
        (r'\bet\b(\s+\w+\s+)stol\b', r'et\1stel'),    # "et acetat stol" -> "et acetat stel"
        (r'\b([Ss])tol(?=\s+(?:og|med|i|har|er|fra))', r'\1tel'),  # "stol og" -> "stel og"
        (r'(?<=[- ])stol(?=[- .,:;!?])', 'stel'),       # " stol." -> " stel."
        (r'\b([Ss])tolet\b', r'\1tellet'),              # "stolet" -> "stellet"
        (r'\bhatte\b', 'hætte'),        # hoodie har en hætte, ikke hatte
        (r'\bfremstiller\b', 'er fremstillet'),  # passiv: er fremstillet, ikke fremstiller
        (r'\bribkanter\b', 'ribkant'),   # singular
        # Eyewear-specific fixes
        (r'\bben\b(?=.*(?:solbrill|glass|lens|stel))', 'stænger'),  # "ben" -> "stænger" in eyewear context

        # ── Spelling fixes (observed AI errors) ──
        (r'\bskaåret\b', 'skåret'),                     # "skaåret" -> "skåret"
        (r'\bbomulding\b', 'bomuld'),                    # "bomulding" -> "bomuld"
        (r'\bbomud\b', 'bomuld'),                        # "bomud" -> "bomuld"
        (r'\blysegså\b', 'lysegrå'),                     # "lysegså" -> "lysegrå"
        (r'\blysegra\b', 'lysegrå'),                     # "lysegra" -> "lysegrå"
        (r'\bprofilaret\b', 'profileret'),               # "profilaret" -> "profileret"
        (r'\budstaende\b', 'udstående'),                 # "udstaende" -> "udstående"
        (r'\bafslutten\b', 'afsluttet'),                 # "afslutten" -> "afsluttet"
        (r'\breevers?\s+krave\b', 'reverskrave'),        # "reevers krave" -> "reverskrave"

        # ── Grammar: wrong article (en/et mismatch) ──
        (r'\b[Dd]ette nederdel\b', 'Denne nederdel'),   # "Dette nederdel" -> "Denne nederdel"
        (r'\b[Dd]ette skjorte\b', 'Denne skjorte'),     # skjorte = en-ord
        (r'\b[Dd]ette jakke\b', 'Denne jakke'),          # jakke = en-ord
        (r'\b[Dd]ette kjole\b', 'Denne kjole'),          # kjole = en-ord
        (r'\b[Dd]ette bluse\b', 'Denne bluse'),          # bluse = en-ord
        (r'\b[Dd]ette vest\b', 'Denne vest'),            # vest = en-ord
        (r'\b[Dd]ette top\b', 'Denne top'),              # top = en-ord
        (r'\bDet er designet\b(?=.*(?:skjort|jakk|kjol|blus|vest|top|nederd|bukse))', 'Den er designet'),

        # ── Grammar: adjective agreement ──
        (r'\bet let,?\s*luftigt? konstruktion\b', 'en let, luftig konstruktion'),
        (r'\bet afslappet konstruktion\b', 'en afslappet konstruktion'),

        # ── Nonsense word removal ──
        (r'\b[Uu]dspændt\b', 'udstyret'),               # "udspændt" -> "udstyret" (for eyewear)
        (r'\b[Uu]dprent\b', 'udstyret'),                 # "udprent" -> "udstyret"
        (r'\bkælig farvezone\b', 'farvetone'),            # "kælig farvezone" -> "farvetone"
        (r'\brævende lysende\b', 'lyse'),                 # "rævende lysende" -> "lyse"
        (r'\brævende\b', ''),                             # remove standalone "rævende"
        (r'\bfaldklinke\s*\w*\b', ''),                   # remove "faldklinke tæppe" etc.
        (r'\bcloce\b', 'tætsiddende'),                    # "cloce" -> "tætsiddende"
        (r'\bstriklt\b', 'strikket'),                     # "striklt" -> "strikket"
        (r'\bvilde pasform\b', 'vid pasform'),            # "vilde pasform" -> "vid pasform"
    ]
    for pattern, replacement in grammar_fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # -- Remove vendor/brand name references from the description --
    # (the app prepends "[Title] fra [Vendor]." so it should NOT appear in details)
    if vendor:
        # Remove patterns like "er fra [Vendor].", "er et [Vendor] produkt", etc.
        text = re.sub(rf'\s*er fra {re.escape(vendor)}\.?\s*', ' ', text, flags=re.IGNORECASE)
        text = re.sub(rf'\s*fra {re.escape(vendor)}\.?\s*', ' ', text, flags=re.IGNORECASE)
        # Remove standalone vendor mentions
        text = re.sub(rf"\b{re.escape(vendor)}(?:'s|s)?\b", '', text, flags=re.IGNORECASE)
        # Clean up orphaned "Dette/Denne ... er ." after vendor removal
        text = re.sub(r'(?:Dette|Denne)\s+\w+\s+er\s*\.\s*', '', text)

    # Forbidden phrases — remove sentences containing these
    forbidden = [
        "fremgår ikke", "kan ikke udledes", "ikke tilgængelig",
        "ikke muligt at afgøre", "ikke muligt at fastslå",
        "style den med", "perfekt til", "ideel til", "passer godt til",
        "typisk for mærket", "kendetegnet ved", "kollektionen",
        "fakturaen", "kilden", "manglende information",
        "ikke angivet", "ikke specificeret", "kan ikke bestemmes",
        "ikke oplyst", "ikke kendt",
        "fra fakturaen", "af fakturaen", "på fakturaen",
        "information er ikke", "data er ikke", "oplysninger er ikke",
        # Fluffy marketing language
        "minimalistiske æstetik", "minimalistisk udtryk", "moderne udtryk",
        "karakteristiske", "tidløs", "elegant", "raffineret",
        "minimalistiske", "skandinavisk", "nordisk æstetik",
    ]

    # Split into sentences and filter
    sentences = re.split(r'(?<=[.!?])\s+', text)
    clean_sentences = []
    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(phrase in sentence_lower for phrase in forbidden):
            continue
        # Also skip very short meaningless sentences
        if len(sentence.strip()) < 5:
            continue
        clean_sentences.append(sentence)

    result = " ".join(clean_sentences).strip()

    # -- HARD FIX: Catch any remaining "stol" that should be "stel" --
    # This is the nuclear option: in product descriptions, "stol" almost always means "stel"
    # Only exception would be actual furniture products, which STROM doesn't sell
    if "solbrill" in result.lower() or "glass" in result.lower() or "lens" in result.lower() or "brille" in result.lower():
        result = re.sub(r'\bstol\b', 'stel', result, flags=re.IGNORECASE)
        result = re.sub(r'\bstolet\b', 'stellet', result, flags=re.IGNORECASE)
        result = re.sub(r'\bstolen\b', 'stellet', result, flags=re.IGNORECASE)
        result = re.sub(r'\bstole\b', 'stel', result, flags=re.IGNORECASE)

    # -- Fix contradictory statements --
    # "struktureret pasform og en afslappet pasform" → keep only "afslappet pasform"
    result = re.sub(
        r'(?:en\s+)?struktureret\s+pasform\s+og\s+(?:en\s+)?afslappet\s+pasform',
        'en afslappet pasform', result, flags=re.IGNORECASE
    )
    # "tætsiddende snit ... regular pasform" → keep "regular pasform"
    result = re.sub(
        r'(?:et\s+)?tætsiddende\s+snit\s+(?:med\s+)?(?:et\s+)?(?:trykt\s+logo\s*\.?\s*)?(?:og\s+)?(?:en\s+)?(?:klassisk\s+)?regular\s+pasform',
        'regular pasform med et trykt logo', result, flags=re.IGNORECASE
    )
    # "bundkant, forneden" → just "bundkant"
    result = re.sub(r'\bbundkant,?\s*forneden\b', 'bundkant', result, flags=re.IGNORECASE)
    # "forneden, bundkant" → just "bundkant"
    result = re.sub(r'\bforneden,?\s*(?:og\s+)?bundkant\b', 'bundkant', result, flags=re.IGNORECASE)

    # -- Fix misplaced color words in description --
    # "zipper-lukning brun" → "zipper-lukning"
    result = re.sub(
        r'(lukning|lynlås|knap)\s+(sort|hvid|brun|blå|grå|grøn|rød|beige|creme)\b(?!\s+(?:farve|tone|nuance))',
        r'\1', result, flags=re.IGNORECASE
    )

    # -- Fix "overkrop" used for skirts/bottoms → "talje" --
    if any(kw in text.lower() for kw in ('nederdel', 'skirt', 'bukse', 'shorts')):
        result = re.sub(r'\boverkrop\b', 'talje', result, flags=re.IGNORECASE)

    # -- Clean up double spaces and orphaned punctuation from all the replacements --
    result = re.sub(r'\s{2,}', ' ', result)
    result = re.sub(r'\s+\.', '.', result)
    result = re.sub(r'\.\s*\.', '.', result)
    result = re.sub(r'^\s*[,;.]\s*', '', result)
    result = result.strip()

    # If everything was removed, return empty string (fallback will handle it)
    return result


def _get_fallback_description(type_da: str, color: str) -> str:
    """Kort, ren editorial fallback — kun brugt når AI-beskrivelsen er for kort.
    Holdt bevidst kort (2 sætninger) for at undgå at generiske templates
    fylder produktsider med indhold der ikke matcher det specifikke produkt.
    """
    fallbacks = {
        "Skjorter": "Skjorte med knapper i front og spidskrave. Lange ærmer med manchetknapper og buet søm.",
        "Bukser": "Bukser med regular fit og mellemhøj talje. Lynlås og knaplukning med bæltestropper.",
        "T-Shirts": "T-shirt med afslappet pasform og rund hals. Korte ærmer i blød bomuldskvalitet.",
        "Strik": "Strik med rund hals og ribkant ved hals, ærmer og bund. Regular fit i mellemvægt kvalitet.",
        "Jakker": "Jakke med regular fit og fuld lukning foran. Krave og forede lommer.",
        "Blazere": "Blazer med reverskrave og knapper foran. Indvendige lommer og ren finish.",
        "Kjoler": "Kjole med regular fit. Længde til knæet med ren finish.",
        "Nederdele": "Nederdel med mellemhøj talje og lynlås i siden. Ren silhuet.",
        "Toppe": "Top med afslappet pasform og rund hals. Rene kantafslutninger.",
        "Bluser": "Bluse med lange ærmer og knaplukning. Let kvalitet med blød finish.",
        "Hoodies": "Hoodie med træksnor i hætten og kængurulomme foran. Ribstrik ved ærmer og bund.",
        "Sweatshirts": "Sweatshirt med rund hals og afslappet pasform. Ribstrik ved hals, ærmer og bund.",
        "Shorts": "Shorts med regular fit og mellemhøj talje. Lynlås og knaplukning med sidelommer.",
        "Poloer": "Polo med ribstrikket krave og to-knaps lukning. Korte ærmer med regular fit.",
        "Veste": "Vest med lukning foran. Regular fit med indvendige lommer.",
        "Sneakers": "Sneakers med snørebånd og gummisål. Polstret tunge og forstærket hælkappe.",
        "Sandaler": "Sandaler med justerbare remme og gummisål. Polstret fodseng.",
        "Støvler": "Støvler med forstærket hælkappe og gummisål. Polstret krave og indvendig foring.",
        "Loafers": "Loafers med slip-on design. Læderforing og polstret indlægssål.",
        "Sko": "Sko med snørebånd og ren finish. Polstret indlægssål og gummisål.",
        "Tasker": "Taske med justerbar rem og lynlåslukning. Indvendig lomme og forstærkede sømme.",
        "Rygsække": "Rygsæk med polstrede stropper og lynlås. Hovedrum med indvendig organisering.",
        "Tørklæder": "Tørklæde i blød kvalitet med rene kanter. Generøs størrelse.",
        "Bælter": "Bælte med spænde i metal og ren kant-finish. Justerbar pasform.",
        "Solbriller": "Solbriller med acetat-stel og tonede glas. Brede stænger og UV-beskyttelse.",
        "Hatte": "Hat med struktureret front og buet skygge. Justerbar lukning bagpå.",
        "Caps": "Cap med struktureret front og buet skygge. Justerbar lukning bagpå.",
        "Kasketter": "Kasket med struktureret front og buet skygge. Justerbar lukning bagpå.",
        "Huer": "Hue i blød strik med ribkant. Foldet kant og mellemvægt kvalitet.",
    }
    return fallbacks.get(type_da, "")

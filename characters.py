"""Personaggi a caso per la categoria "Characters" dei prompt pronti (8/10/2026, idea dell'utente: "uomo, donna o
inumano, primi piani o figura intera di persone a caso, coinvolto anche nel random").

Il dado della categoria non pesca da una lista fissa: mette insieme i pezzi di ready_prompts/characters.json (eta',
provenienza, corporatura, capelli, vestiti, espressione, posa, luogo, luce, obiettivo; per i non umani creatura,
dettaglio, luogo) nel formato della famiglia: prosa per photo, frase corta a virgole per sdxl, tag Danbooru per anime.
Migliaia di combinazioni senza modello di testo. Random AI riceve invece soggetto e inquadratura nel brief (brief()).

Regole fisse: sempre adulti (20 anni e oltre), mai persone reali o famose, varieta' di provenienza, eta' e corpo,
creature senza sangue.
"""
import json
import random
from pathlib import Path

DATI = Path(__file__).resolve().parent / "data" / "characters.json"
CHI = ("man", "woman", "nonhuman")
PESI_CHI = (0.4, 0.4, 0.2)
INQUADRATURE = ("closeup", "fullbody")
PRONOMI = {
    "man": {"he": "he", "his": "his", "him": "him"},
    "woman": {"he": "she", "his": "her", "him": "her"},
    "nonhuman": {"he": "it", "his": "its", "him": "it"},
}
# per Random AI: cosa deve essere il soggetto e come si inquadra
SOGGETTI_BRIEF = {
    "man": "one adult man (20 or older), an invented person, never a real or famous one",
    "woman": "one adult woman (20 or older), an invented person, never a real or famous one",
    "nonhuman": "one non-human character: a creature, monster, alien, robot, spirit or fantasy being (no blood or gore)",
}
INQUADRATURE_BRIEF = {
    "closeup": "close-up portrait, head and shoulders",
    "fullbody": "full body, the whole figure in frame from head to feet",
}


def _dati():
    return json.loads(DATI.read_text(encoding="utf-8"))


def _articolo(parola):
    p = parola.lower()
    if p[:1].isdigit():
        # "an 81-year-old", "an 18-year-old": si legge "eighty", "eighteen"
        return "an" if p.startswith("8") or p[:2] in ("11", "18") and not p[2:3].isdigit() else "a"
    if p[:1] in "aeiou" and not p.startswith(("uk", "ur", "eu", "uni", "use", "one")):
        return "an"
    return "a"


def _luogo(d, chiave, rng):
    """Un luogo [preposizione, luogo, "in"/"out"] e una luce adatta (niente pioggia dentro un caffe')."""
    prep, luogo, dove = rng.choice(d[chiave])
    return prep, luogo, rng.choice(d["luci"][dove])


def _maiuscola(frase):
    return frase[:1].upper() + frase[1:]


def scegli(chi="", inquadratura="", rng=random):
    """chi e inquadratura come scelti dall'utente, o a caso se vuoti / non validi."""
    chi = chi if chi in CHI else rng.choices(CHI, weights=PESI_CHI)[0]
    inquadratura = inquadratura if inquadratura in INQUADRATURE else rng.choice(INQUADRATURE)
    return chi, inquadratura


def brief(chi="", inquadratura="", rng=random):
    """Le righe del RANDOM BRIEF di Random AI quando la categoria e' Characters."""
    chi, inquadratura = scegli(chi, inquadratura, rng)
    return {"subject": SOGGETTI_BRIEF[chi], "framing": INQUADRATURE_BRIEF[inquadratura]}


def _eta(d, rng):
    da, a, _ = rng.choices(d["eta"], weights=[x[2] for x in d["eta"]])[0]
    anni = rng.randint(da, a)
    return anni, d["decenni"][str(anni // 10)]


def _vestito(d, chi, rng):
    return rng.choice(d["vestiti"]["any"] + d["vestiti"].get(chi, []))


def _foto_persona(d, chi, inquadratura, rng):
    pr = PRONOMI[chi]
    anni, decennio = _eta(d, rng)
    corpo, origine = rng.choice(d["corporature"]), rng.choice(d["origini"])
    soggetto = f"{_articolo(corpo)} {corpo} {origine} {chi} in {pr['his']} {decennio}"
    capelli, dettaglio = rng.choice(d["capelli"][chi]), rng.choice(d["dettagli"])
    vestito, espressione = _vestito(d, chi, rng), rng.choice(d["espressioni"])
    posa = rng.choice(d["pose"][inquadratura]).format(**pr)
    prep, luogo, luce = _luogo(d, "luoghi", rng)
    obiettivo = rng.choice(d["obiettivi"][inquadratura])
    lui = _maiuscola(pr["he"])
    if inquadratura == "closeup":
        return (f"A close-up portrait photograph of {soggetto}. {lui} has {capelli} and {dettaglio}, and wears {vestito}. "
                f"{lui} {espressione}, {posa}. Behind {pr['him']}, out of focus, {luogo}. {_maiuscola(luce)}. "
                f"{_maiuscola(obiettivo)}.")
    return (f"A full-body photograph of {soggetto}, {posa}, {prep} {luogo}. {lui} has {capelli} and {dettaglio}, and wears "
            f"{vestito}. {lui} {espressione}. {_maiuscola(luce)}. {_maiuscola(obiettivo)}, the whole figure in frame from "
            f"head to feet.")


def _foto_creatura(d, inquadratura, rng):
    pr = PRONOMI["nonhuman"]
    creatura = rng.choice(d["creature"])
    dettaglio = rng.choice(d["creature_dettagli"])["frase"]
    espressione = rng.choice(d["creature_espressioni"])
    posa = rng.choice(d["pose"]["closeup" if inquadratura == "closeup" else "creature_fullbody"]).format(**pr)
    prep, luogo, luce = _luogo(d, "creature_luoghi", rng)
    obiettivo = rng.choice(d["obiettivi"][inquadratura])
    if inquadratura == "closeup":
        return (f"A close-up portrait photograph of {creatura}. {dettaglio}. It {espressione}, {posa}. Behind it, out of "
                f"focus, {luogo}. {_maiuscola(luce)}. {_maiuscola(obiettivo)}.")
    return (f"A full-body photograph of {creatura}, {posa}, {prep} {luogo}. {dettaglio}. It {espressione}. "
            f"{_maiuscola(luce)}. {_maiuscola(obiettivo)}, the whole figure in frame from head to feet.")


def _sdxl(d, chi, inquadratura, rng):
    inquadra = "close-up portrait" if inquadratura == "closeup" else "full body shot"
    obiettivo = rng.choice(d["obiettivi_brevi"][inquadratura])
    _, luogo, luce = _luogo(d, "luoghi" if chi != "nonhuman" else "creature_luoghi", rng)
    if chi == "nonhuman":
        parti = [inquadra, f"photo of {rng.choice(d['creature'])}", rng.choice(d["creature_dettagli"])["breve"],
                 rng.choice(d["creature_espressioni_brevi"])]
    else:
        anni, _ = _eta(d, rng)
        origine = rng.choice(d["origini"])
        parti = [inquadra, f"photo of {_articolo(str(anni))} {anni}-year-old {origine} {chi}",
                 rng.choice(d["capelli"][chi]), rng.choice(d["dettagli"]), _vestito(d, chi, rng),
                 rng.choice(d["espressioni_brevi"])]
    if inquadratura == "fullbody":
        parti.append(rng.choice(d["pose"]["fullbody" if chi != "nonhuman" else "creature_fullbody"]))
    return ", ".join(parti + [luogo, luce, obiettivo])


def _anime(d, chi, inquadratura, rng):
    a = d["anime"]
    posa_tag, posa_frase = rng.choice(a["pose"][inquadratura])
    sfondo_tag, sfondo_frase = rng.choice(a["sfondi"])
    luce = rng.choice(a["luci"])
    if chi == "nonhuman":
        tag, frase = rng.choice(a["creature"])
        tag = [tag, rng.choice(a["espressioni"]), posa_tag, sfondo_tag, luce]
        soggetto = frase
    else:
        anni, _ = _eta(d, rng)
        conteggio = "1boy" if chi == "man" else "1girl"
        maturo = ("mature male" if chi == "man" else "mature female") if anni >= 30 else ""
        tag = [conteggio, "solo", maturo, rng.choice(a["capelli"][chi]), rng.choice(a["pelle"]), rng.choice(a["occhi"]),
               rng.choice(a["vestiti"]["any"] + a["vestiti"].get(chi, [])), rng.choice(a["espressioni"]), posa_tag,
               sfondo_tag, luce]
        persona = "man" if chi == "man" else "woman"
        soggetto = f"a young adult {persona}" if anni < 30 else f"{_articolo(str(anni))} {anni}-year-old {persona}"
    frase = f"{_maiuscola(soggetto)} {posa_frase} {sfondo_frase}."
    return ", ".join(t for t in tag if t) + ". " + frase


# luci in piu' per lo "Strange mix", quelle della prima versione che stavano bene solo in citta'
LUCI_STRANE = ["light rain and the glow of streetlights", "pink and blue neon glow", "a spinning disco ball"]


def mescolati(d):
    """Gli stessi pezzi senza la logica (casella "Strange mix", 8/10/2026: l'utente voleva poter riavere gli
    accostamenti della prima versione, come la pioggia dentro un caffe' o un elementale di fuoco che si allaccia le
    scarpe): luoghi di persone e creature insieme, qualunque luce in qualunque luogo, le creature con anche le pose delle
    persone, in anime posa dei tag e posa della frase pescate separatamente."""
    m = json.loads(json.dumps(d))
    luoghi = [[prep, luogo, "x"] for prep, luogo, _ in d["luoghi"] + d["creature_luoghi"]]
    m["luoghi"] = m["creature_luoghi"] = luoghi
    m["luci"] = {"x": d["luci"]["in"] + d["luci"]["out"] + LUCI_STRANE}
    m["pose"]["creature_fullbody"] = d["pose"]["creature_fullbody"] + d["pose"]["fullbody"]
    for inq, coppie in d["anime"]["pose"].items():
        m["anime"]["pose"][inq] = [[tag, frase] for tag, _ in coppie for _, frase in coppie]
    return m


def componi(famiglia, chi="", inquadratura="", rng=random, strano=False):
    """Un personaggio a caso nel formato della famiglia (photo, sdxl, anime). Torna {"testo", "chi", "inquadratura"}.
    strano: casella "Strange mix" (accostamenti senza logica, vedi mescolati)."""
    if famiglia not in ("photo", "sdxl", "anime"):
        raise ValueError(f"no random characters for the '{famiglia}' family")
    d = _dati()
    if strano:
        d = mescolati(d)
    chi, inquadratura = scegli(chi, inquadratura, rng)
    if famiglia == "photo":
        testo = _foto_creatura(d, inquadratura, rng) if chi == "nonhuman" else _foto_persona(d, chi, inquadratura, rng)
    elif famiglia == "sdxl":
        testo = _sdxl(d, chi, inquadratura, rng)
    else:
        testo = _anime(d, chi, inquadratura, rng)
    return {"testo": testo, "chi": chi, "inquadratura": inquadratura}

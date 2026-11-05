#!/usr/bin/env python3
"""Construit avomarks/data.enc.json pour l'espace client Molko Design.

Usage :
  python3 tools/build.py --export EXPORT_DIR --blobs BLOBS_DIR --code CODE --out avomarks/data.enc.json

EXPORT_DIR contient les sous-dossiers contenus/, taches/, documents/ (un JSON par document,
tels qu'exportés depuis l'appli de pilotage). BLOBS_DIR contient les fichiers des aperçus et
documents, nommés par leur identifiant d'asset (ex. 5e99...cef.jpg).
Les données sont chiffrées en AES-256-GCM avec une clé dérivée du code (PBKDF2-SHA256).
"""
import argparse, base64, glob, io, json, os, secrets, datetime
from zoneinfo import ZoneInfo
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

PARIS = ZoneInfo("Europe/Paris")
ITER = 250000
FLABEL = {"post": "Post", "carrousel": "Carrousel", "reel": "Reel", "story": "Story"}


def load(dirpath):
    out = {}
    for f in sorted(glob.glob(os.path.join(dirpath, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict) and "id" in d:
            d = d["data"]
        out[os.path.splitext(os.path.basename(f))[0]] = d
    return out


def blob_path(blobs, asset_id):
    if not asset_id:
        return None
    hits = glob.glob(os.path.join(blobs, asset_id + "*"))
    return hits[0] if hits else None


def data_uri(path, max_w=None):
    ext = os.path.splitext(path)[1].lower()
    if max_w and ext in (".jpg", ".jpeg", ".png", ".webp"):
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    mime = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "application/octet-stream")
    with open(path, "rb") as fh:
        return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode())


def when(c):
    if not c.get("date"):
        return None
    h = c.get("heure") or "23:59"
    return datetime.datetime.fromisoformat(c["date"] + "T" + h).replace(tzinfo=PARIS)


def effective(c, now):
    s = c.get("statut") or "a_creer"
    if s == "programme" and c.get("mode") != "manuel":
        w = when(c)
        if w and w < now:
            return "publie"
    return s


def build(export, blobs, now):
    contenus = load(os.path.join(export, "contenus"))
    taches = load(os.path.join(export, "taches"))
    documents = load(os.path.join(export, "documents"))
    for k, c in contenus.items():
        c["_id"] = k
        c["_eff"] = effective(c, now)

    month = now.strftime("%Y-%m")
    cal = next((d for d in documents.values() if d.get("type") == "calendrier" and d.get("mois") == month), None)
    if cal is None:
        cals = sorted([d for d in documents.values() if d.get("type") == "calendrier"], key=lambda d: d.get("mois", ""))
        cal = cals[-1] if cals else None
        if cal:
            month = cal["mois"]

    semaines, counts = [], {"publie": 0, "programme": 0, "decale": 0, "preparation": 0}
    for w in (cal or {}).get("semaines", []):
        items = []
        for it in w.get("items", []):
            cs = [contenus[i] for i in it.get("ids", []) if i in contenus]
            dec = next((c.get("decale") for c in cs if c.get("decale") and c["_eff"] != "publie"), None)
            if cs and all(c["_eff"] == "publie" for c in cs):
                st, info = "publie", None
            elif dec:
                st, info = "decale", {"au": dec.get("a"), "motif": dec.get("motif", "")}
            elif cs and all(c["_eff"] in ("programme", "publie") for c in cs):
                st, info = "programme", None
            else:
                st, info = "preparation", None
            dates = sorted([c["date"] for c in cs if c.get("date")])
            items.append({"titre": it["titre"], "format": it.get("format", ""), "statut": st, "decale": info,
                          "date": dates[0] if dates else None, "bonus": bool(it.get("bonus"))})
            if not it.get("bonus"):
                counts[st] += 1
        semaines.append({"nom": w["nom"], "dates": w["dates"], "fin": w.get("fin"), "items": items})

    in_month = [c for c in contenus.values() if (c.get("date") or "").startswith(month)]
    volume = {
        "feed": sum(1 for c in in_month if c.get("format") != "story" and c["_eff"] == "publie"),
        "stories": sum(1 for c in in_month if c.get("format") == "story" and c["_eff"] == "publie"),
        "feedPrevu": sum(1 for c in in_month if c.get("format") != "story"),
        "devis": "8 à 12 contenus par mois",
    }

    today = now.date().isoformat()
    horizon = (now.date() + datetime.timedelta(days=10)).isoformat()
    prochaines = []
    for c in sorted(contenus.values(), key=lambda c: (c.get("date") or "9999") + (c.get("heure") or "99")):
        d = c.get("date") or ""
        if today <= d <= horizon and c["_eff"] != "publie":
            p = blob_path(blobs, c.get("apercuId"))
            prochaines.append({"titre": c.get("titre", ""), "format": FLABEL.get(c.get("format"), ""), "date": d,
                               "heure": c.get("heure") or "", "statut": "programme" if c["_eff"] == "programme" else "preparation",
                               "image": data_uri(p, 360) if p else None})

    feed = []
    for c in sorted(contenus.values(), key=lambda c: (c.get("date") or "") + (c.get("heure") or ""), reverse=True):
        if c.get("format") == "story" or c["_eff"] not in ("publie", "programme", "pret"):
            continue
        p = blob_path(blobs, c.get("apercuId"))
        if not p:
            continue
        feed.append({"format": c.get("format"), "date": c.get("date"), "publie": c["_eff"] == "publie", "image": data_uri(p, 420)})

    attentes = [{"texte": t["texte"], "detail": t.get("detail", ""), "echeance": t.get("echeance", "")}
                for t in sorted(taches.values(), key=lambda t: (t.get("echeance") or "9999", t.get("cree", 0)))
                if t.get("categorie") == "client" and not t.get("fait") and not t.get("prive")]

    docs = []
    if cal:
        img, pdf = blob_path(blobs, cal.get("imageId")), blob_path(blobs, cal.get("pdfId"))
        docs.append({"titre": "Calendrier éditorial", "sousTitre": cal.get("sousTitre", ""),
                     "image": data_uri(img) if img else None, "pdf": data_uri(pdf) if pdf else None,
                     "fichier": "Avomarks_Calendrier_%s.pdf" % month})

    return {"client": "Avomarks", "mois": month, "maj": now.isoformat(timespec="minutes"),
            "compteur": counts, "semaines": semaines, "volume": volume, "prochaines": prochaines[:8],
            "feed": feed[:12], "attentes": attentes, "documents": docs}


def encrypt(payload, code):
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(code.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b(salt), "iv": b(iv), "data": b(ct)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--blobs", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--now", help="ISO datetime (tests)")
    a = ap.parse_args()
    now = datetime.datetime.fromisoformat(a.now).replace(tzinfo=PARIS) if a.now else datetime.datetime.now(PARIS)
    payload = build(a.export, a.blobs, now)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(encrypt(payload, a.code), fh)
    c = payload["compteur"]
    print("OK", payload["mois"], "publiés", c["publie"], "programmés", c["programme"], "décalés", c["decale"],
          "en préparation", c["preparation"], "| feed", len(payload["feed"]), "| prochaines", len(payload["prochaines"]),
          "| taille", os.path.getsize(a.out) // 1024, "Ko")

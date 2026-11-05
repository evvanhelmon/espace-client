# Espace client Molko Design

Pages de suivi clients, hébergées sur GitHub Pages (app-molkodesign.ch).

- `avomarks/index.html` : page de suivi Avomarks (protégée par code d'accès).
- `avomarks/data.enc.json` : données chiffrées (AES-256-GCM, clé dérivée du code d'accès). Illisibles sans le code.
- `tools/build.py` : génère `data.enc.json` à partir de l'export de l'appli de pilotage.

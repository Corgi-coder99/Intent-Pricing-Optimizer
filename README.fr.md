# Optimiseur dynamique de prix et de conversion piloté par l'intention

[English](README.md)

Un projet de bout en bout **IA + Data Science + E-commerce** : les signaux issus des avis clients (NLP/RAG) nourrissent un modèle d'élasticité-prix
(LightGBM), qui pilote un agent de prix recommandant un prix et une stratégie. Le projet comprend un tableau de bord BI bilingue (anglais/français),
une API REST, un pipeline de données bronze/silver/gold et des données de marché réelles UE/Royaume-Uni (prix, taux de change, marges sectorielles).

> **État réel :** le modèle de conversion est entraîné sur des sessions **simulées**. Sa sensibilité globale au prix a été comparée à de vraies
> transactions (voir `docs/REAL_DATA_VALIDATION.md`). La lecture des avis (niveau 1) n'est **pas encore** validée sur de vrais avis. Les marges
> de la comparaison entre pays sont un **scénario hypothétique**, pas le bénéfice réel d'une entreprise.

## Démarrage rapide
```bash
pip install -r requirements.txt
python -m src.train_pipeline                 # simule les données -> bronze/silver/gold -> entraîne le modèle
python -m pytest -q tests                    # plus de 160 tests
python -m streamlit run app/Dashboard.py # tableau de bord (sélecteur English/Français dans la barre latérale)
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000   # API REST, documentation sur /docs
```
Le tableau de bord n'écoute que sur `127.0.0.1` (`.streamlit/config.toml`) ; ouvrez `http://127.0.0.1:8501`.
Par défaut, Streamlit écoute sur toutes les interfaces réseau et ce tableau de bord n'a pas d'authentification : ne modifiez pas ce réglage.

## Données réelles UE/Royaume-Uni (facultatif)
```bash
python -m src.connectors.openprices --mode by-location --countries GB FR DE IE ES IT NL BE --per-country 1500
python -m src.connectors.ecb_fx
python -m src.pipeline run
```
Ouvrez ensuite la page « Comparaison par pays » du tableau de bord. Sources, licences et limites : `docs/DATA_SOURCES.md` (en anglais).
Données des contributeurs Open Food Facts, disponibles sous licence ODbL.

## API REST
Points d'accès : `GET /health`, `POST /v1/review/analyze`, `POST /v1/price/simulate`, `POST /v1/price/recommend` (champ facultatif `"lang": "en" | "fr"`),
`POST /v1/admin/reload` (administrateur). Créez une clé par utilisateur : `python -m src.auth create --name alice --role user` ; envoyez-la dans l'en-tête `X-API-Key`.

## Documentation (en anglais)
`docs/DATA_PIPELINE.md` (pipeline, contrats, dédoublonnage, délais d'expiration, rétention), `docs/SECURITY.md` (sécurité, usage multi-utilisateur, risques restants),
`docs/DATA_SOURCES.md` (sources, licences, niveau de confiance), `docs/REAL_DATA_VALIDATION.md` (élasticité-prix : simulation et transactions réelles).

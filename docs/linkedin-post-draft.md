# LinkedIn draft (NOT published, waiting for the owner's approval)

Format: one post with the **video** (LinkedIn allows either a video or an image carousel in one post, not both). Put the images in a second post or
as a document/carousel later. Put the GitHub link in the **first comment** (posts with links in the body usually get less reach).

## English (about 150 words)

I built an AI pricing system for EU/UK e-commerce, and spent as much time trying to break it as building it.

What it does: reads customer reviews, estimates how demand reacts to price, and recommends a price and a strategy. English/French dashboard, REST API, a bronze/silver/gold data pipeline, and real market data from 8 countries (Open Prices, ECB, Eurostat).

The part I'm proudest of is the checking. Its price sensitivity was compared with real transactions (-2.27 real vs -2.44 simulated). An independent code review found 10 defects, and browser testing found a page crash I had missed. All fixed, all covered by tests (195).

The honest limits: the conversion model learns from simulated sessions; the review reader is a small keyword model not yet validated on real reviews; the margins across countries are a labelled hypothetical scenario, because real branch costs are not public.

Code in the first comment.

#DataScience #Ecommerce #Pricing #Python #Portfolio

## Français (environ 150 mots)

J'ai construit un système de prix par IA pour l'e-commerce UE/Royaume-Uni, et j'ai passé autant de temps à essayer de le casser qu'à le construire.

Ce qu'il fait : il lit les avis clients, estime la réaction de la demande au prix, puis recommande un prix et une stratégie. Tableau de bord anglais/français, API REST, pipeline de données bronze/silver/gold et données de marché réelles de 8 pays (Open Prices, BCE, Eurostat).

La partie dont je suis le plus fier, c'est la vérification. Sa sensibilité au prix a été comparée à de vraies transactions (-2,27 en réel contre -2,44 en simulation). Une revue de code indépendante a trouvé 10 défauts, et les tests navigateur ont révélé un plantage de page que j'avais manqué. Tout est corrigé et couvert par des tests (195).

Les limites, honnêtement : le modèle de conversion apprend sur des sessions simulées ; le lecteur d'avis est un petit modèle à mots-clés pas encore validé sur de vrais avis ; les marges entre pays sont un scénario hypothétique clairement indiqué, car les vrais coûts des filiales ne sont pas publics.

Le code est dans le premier commentaire.

#DataScience #Ecommerce #Pricing #Python #Portfolio

## First comment (English / Français)

Code and documentation: https://github.com/Corgi-coder99/Intent-Pricing-Optimizer  (add after the repository is public)
Data: Open Food Facts contributors (ODbL), ECB, Eurostat.

## Media

| File | Use |
|---|---|
| `docs/media/demo.mp4` | the video of the post (about 35 s, no sound, captions burned in) |
| `docs/media/01-dashboard-en.png` | carousel image 1 (English interface) |
| `docs/media/02-dashboard-fr.png` | carousel image 2 (French interface) |
| `docs/media/03-country-comparison.png` | carousel image 3 |
| `docs/pipeline-diagram.png` | carousel image 4 |

Alt text (accessibility): "Dashboard of an AI pricing system: KPI cards, price elasticity curve and a map of France" (EN) / "Tableau de bord d'un système de prix par IA : indicateurs, courbe d'élasticité-prix et carte de France" (FR).

## Checks before posting
- Every number in the text appears in the repository docs (`docs/REAL_DATA_VALIDATION.md`); re-read them once.
- The repository is public and its README opens correctly; no personal data in the video (the dashboard shows simulated data only).
- Say that AI tools wrote much of the code if asked: it is stated in the portfolio ("With AI, openly").

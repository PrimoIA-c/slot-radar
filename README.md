# Slot Radar

Radar de sorties de slots. Collecte quotidienne silencieuse, récapitulatif Telegram tous les lundis à 9h, historique Excel cumulatif avec un onglet par provider.

[![Slot data: slot.report](https://img.shields.io/badge/slot_data-slot.report-1565D8)](https://slot.report/)

Données de slots fournies par [slot.report](https://slot.report/).

---

## Ce que ça fait

Tous les jours à 9h (heure de Paris), un job GitHub Actions interroge l'API slot.report, compare les sorties aux entrées déjà connues, et enregistre les nouveautés. Le lundi uniquement, il envoie sur Telegram la liste de tout ce qui s'est accumulé depuis le dernier envoi, avec le classeur Excel à jour en pièce jointe.

```
Sorties slots — semaine du 10/08/2026
3 nouvelles sorties

Booming Games
  06/08/2026 — Mr. Oinkster's Hold & Win

NetEnt
  ~04/08/2026 — Dead Or Alive 3

Pragmatic Play
  03/08/2026 — Sweet Bonanza 1000

~ date de détection, non publiée par le provider
```

### Pourquoi collecter tous les jours si la notification est hebdomadaire

Trois raisons, toutes concrètes :

1. **L'endpoint `/new.json` ne renvoie que les 30 sorties les plus récentes.** Sur une semaine chargée (salon iGaming, fin de trimestre), le volume peut frôler ou dépasser 30 — un passage unique le lundi perdrait silencieusement les entrées sorties de la fenêtre. Un passage quotidien rend ça impossible.
2. **GitHub désactive les workflows planifiés après 60 jours d'inactivité du dépôt.** Les commits quotidiens maintiennent le dépôt vivant sans intervention.
3. **La donnée est déjà là si tu changes d'avis.** Passer en notification quotidienne, c'est une variable à changer, sans perte d'historique.

---

## Installation

### 1. Créer le bot Telegram

Ouvre [@BotFather](https://t.me/BotFather), envoie `/newbot`, suis les instructions. Tu récupères un token de la forme `123456789:AAxxxxx...`.

Envoie ensuite **un message quelconque à ton bot** (indispensable : un bot ne peut pas écrire le premier), puis ouvre dans ton navigateur :

```
https://api.telegram.org/bot<TON_TOKEN>/getUpdates
```

Ton `chat_id` se trouve dans `result[0].message.chat.id`.

### 2. Créer le dépôt

```bash
git init
git add .
git commit -m "init: slot radar"
git remote add origin git@github.com:<toi>/slot-radar.git
git push -u origin main
```

### 3. Déclarer les secrets

Dans `Settings → Secrets and variables → Actions → New repository secret` :

| Secret | Obligatoire | Valeur |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | oui | Le token BotFather |
| `TELEGRAM_CHAT_ID` | oui | Ton identifiant de conversation |
| `SLOT_REPORT_API_KEY` | après 24h | Voir la section suivante |

### 4. Régler l'accès à l'API

L'API slot.report est gratuite mais conditionnée. Depuis le 30 juin 2026, elle exige un lien dofollow actif vers slot.report, vérifié automatiquement par domaine, ou une clé gratuite liée au domaine pour les applications serveur — avec un essai de 24 heures sans lien pour tester d'abord. Le lien est revérifié quotidiennement : s'il est retiré ou passé en nofollow, l'accès se met en pause.

**C'est la cause classique d'un `403` sur cette API.**

Deux options :

- **Dépôt public** — le badge en tête de ce README fait office de lien dofollow. Rends le dépôt public et demande ta clé sur [slot.report/api](https://slot.report/api/).
- **Dépôt privé** — colle le lien sur n'importe quel domaine que tu possèdes déjà, puis demande la clé.

Dans les deux cas, tu peux lancer le bootstrap immédiatement grâce à l'essai de 24h, et ajouter la clé ensuite.

> Le nom exact de l'en-tête d'authentification est à confirmer au moment où tu génères la clé. Le client envoie `X-API-Key`, `Authorization: Bearer` et un paramètre `?key=` en parallèle — un en-tête non reconnu est ignoré sans effet. Si ça coince, `src/sources.py`, fonction `_headers()`.

### 5. Bootstrap

`Actions → Radar sorties slots → Run workflow`, sans cocher aucune option.

Ce premier run importe le catalogue complet (environ 6 000 slots), remplit l'Excel avec tout l'historique disponible et **marque tout comme déjà notifié** — pour que le premier lundi n'envoie pas 6 000 lignes. Seules les sorties postérieures au bootstrap seront annoncées.

Si tu préfères un Excel qui démarre vide, mets le secret de variable `BOOTSTRAP_BACKFILL` à `false`, ou lance en local avec `--no-backfill`.

C'est tout. Le cron prend le relais.

---

## Utilisation en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # puis remplis-le
set -a && source .env && set +a

python -m src.main --dry-run --force   # simulation, aucune écriture
python -m src.main --notify --force    # envoi immédiat du récap
```

| Option | Effet |
|---|---|
| `--dry-run` | N'écrit rien, n'envoie rien, affiche un aperçu du message |
| `--notify` | Force l'envoi même hors du jour configuré |
| `--force` | Ignore la garde « déjà exécuté aujourd'hui » |
| `--no-backfill` | Au bootstrap, n'importe pas l'historique dans l'Excel |

---

## Configuration

Toutes les variables sont optionnelles sauf les deux secrets Telegram.

| Variable | Défaut | Rôle |
|---|---|---|
| `TIMEZONE` | `Europe/Paris` | Fuseau de référence pour toute la logique calendaire |
| `NOTIFY_WEEKDAY` | `0` | Jour d'envoi. `0` = lundi, `6` = dimanche |
| `SEND_EXCEL` | `true` | Joindre le classeur au récapitulatif |
| `BOOTSTRAP_BACKFILL` | `true` | Importer l'historique dans l'Excel au premier run |
| `EXCLUDED_PROVIDERS` | vide | Slugs à ignorer, séparés par des virgules |

Pour passer en notification quotidienne, retire simplement la condition : mets `NOTIFY_WEEKDAY` à la valeur du jour courant n'a pas de sens — modifie plutôt `should_notify` dans `src/main.py` en `True`.

---

## Architecture

```
src/
  config.py     Variables d'environnement, chemins, fuseau
  sources.py    Client API slot.report, normalisation, retries
  state.py      Persistance et dédoublonnage
  excel.py      Génération du classeur
  notifier.py   Client Telegram, découpage des messages
  main.py       Orchestration
data/
  state.json           Source de vérité, versionné
  sorties_slots.xlsx   Artefact généré, versionné
```

### Trois décisions qui structurent le projet

**`state.json` est la seule source de vérité.** L'Excel est reconstruit intégralement à chaque run. Aucune écriture incrémentale, donc aucune dérive possible entre les deux. Si tu supprimes le classeur, il se régénère à l'identique au run suivant.

**La nouveauté se définit par l'absence dans le state, jamais par la date.** La couverture du champ `release_date` est partielle (~84 %) et une slot peut entrer dans la base plusieurs jours après son lancement. Filtrer sur une fenêtre de dates ferait passer ces entrées à la trappe définitivement. Une date manquante est remplacée par la date de détection et signalée par un `~` dans le message.

**Le dédoublonnage est strict, jamais flou.** La clé est `provider_slug::slug`. Aucun rapprochement approximatif : *Sweet Bonanza* et *Sweet Bonanza 1000* sont deux jeux distincts, une comparaison par similarité les fusionnerait.

### Conséquence utile

Les entrées portent un drapeau `notified`. Un envoi Telegram raté ne perd rien : les entrées restent en attente et partent au run suivant, en plus des nouveautés du jour. Le marquage n'intervient qu'après un envoi intégralement réussi.

---

## Dépannage

| Symptôme | Cause probable |
|---|---|
| `403 refuse par slot.report` | Lien dofollow absent, en nofollow, ou essai de 24h expiré |
| `chat not found` | Tu n'as pas encore écrit au bot, ou le `chat_id` est erroné |
| Le workflow ne se déclenche plus | 60 jours sans activité sur le dépôt : relance-le manuellement |
| Rien n'est envoyé le lundi | Normal s'il n'y a aucune nouveauté — le bot reste silencieux |
| `state.json est corrompu` | Restaure-le via `git checkout <commit> -- data/state.json`. **Ne le supprime pas** : un state absent relance un bootstrap qui marquerait tout comme déjà notifié |

Les logs complets de chaque run sont dans l'onglet `Actions` du dépôt.

---

## Coût

Zéro. GitHub Actions est gratuit en dépôt public et couvre largement ce besoin en privé (~2 min/mois sur un quota de 2 000). L'API slot.report et l'API Telegram sont gratuites. La persistance, c'est le dépôt git.

# Guide et scripts minutés de démonstration

Ce document décrit le déroulement minuté des démonstrations pour le jury et les évaluateurs du hackathon.
La démonstration repose sur un facteur d'accélération d'horloge standard de **$\times 20$** (1 minute réelle = 20 minutes simulées).

---

## 1. Vue d'ensemble des passages de démo

| Passage | Scénario associé | Objectif opérationnel démontré | Twist / Événement tactique |
|---|---|---|---|
| **Passage A** | [demo_passage_a.json](file:///C:/Users/libra/corridor/backend/scenarios/demo_passage_a.json) | Contournement d'un brouilleur ami actif + adaptabilité en temps réel | Apparition d'une nouvelle menace coupant la trajectoire de repli en plein vol |
| **Passage B** | [demo_passage_b.json](file:///C:/Users/libra/corridor/backend/scenarios/demo_passage_b.json) | Déconfliction 4D et gain d'énergie via synchronisation de coupure EW | Traversée directe pendant la fenêtre de coupure et réactivation automatique de la bulle EW |

---

## 2. Passage A : Contournement tactique et nouvelle menace en vol

### Contexte tactique
- **Mission** : `M1` (Pilote `PILOT-1`, drone `UAV-1`) doit rallier un objectif situé à l'est de Munich.
- **Obstacle EW** : Le brouilleur ami `J3` est allumé en continu (aucune coupure programmée). La trajectoire directe est bloquée par le brouillage.
- **Trajectoire initiale** : Le planificateur calcule automatiquement la route sécurisée de contournement (**baseline / safe**) passant par le corridor Sud entre `J3` et le brouilleur ennemi `E1` ($646\text{ s}$ de vol, $36\%$ de batterie consommée).

### Chronogramme minuté (Durée totale : ~2 min)

| Temps réel ($T$) | Temps simulé ($t$) | Rôle / Écran | Actions du démonstrateur | Ce que voit l'auditoire | Points d'insistance jury |
|---|---|---|---|---|---|
| **00:00** | $000\text{ s}$<br>14:00:00 | **Commandement** (`CMD`) | 1. Démarrer le serveur avec `demo_passage_a.json`.<br>2. Se connecter sous le profil `CMD`. | Carte tactique complète : `J3` (rouge, ami au centre), `R1` (radar ennemi au nord), `E1` (brouilleur ennemi au sud). | "Le système offre une situation tactique unifiée intégrant pour la première fois l'empreinte électromagnétique réelle des brouilleurs." |
| **00:20** | $300\text{ s}$<br>14:05:00 | **Pilote** (`PILOT-1`) | 1. Basculer sur le rôle `PILOT-1`.<br>2. Ouvrir le détail de la mission `M1`. | Tracé orange du contournement par le Sud. La liaison radio avec le pilote est garantie sur tout le trajet. | "En l'absence de coordination EW, le pilote est contraint à un long détour de 9.7 km qui consomme plus du tiers de sa batterie." |
| **00:40** | $600\text{ s}$<br>14:10:00 | **Pilote** (`PILOT-1`) | 1. Démarrer l'horloge ($\times 20$).<br>2. Cliquer sur **Launch** pour lancer la mission. | Le drone `UAV-1` décolle et s'engage le long du corridor Sud. | "Le vol est suivi en temps réel avec le calcul dynamique du rapport signal/brouillage (J/S)." |
| **01:00** | $700\text{ s}$<br>14:11:40 | **Commandement** (`CMD`) | 1. Basculer sur `CMD`.<br>2. Ajouter une nouvelle menace ennemie sur le corridor Sud :<br>- Type : `Radar`<br>- Pos : `48.12500, 11.54500`<br>- Rayon : `1500 m` | La nouvelle zone rouge apparaît immédiatement sur la carte, en plein sur la route du drone en vol. | "La guerre électronique et le champ de bataille sont dynamiques : une menace imprévue surgit sur l'itinéraire de repli." |
| **01:15** | $760\text{ s}$<br>14:12:40 | **Pilote** (`PILOT-1`) | 1. Observer la réaction automatique du drone. | Le drone interrompt sa progression, dégage vers la cellule libre la plus proche (`escape_cell`) et Corridor recalcule un nouvel itinéraire de contournement sécurisé. | "Règle de sécurité stricte : aucun drone n'est abandonné au brouillage. La replanification en vol préserve l'aéronef sans intervention manuelle complexe." |
| **01:45** | $1200\text{ s}$<br>14:20:00 | **Pilote** & **CMD** | Laisser le drone poursuivre sa nouvelle trajectoire d'évitement. | Le drone contourne la nouvelle zone et poursuit sa mission vers l'objectif. | "Résilience démontrée face aux aléas tactiques." |

#### Coordonnées de la menace pour le Passage A
- **Latitude** : `48.12500`
- **Longitude** : `11.54500`
- **Rayon** : `1500` mètres (Type : Radar)

---

## 3. Passage B : Déconfliction 4D et franchissement synchronisé

### Contexte tactique
- **Mission** : Même objectif pour `M1`, mais une coupure planifiée de 10 minutes ($600\text{ s}$) a été programmée sur le brouilleur ami `J3` (de 14:15:00 à 14:25:00, soit $t = 900\text{ s}$ à $1500\text{ s}$).
- **Enjeu** : Permettre au drone de traverser en ligne droite la zone habituellement interdite, en synchronisant son passage avec l'extinction du brouilleur.

### Chronogramme minuté (Durée totale : ~2 min 15)

| Temps réel ($T$) | Temps simulé ($t$) | Rôle / Écran | Actions du démonstrateur | Ce que voit l'auditoire | Points d'insistance jury |
|---|---|---|---|---|---|
| **00:00** | $000\text{ s}$<br>14:00:00 | **Pilote** (`PILOT-1`) | 1. Démarrer le serveur avec `demo_passage_b.json`.<br>2. Se connecter sous `PILOT-1`.<br>3. Ouvrir la mission `M1`. | Le panneau affiche deux choix d'itinéraires :<br>1. **Safe (Détour)** : $646\text{ s}$, $36\%$ batterie.<br>2. **Fastest / One cut (Coupure J3)** : $308\text{ s}$, $17\%$ batterie. | "Corridor identifie l'opportunité dans le calendrier EW ami : gain direct de 52% de temps de vol et 19% d'autonomie batterie préservée." |
| **00:25** | $300\text{ s}$<br>14:05:00 | **Pilote** (`PILOT-1`) | Cliquer sur le choix **Fastest (Coupure J3)**. | La demande passe en statut `pending` (en attente d'approbation commandement/EW). | "Le système ne coupe jamais un brouilleur tout seul. La décision finale reste humaine (*human-in-the-loop*)." |
| **00:40** | $450\text{ s}$<br>14:07:30 | **Commandement** (`CMD`) | 1. Basculer sur `CMD`.<br>2. Cliquer sur la notification de demande de route pour approuver (`Approuver / Confirmer`). | La route directe devient verte/validée (`accepted`). Les deux rôles sont notifiés de la confirmation. | "La boucle de coordination inter-armes est bouclée en quelques secondes sans passer par des échanges radio flous." |
| **01:00** | $600\text{ s}$<br>14:10:00 | **Pilote** (`PILOT-1`) | 1. Lancer l'horloge ($\times 20$).<br>2. Cliquer sur **Launch** pour lancer la mission. | Le drone décolle et vole directement vers le centre de la zone de `J3`. | "La trajectoire est calculée pour arriver précisément dans la fenêtre de sécurité : entrée après $t = 930\text{ s}$ (+30s de marge d'extinction)." |
| **01:15** | $900\text{ s}$<br>14:15:00 | **Tous rôles** | Observer la carte au moment de la coupure. | **La bulle rouge de J3 s'éteint** (devient verte/inactive sur la carte). Le drone traverse le couloir libéré. | "Pendant la coupure, la liaison radio C2 et le flux vidéo du drone restent intègres." |
| **01:35** | $1208\text{ s}$<br>14:20:08 | **Pilote** (`PILOT-1`) | Observer l'arrivée sur l'objectif. | `M1` passe en statut `arrived`. Batterie restante : $83\%$ (contre $64\%$ avec le détour). | "Le drone a franchi la ligne de front et atteint sa cible deux fois plus vite, avec un potentiel d'action intact." |
| **01:50** | $1500\text{ s}$<br>14:25:00 | **Tous rôles** | Observer la fin de la fenêtre de coupure. | **J3 se rallume automatiquement** (`jammer.restarted`). La bulle de protection redevient active (rouge). Notification envoyée à l'opérateur EW. | "Zéro oubli : la bulle de défense sol-air de l'unité amie est restaurée automatiquement dès la fin du créneau." |

---

## 4. Commandes d'exécution rapide pour le démonstrateur

### Lancer le serveur backend

```bash
# Pour le Passage A
cd backend
uv run corridor-server scenarios/demo_passage_a.json

# Pour le Passage B
cd backend
uv run corridor-server scenarios/demo_passage_b.json
```

### Lancer l'interface frontend

```bash
cd frontend
npm run dev
```

Ouvrir le navigateur sur `http://localhost:5173`.

### Injection manuelle de la nouvelle menace (Passage A) en cas de secours
Si l'interface de saisie graphique n'est pas utilisée, la commande WebSocket peut être envoyée directement depuis la console dev du navigateur :

```javascript
// Injection de la menace R2 bloquant le corridor Sud
window.__corridor_send?.({
  type: "threat.add",
  kind: "radar",
  pos: { lat: 48.12500, lon: 11.54500 },
  radius_m: 1500
});
```

---

## 5. Synthèse des métriques comparatives à projeter

| Métrique | Passage A (Contournement de repli) | Passage B (Franchissement synchronisé) | Gain opérationnel |
|---|---|---|---|
| **Distance totale parcourue** | $9.7\text{ km}$ | $5.4\text{ km}$ | **$-44\%$ de distance** |
| **Durée du vol** | $646\text{ s}$ ($10\text{ min }46\text{ s}$) | $308\text{ s}$ ($5\text{ min }08\text{ s}$) | **Temps de vol divisé par 2** |
| **Consommation batterie** | $36\%$ | $17\%$ | **$+19\%$ de réserve utile préservée** |
| **Exposition aux menaces sol** | Prolongée ($10\text{ min}$) | Réduite ($5\text{ min}$) | **Vulnérabilité cinétique minimisée** |
| **Garantie de protection EW amie** | Brouilleur actif en continu | Coupure stricte de $10\text{ min}$ avec rallumage programmé | **Fratricide électromagnétique évité** |

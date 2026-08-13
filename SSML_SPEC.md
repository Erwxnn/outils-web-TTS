# Spec SSML du moteur — a donner au generateur (ChatGPT)

Ce fichier decrit **exactement** ce que le moteur TTS de ce projet sait interpreter.
Il est concu pour etre colle tel quel dans les instructions du generateur de script SSML.

Le moteur n'est **pas** un moteur SSML standard : il s'appuie sur `edge-tts`, qui n'a pas
de moteur SSML natif. Un petit parseur maison (`services/tts_studio.py`) lit le balisage,
le decoupe en segments, et envoie chaque segment separement au service Edge.
Consequence : **seules les trois balises ci-dessous ont un effet**, tout le reste est ignore.

---

## 1. Balises supportees

### `<break time="..."/>` — silence exact

Insere un silence reel de la duree demandee.

- Unites acceptees : millisecondes (`200ms`) ou secondes (`1s`, `1.5s`).
- Une duree de zero est ignoree.

```xml
Bleed.<break time="700ms"/>Bled.
```

### `<prosody rate="...">...</prosody>` — vitesse locale

Ajuste la vitesse de lecture du contenu, **relativement** au reglage de vitesse choisi
dans l'interface (les deux se cumulent).

- Mots-cles : `x-slow` (-50), `slow` (-25), `medium` (0), `fast` (+25), `x-fast` (+50).
- Pourcentages : `+20%` / `-10%` (delta), ou `80%` (= 80 % de la vitesse normale, soit -20).
- **Seul l'attribut `rate` est lu.** `pitch` et `volume` sur `<prosody>` sont ignores.

```xml
<prosody rate="slow">Be. Was. Were. Been.</prosody>
```

### `<voice name="...">...</voice>` — changement de voix

Fait lire ce segment par une **autre voix Edge** que celle selectionnee dans l'interface.
C'est le mecanisme a utiliser pour qu'un mot francais ne soit pas lu avec la phonetique
de la voix anglaise.

```xml
<voice name="fr-FR-HenriNeural">Saigner.</voice>
```

Apres la balise fermante, la lecture revient automatiquement a la voix de base.

Les balises se combinent, dans n'importe quel ordre d'imbrication :

```xml
<voice name="fr-FR-HenriNeural"><prosody rate="slow">Obtenir, avoir, se procurer.</prosody></voice>
```

---

## 2. Comment nommer une voix

L'attribut `name` doit contenir le **ShortName** exact de la voix Edge, **sensible a la casse**.
Le format est `<locale>-<Prenom>Neural`, par exemple `fr-FR-HenriNeural`.

Un `name` inconnu ou mal orthographie fait **echouer la generation**. Il ne s'agit donc pas
d'un nom libre : il doit provenir de la liste officielle.

Le moteur verifie chaque nom **avant** le premier appel reseau et s'arrete en nommant la voix
fautive, avec une suggestion de correction quand un nom proche existe. Rien n'est genere dans
ce cas : il n'y a donc pas de risque de fichier audio tronque, mais le lot entier est perdu.

### Obtenir la liste exacte

```bash
python tools/list_voices.py fr en                    # affiche les voix fr* et en*
python tools/list_voices.py fr en -o voices.md       # ecrit un tableau Markdown
```

Les ShortNames sont aussi visibles directement dans le selecteur de voix de l'interface
(en fin de libelle), et le champ de recherche du selecteur accepte le ShortName.

### Voix couramment disponibles

A confirmer avec le script ci-dessus — Microsoft fait evoluer le catalogue.

| Langue | ShortName | Genre |
| --- | --- | --- |
| Francais (France) | `fr-FR-DeniseNeural` | Femme |
| Francais (France) | `fr-FR-HenriNeural` | Homme |
| Francais (France) | `fr-FR-EloiseNeural` | Femme |
| Anglais (US) | `en-US-AriaNeural` | Femme |
| Anglais (US) | `en-US-GuyNeural` | Homme |
| Anglais (US) | `en-US-JennyNeural` | Femme |
| Anglais (GB) | `en-GB-SoniaNeural` | Femme |
| Anglais (GB) | `en-GB-RyanNeural` | Homme |

### Voix multilingues

Les voix dont le nom contient `Multilingual` (par exemple `en-US-AndrewMultilingualNeural`)
savent parler plusieurs langues avec la meme identite vocale. Elles sont valides partout ou
un `ShortName` est attendu, y compris comme voix de base.

**Il ne faut pourtant pas compter sur leur detection automatique de langue dans ce moteur.**
Chaque segment part dans un appel reseau distinct qui ne contient que son propre texte : le
modele recoit donc `Saigner.` isole, sans aucun contexte, dans un document que `edge-tts`
declare toujours en `xml:lang='en-US'`. C'est le cas le plus defavorable pour une detection
automatique, et le comportement sur un mot seul n'est pas garanti.

La regle reste donc la meme : **entourer explicitement le francais d'une balise `<voice>`**
avec une voix `fr-FR-...`, meme lorsque la voix de base est multilingue. C'est deterministe,
et la voix distincte signale a l'oreille qu'il s'agit de la traduction.

---

## 3. Regles imperatives

### Le document doit etre du XML bien forme

C'est la contrainte la plus importante. Si le parseur n'arrive pas a lire le XML, il bascule
en mode degrade : **toutes les balises sont supprimees**, les `<break>` deviennent de simples
virgules, et les changements de voix sont perdus. Le rendu part alors entierement de travers,
sans message d'erreur.

En pratique :

- Toute balise ouverte doit etre fermee (`<voice ...>` … `</voice>`).
- Les balises vides s'auto-ferment (`<break time="1s"/>`, pas `<break time="1s">`).
- Les attributs sont toujours entre guillemets.
- **Les caracteres reserves doivent etre echappes dans le texte** :

| Caractere | A ecrire |
| --- | --- |
| `&` | `&amp;` |
| `<` | `&lt;` |
| `>` | `&gt;` |

Exemple du piege : `Rock & Roll<break time="1s"/>forever` casse tout le document.
Il faut ecrire `Rock &amp; Roll<break time="1s"/>forever`.

Les accents et caracteres UTF-8 (`é`, `à`, `ç`…) ne posent aucun probleme.

### Balise `<speak>`

Elle est acceptee et sans effet (avec ou sans `xmlns` / `xml:lang`). On peut l'inclure ou non.
Les `<p>` sont egalement traverses sans effet — ils **ne creent aucune pause**.
Pour une pause entre deux blocs, il faut un `<break>` explicite.

---

## 4. Balises NON supportees

Elles sont supprimees silencieusement, **leur texte interieur est conserve et lu par la voix de base** :

| Balise | Effet reel | A utiliser a la place |
| --- | --- | --- |
| `<lang xml:lang="fr-FR">` | **aucun** — lu avec l'accent de la voix de base | `<voice name="fr-FR-...">` |
| `<phoneme alphabet="ipa" ph="...">` | aucun — le mot est lu normalement | reecrire le mot phonetiquement en clair |
| `<emphasis>` | aucun | `<prosody rate="...">` |
| `<say-as interpret-as="...">` | aucun | ecrire la forme developpee en toutes lettres |
| `<sub alias="...">` | aucun — l'alias est perdu | ecrire directement le texte a prononcer |
| `<audio src="...">` | aucun | — |
| `<prosody pitch=...>` / `<prosody volume=...>` | aucun (seul `rate` est lu) | reglage global dans l'interface |
| `<mark>` | aucun | — |

**`<lang>` est le piege principal** : c'est la balise SSML standard pour changer de langue, mais
elle n'a strictement aucun effet ici. Il faut imperativement `<voice>`.

Pour forcer une prononciation particuliere (par exemple `read` prononce « red », ou `said`
prononce « sed »), `<phoneme>` ne fonctionnera pas : il faut ecrire le mot dans une graphie
qui induit la bonne prononciation, ou isoler le mot dans son propre segment.

---

## 5. Modele pour les exercices francais → anglais

Format cible : traduction francaise, puis les trois formes anglaises.

```xml
<voice name="fr-FR-HenriNeural">Saigner.</voice>
<break time="200ms"/>
Bleed.
<break time="700ms"/>
Bled.
<break time="700ms"/>
Bled.
<break time="1s"/>
<voice name="fr-FR-HenriNeural">Nourrir.</voice>
<break time="200ms"/>
Feed.
<break time="700ms"/>
Fed.
<break time="700ms"/>
Fed.
```

La voix selectionnee dans l'interface doit etre une **voix anglaise** : elle lit tout ce qui
n'est pas entoure d'une balise `<voice>`.

Pour une traduction plus longue, tout tient dans une seule balise :

```xml
<voice name="fr-FR-HenriNeural">Obtenir, avoir, se procurer.</voice>
<break time="200ms"/>
Get.
<break time="700ms"/>
Got.
<break time="700ms"/>
Got.
```

### Attention au texte apres un `<break>` interne

Le contexte d'une balise s'applique bien a tout son contenu, y compris apres un `<break>`
imbrique. Les deux ecritures ci-dessous sont donc equivalentes et correctes :

```xml
<voice name="fr-FR-HenriNeural">Obtenir.<break time="200ms"/>Se procurer.</voice>
<voice name="fr-FR-HenriNeural">Obtenir.</voice><break time="200ms"/><voice name="fr-FR-HenriNeural">Se procurer.</voice>
```

En revanche, un `<break>` place **apres** `</voice>` appartient au flux de base :
c'est le cas normal pour separer le francais de l'anglais.

---

## 6. Cout de generation

Chaque segment de texte et chaque pause declenchent un **appel reseau separe** au service Edge.
Un document tres balise est donc nettement plus lent a generer qu'un texte brut equivalent.

Consequence pratique : regrouper le texte lorsque c'est possible plutot que de multiplier les
balises. Par exemple, preferer une seule balise `<voice>` autour de « Obtenir, avoir, se procurer. »
plutot qu'une balise par mot.

# TTS Generator

Application Streamlit pour convertir du texte en audio avec les voix Microsoft Edge (`edge-tts`), gratuites et sans cle API.

## Lancer l'application

Double-cliquer sur `launch_ui.bat`, puis :

- filtrer les voix par langue, pays et genre, restreindre aux voix multilingues, ou taper une recherche libre (le nom, le `ShortName` ou le pays fonctionnent, et les termes peuvent etre dans n'importe quel ordre : `andrew multi`) ;
- deplier "Ecouter les voix avant de choisir" pour auditionner chaque voix filtree sans changer la selection courante (bouton lecture), puis la selectionner d'un clic (bouton coche) ;
- choisir une voix, puis ouvrir l'icone parametres a cote du selecteur pour ajuster vitesse, volume et hauteur ;
- un aperçu vocal se joue automatiquement en arriere-plan a chaque changement de voix ou de parametre, dans la langue de la voix choisie ;
- coller le texte final, ou l'importer depuis un fichier `.txt`, `.docx` ou `.pdf` via le bouton d'import (le texte extrait remplace le contenu de la zone de texte, et le mode SSML est active automatiquement si le fichier contient des balises `<speak>`, `<break>` ou `<prosody>`) ;
- cliquer sur "Generer l'audio" (le bouton se desactive et affiche une barre de progression pendant la generation, avec un bouton pour annuler), puis telecharger le fichier.

Aucune cle API n'est necessaire, une connexion Internet est requise (les voix sont fournies par le service Microsoft Edge).

Le mode SSML est pris en charge sur les voix Edge : les balises `<break time="..."/>` inserent un silence de la duree exacte (secondes ou millisecondes), `<prosody rate="slow|medium|fast">...</prosody>` ajuste localement la vitesse de lecture par rapport au reglage de base, et `<voice name="...">...</voice>` fait lire ce segment avec une autre voix Edge que celle choisie dans l'interface (utile pour un mot ou une phrase en francais au milieu d'un texte lu par une voix anglaise, par exemple `<voice name="fr-FR-HenriNeural">Saigner.</voice>`). Le `name` attendu est le `ShortName` Edge de la voix, sensible a la casse (il est affiche en fin de libelle dans le selecteur de voix, et la recherche du selecteur l'accepte). Le reste du balisage est ignore -- en particulier `<lang xml:lang="...">`, qui n'a aucun effet : c'est bien `<voice>` qu'il faut utiliser pour changer de langue. Comme chaque segment de texte et chaque pause declenchent un appel reseau separe, un texte avec beaucoup de balises prend plus de temps a generer qu'un texte brut equivalent (la barre de progression avance segment par segment).

Les noms de voix sont verifies **avant** le premier appel reseau : un `<voice name="...">` mal forme ou inconnu arrete la generation immediatement, en nommant la voix fautive et en suggerant la correction la plus proche, plutot que d'echouer a mi-parcours en laissant un fichier tronque.

La specification complete du balisage supporte est dans [`SSML_SPEC.md`](SSML_SPEC.md) : elle est redigee pour etre collee dans les instructions d'un generateur automatique de SSML (ChatGPT ou autre).

Pour obtenir la liste exacte des `ShortName` utilisables :

```bash
python tools/list_voices.py fr en                 # affiche les voix francaises et anglaises
python tools/list_voices.py fr en -o voices.md    # ecrit un tableau Markdown
```

## Lancer manuellement

Toujours appeler l'interpreteur du venv explicitement, comme le fait `launch_ui.bat` :

```bat
".venv\Scripts\python.exe" -m streamlit run ui.py
```

Ne pas se fier a `activate` : un venv n'est pas relocalisable, donc si le projet a ete
deplace depuis la creation du venv, `activate.bat` exporte un `VIRTUAL_ENV` obsolete.
Le prompt affiche alors `(.venv)` alors que `python` pointe en realite sur
l'interpreteur systeme, et les dependances du projet semblent absentes. Dans ce cas,
recreer le venv a son emplacement actuel :

```bat
rmdir /s /q .venv
py -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
```

## Dependances

- `requirements.txt` : les dependances directes du projet, en versions figees.
- `requirements.lock.txt` : le lock complet (directes + transitives), genere par
  `pip freeze`. C'est ce fichier qu'installe `launch_ui.bat`, pour un environnement
  reproductible a l'identique.

Pour regenerer le lock apres avoir modifie `requirements.txt` :

```bat
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m pip freeze > requirements.lock.txt
```

## Architecture

- `ui.py` : interface Streamlit (filtres et audition des voix, apercu en direct, import de fichier, generation avec progression/annulation, export)
- `services/tts_studio.py` : moteur TTS Edge (`edge-tts`), decoupage SSML en segments texte/pause/voix, insertion de silence exacte, filtres de voix et validation des noms de voix
- `services/file_text.py` : extraction de texte depuis des fichiers `.txt`, `.docx` et `.pdf`
- `config.py` : configuration (chemin de sortie des fichiers audio)
- `conftest.py` : rend la racine du projet importable pendant les tests
- `tools/list_voices.py` : liste les `ShortName` Edge utilisables dans `<voice name="...">`
- `SSML_SPEC.md` : specification du balisage supporte, a fournir au generateur de SSML

## Tests

```bat
".venv\Scripts\python.exe" -m pytest
```

- `tests/test_tts_studio.py` : le moteur et le parseur SSML (segments, pauses, `<prosody>`, `<voice>`, filtres, validation)
- `tests/test_ui.py` : tests de fumee de l'interface via le harnais `AppTest` de Streamlit (filtres, audition, selection, validation)

Aucun test ne touche au reseau : la liste des voix et la synthese sont simulees.

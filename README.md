# TTS Generator

Application Streamlit pour convertir du texte en audio avec les voix Microsoft Edge (`edge-tts`), gratuites et sans cle API.

## Lancer l'application

Double-cliquer sur `launch_ui.bat`, puis :

- choisir une voix, puis ouvrir l'icone parametres a cote du selecteur pour ajuster vitesse, volume et hauteur ;
- un aperçu vocal se joue automatiquement en arriere-plan a chaque changement de voix ou de parametre ;
- coller le texte final, cliquer sur "Generer l'audio" (le bouton se desactive et affiche une barre de progression pendant la generation, avec un bouton pour annuler), puis telecharger le fichier.

Aucune cle API n'est necessaire, une connexion Internet est requise (les voix sont fournies par le service Microsoft Edge).

Le mode SSML est pris en charge sur les voix Edge : les balises `<break time="..."/>` inserent un silence de la duree exacte (secondes ou millisecondes), et `<prosody rate="slow|medium|fast">...</prosody>` ajuste localement la vitesse de lecture par rapport au reglage de base. Le reste du balisage est ignore. Comme chaque segment de texte et chaque pause declenchent un appel reseau separe, un texte avec beaucoup de balises prend plus de temps a generer qu'un texte brut equivalent (la barre de progression avance segment par segment).

## Lancer manuellement

```bash
python -m streamlit run ui.py
```

## Architecture

- `ui.py` : interface Streamlit (selection de voix, apercu en direct, generation avec progression/annulation, export)
- `services/tts_studio.py` : moteur TTS Edge (`edge-tts`), decoupage SSML en segments texte/pause et insertion de silence exacte
- `config.py` : configuration (chemin de sortie des fichiers audio)

## Tests

```bash
python -m pytest
```

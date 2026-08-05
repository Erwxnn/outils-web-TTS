# TTS Generator

Application Streamlit pour convertir du texte en audio avec les voix Microsoft Edge (`edge-tts`), gratuites et sans cle API.

## Lancer l'application

Double-cliquer sur `launch_ui.bat`, puis :

- rechercher et choisir une voix (barre de recherche par nom, langue ou genre) ;
- ajuster vitesse, volume et hauteur ;
- ecouter l'apercu instantane qui se joue automatiquement a chaque changement de voix ou de parametre ;
- coller le texte final, generer puis telecharger le fichier audio.

Aucune cle API n'est necessaire, une connexion Internet est requise (les voix sont fournies par le service Microsoft Edge).

Le mode SSML est disponible mais reste une approximation sur les voix Edge : les balises `<break>` sont converties en pauses et le reste du balisage est retire, sans garantie de minutage exact.

## Lancer manuellement

```bash
python -m streamlit run ui.py
```

## Architecture

- `ui.py` : interface Streamlit (recherche de voix, apercu en direct, generation et export)
- `services/tts_studio.py` : moteur TTS Edge (`edge-tts`) et approximation SSML
- `config.py` : configuration (chemin de sortie des fichiers audio)

## Tests

```bash
python -m pytest
```

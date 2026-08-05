# TTS Generator

Application Streamlit pour convertir du texte en audio avec des voix Windows natives ou des voix Microsoft Edge gratuites via `edge-tts`.

## Lancer l'application

Double-cliquer sur `launch_ui.bat`, puis :

- coller le texte a convertir ;
- choisir une voix Windows ou Edge ;
- ajuster vitesse, volume et hauteur ;
- generer puis telecharger le fichier audio.

Aucune cle API n'est necessaire : les voix Windows et Edge sont utilisees gratuitement.

Le mode SSML est supporte avec les voix Windows. Les voix Edge sont utilisees pour du texte brut uniquement.

## Lancer manuellement

```bash
python -m streamlit run ui.py
```

## Architecture

- `ui.py` : interface Streamlit du generateur TTS (point d'entree de l'application)
- `services/tts_studio.py` : moteurs TTS Windows (SAPI) et Edge (edge-tts)
- `config.py` : configuration (chemin de sortie des fichiers audio)

## Tests

```bash
python -m pytest
```

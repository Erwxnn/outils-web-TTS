"""Streamlit UI for the TTS generator."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st

from config import Settings
from services.tts_studio import EngineName, TTSRequest, TTSStudio, Voice


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEXT = "Hello. This is a simple text-to-speech test. You can change the voice and the speed."
DEFAULT_SSML = """<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
  <p>Hello.</p>
  <break time="700ms"/>
  <p>This sentence uses SSML pauses with a native Windows voice.</p>
</speak>"""


def configure_logging(output_path: Path) -> None:
    log_dir = output_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


@st.cache_data(ttl="10m", show_spinner=False)
def cached_voices(engine: EngineName) -> list[Voice]:
    studio = TTSStudio(PROJECT_ROOT / "outputs" / "tts")
    return studio.list_voices(engine)


def audio_mime(path: Path) -> str:
    return "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"


def voice_label(voice: Voice) -> str:
    return voice.label


def main() -> None:
    st.set_page_config(page_title="TTS generator", layout="wide")
    settings = Settings.from_env(PROJECT_ROOT)
    configure_logging(settings.output_path)
    st.session_state.setdefault("last_audio", None)

    st.title("TTS generator")
    st.caption("Colle ton texte, choisis une voix, ajuste le rendu, puis exporte l'audio.")

    studio = TTSStudio(settings.output_path / "tts")

    windows_voices = cached_voices("windows")

    with st.sidebar:
        st.header("Moteur")
        default_engine = "Voix Windows" if windows_voices else "Voix Edge gratuites"
        engine_label = st.segmented_control(
            "Source des voix",
            ["Voix Windows", "Voix Edge gratuites"],
            default=default_engine,
        )
        engine: EngineName = "windows" if engine_label == "Voix Windows" else "edge"

        if engine == "windows":
            st.info("Utilise les voix natives installees sur Windows. Supporte le SSML.")
        else:
            st.info("Utilise edge-tts : voix Microsoft en ligne sans cle API. Connexion Internet requise.")

        if st.button("Rafraichir les voix", icon=":material/refresh:", width="stretch"):
            cached_voices.clear()
            st.rerun()

    voices = windows_voices if engine == "windows" else cached_voices("edge")
    if not voices:
        if engine == "edge":
            st.error("Aucune voix Edge disponible. Verifie que edge-tts est installe et que la connexion Internet fonctionne.")
        else:
            st.error("Aucune voix Windows SAPI detectee. Installe une voix Windows compatible SAPI ou utilise les voix Edge gratuites.")
        return

    with st.container(border=True):
        voice = st.selectbox("Voix", voices, format_func=voice_label)
        voice_info = f"{voice.name} | {voice.locale}"
        if voice.gender:
            voice_info += f" | {voice.gender}"
        st.caption(voice_info)

        text_mode = st.segmented_control("Mode texte", ["Texte brut", "SSML"], default="Texte brut")
        if engine == "edge" and text_mode == "SSML":
            st.warning("Le mode SSML est reserve aux voix Windows dans cette app.")
            text_mode = "Texte brut"

        default_value = DEFAULT_SSML if text_mode == "SSML" else DEFAULT_TEXT
        text = st.text_area("Texte a convertir", value=default_value, height=260)

    with st.container(border=True):
        st.subheader("Personnalisation")
        rate_bounds = (-10, 10) if engine == "windows" else (-50, 50)
        volume_bounds = (0, 100) if engine == "windows" else (-50, 50)
        rate_help = "Windows SAPI : -10 a 10" if engine == "windows" else "Edge : pourcentage, -50 a 50"
        volume_help = "Windows SAPI : 0 a 100" if engine == "windows" else "Edge : pourcentage, -50 a 50"

        rate = st.slider("Vitesse", rate_bounds[0], rate_bounds[1], 0, help=rate_help)
        volume_default = 100 if engine == "windows" else 0
        volume = st.slider("Volume", volume_bounds[0], volume_bounds[1], volume_default, help=volume_help)
        pitch = 0
        if engine == "edge":
            pitch = st.slider("Hauteur de voix", -100, 100, 0, help="Edge : variation en Hz")

    generate = st.button("Generer l'audio", type="primary", icon=":material/graphic_eq:", width="stretch")
    if generate:
        if not text.strip():
            st.error("Ajoute du texte avant de generer.")
        else:
            request = TTSRequest(
                engine=engine,
                text=text.strip(),
                text_mode="ssml" if text_mode == "SSML" else "plain",
                voice_id=voice.id,
                rate=rate,
                volume=volume,
                pitch=pitch,
            )
            with st.spinner("Synthese vocale en cours..."):
                try:
                    output_path = studio.synthesize(request)
                except Exception as exc:
                    st.error(f"Generation impossible : {exc}")
                else:
                    st.session_state["last_audio"] = str(output_path)
                    st.success(f"Audio genere : {output_path.name}")

    if st.session_state["last_audio"]:
        output_path = Path(st.session_state["last_audio"])
        if output_path.exists():
            st.subheader("Resultat")
            st.audio(str(output_path))
            st.download_button(
                "Telecharger l'audio",
                output_path.read_bytes(),
                file_name=output_path.name,
                mime=audio_mime(output_path),
                icon=":material/download:",
                width="stretch",
            )


if __name__ == "__main__":
    main()

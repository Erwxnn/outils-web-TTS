"""Streamlit UI for the TTS generator."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st

from config import Settings
from services.tts_studio import TTSRequest, TTSStudio, Voice


PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_TEXT = "Hello. This is a simple text-to-speech test. You can change the voice and the speed."
DEFAULT_SSML = """<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
  <p>Hello.</p>
  <break time="700ms"/>
  <p>This sentence uses SSML pauses, approximated on Edge voices.</p>
</speak>"""

PREVIEW_DEFAULT_PLAIN = "Hello! This is a quick preview of this voice with the current settings."
PREVIEW_DEFAULT_SSML = '<speak><p>Hello.</p><break time="500ms"/><p>This is a quick preview.</p></speak>'


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
def cached_voices() -> list[Voice]:
    studio = TTSStudio(PROJECT_ROOT / "outputs" / "tts")
    return studio.list_voices()


def voice_label(voice: Voice) -> str:
    return voice.label


def main() -> None:
    st.set_page_config(page_title="TTS generator", page_icon="🔊", layout="wide")
    st.markdown("<style>.block-container{padding-top:2.5rem;}</style>", unsafe_allow_html=True)

    settings = Settings.from_env(PROJECT_ROOT)
    configure_logging(settings.output_path)
    st.session_state.setdefault("last_audio", None)
    st.session_state.setdefault("preview_signature", None)
    st.session_state.setdefault("preview_audio", None)

    st.title("🔊 TTS Generator")
    st.caption("Colle ton texte, choisis une voix Microsoft Edge, ajuste le rendu, puis exporte l'audio.")

    studio = TTSStudio(settings.output_path / "tts")
    voices = cached_voices()

    with st.sidebar:
        st.header("Voix Microsoft Edge")
        st.caption("Voix en ligne gratuites, aucune cle API necessaire. Connexion Internet requise.")
        if st.button("Rafraichir les voix", icon=":material/refresh:", width="stretch"):
            cached_voices.clear()
            st.rerun()
        if voices:
            st.caption(f"{len(voices)} voix disponibles.")

    if not voices:
        st.error(
            "Aucune voix Edge disponible. Verifie que edge-tts est installe "
            "et que la connexion Internet fonctionne."
        )
        return

    with st.container(border=True):
        st.subheader("🎙️ Voix")
        search = st.text_input(
            "Rechercher une voix",
            placeholder="ex : French, Aria, en-GB, Female...",
            help="Filtre la liste par nom, langue ou genre.",
        )
        query = search.strip().lower()
        filtered_voices = [v for v in voices if query in v.search_text] if query else voices

        if not filtered_voices:
            st.warning(f"Aucune voix ne correspond a « {search} ».")
            return

        voice = st.selectbox("Voix", filtered_voices, format_func=voice_label)
        voice_info = f"{voice.name} | {voice.locale}"
        if voice.gender:
            voice_info += f" | {voice.gender}"
        st.caption(voice_info)

    with st.container(border=True):
        st.subheader("📝 Texte")
        text_mode = st.segmented_control("Mode texte", ["Texte brut", "SSML"], default="Texte brut")
        if text_mode == "SSML":
            st.caption(
                "Les voix Edge n'ont pas de moteur SSML natif : les balises <break> sont "
                "approximees par des pauses et le reste du balisage est simplement retire "
                "(le minutage exact n'est pas garanti, contrairement aux voix Windows)."
            )

        default_value = DEFAULT_SSML if text_mode == "SSML" else DEFAULT_TEXT
        text = st.text_area("Texte a convertir", value=default_value, height=260)

    with st.container(border=True):
        st.subheader("🎛️ Personnalisation")
        rate = st.slider("Vitesse", -50, 50, 0, help="Pourcentage, -50 a 50")
        volume = st.slider("Volume", -50, 50, 0, help="Pourcentage, -50 a 50")
        pitch = st.slider("Hauteur de voix", -100, 100, 0, help="Variation en Hz")

    with st.container(border=True):
        st.subheader("🔊 Aperçu en direct")
        st.caption("Change de voix ou ajuste un parametre : un court extrait se joue automatiquement.")

        preview_default = PREVIEW_DEFAULT_SSML if text_mode == "SSML" else PREVIEW_DEFAULT_PLAIN
        preview_text = st.text_input(
            "Texte de test",
            value=preview_default,
            key=f"preview_text_{text_mode}",
            help="Ce texte court sert uniquement a l'apercu instantane ci-dessous.",
        )

        just_generated = False
        preview_signature = (voice.id, rate, volume, pitch, text_mode, preview_text)
        if preview_text.strip() and preview_signature != st.session_state["preview_signature"]:
            preview_request = TTSRequest(
                text=preview_text.strip(),
                text_mode="ssml" if text_mode == "SSML" else "plain",
                voice_id=voice.id,
                rate=rate,
                volume=volume,
                pitch=pitch,
            )
            try:
                with st.spinner("Generation de l'apercu..."):
                    preview_path = studio.synthesize(preview_request, filename="preview.mp3")
                    st.session_state["preview_audio"] = preview_path.read_bytes()
                    st.session_state["preview_signature"] = preview_signature
                    just_generated = True
            except Exception as exc:
                st.warning(f"Apercu impossible : {exc}")

        replay_clicked = st.button(
            "🔁 Rejouer l'apercu",
            disabled=not st.session_state["preview_audio"],
            help="Si l'apercu ne s'est pas lance automatiquement (regles du navigateur), rejoue-le ici.",
        )

        if st.session_state["preview_audio"]:
            st.audio(
                st.session_state["preview_audio"],
                format="audio/mpeg",
                autoplay=just_generated or replay_clicked,
            )

    generate = st.button("Generer l'audio", type="primary", icon=":material/graphic_eq:", width="stretch")
    if generate:
        if not text.strip():
            st.error("Ajoute du texte avant de generer.")
        else:
            request = TTSRequest(
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
                mime="audio/mpeg",
                icon=":material/download:",
                width="stretch",
            )


if __name__ == "__main__":
    main()

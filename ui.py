"""Streamlit UI for the TTS generator."""

from __future__ import annotations

import base64
import logging
import sys
import threading
import time
from pathlib import Path

import streamlit as st

from config import Settings
from services.tts_studio import TTSCancelled, TTSRequest, TTSStudio, Voice


PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_TEXT = "Hello. This is a simple text-to-speech test. You can change the voice and the speed."
DEFAULT_SSML = """<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
  <p>Hello.</p>
  <break time="700ms"/>
  <prosody rate="slow">This sentence is read more slowly.</prosody>
  <break time="1s"/>
  <p>Breaks and prosody rate are honoured precisely on Edge voices.</p>
</speak>"""

# Preview text is intentionally fixed server-side: it is used only to let the
# user hear a voice/setting change instantly, never shown or editable in the UI.
PREVIEW_TEXT_PLAIN = "Hello! This is a quick preview of this voice with the current settings."
PREVIEW_TEXT_SSML = '<speak><p>Hello.</p><break time="500ms"/><p>This is a quick preview.</p></speak>'


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


def _play_hidden_audio(audio_bytes: bytes) -> None:
    """Play an audio clip in the background, with no visible player."""
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    st.markdown(
        f"""
        <audio autoplay style="display:none">
            <source src="data:audio/mpeg;base64,{encoded}" type="audio/mpeg">
        </audio>
        """,
        unsafe_allow_html=True,
    )


def _run_generation_job(studio: TTSStudio, request: TTSRequest, job: dict) -> None:
    """Run synthesis in a background thread, reporting progress into ``job``.

    Only plain-Python mutations happen here (no Streamlit calls), since this
    runs on a worker thread: the main thread reads ``job`` on each rerun.
    """

    def on_progress(fraction: float) -> None:
        job["progress"] = fraction

    try:
        output_path = studio.synthesize(
            request,
            on_progress=on_progress,
            should_cancel=job["cancel_event"].is_set,
        )
        job["result_path"] = str(output_path)
    except TTSCancelled:
        job["cancelled"] = True
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        job["error"] = str(exc)
    finally:
        job["done"] = True


def main() -> None:
    st.set_page_config(page_title="TTS generator", page_icon="🔊", layout="wide")
    st.markdown("<style>.block-container{padding-top:2.5rem;}</style>", unsafe_allow_html=True)

    settings = Settings.from_env(PROJECT_ROOT)
    configure_logging(settings.output_path)
    st.session_state.setdefault("last_audio", None)
    st.session_state.setdefault("preview_signature", None)
    st.session_state.setdefault("preview_audio", None)
    st.session_state.setdefault("gen_job", None)

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
        col_voice, col_settings, col_replay = st.columns([8, 1, 1])
        with col_voice:
            voice = st.selectbox("Voix", voices, format_func=voice_label)
        with col_settings:
            with st.popover("", icon=":material/tune:", help="Parametres de la voix"):
                st.caption("Parametres de la voix")
                rate = st.slider("Vitesse", -50, 50, 0, help="Pourcentage, -50 a 50")
                volume = st.slider("Volume", -50, 50, 0, help="Pourcentage, -50 a 50")
                pitch = st.slider("Hauteur de voix", -100, 100, 0, help="Variation en Hz")
        with col_replay:
            replay_clicked = st.button(
                "",
                icon=":material/replay:",
                help="Rejouer l'apercu vocal (utile si la lecture automatique a ete bloquee).",
            )

        voice_info = f"{voice.name} | {voice.locale}"
        if voice.gender:
            voice_info += f" | {voice.gender}"
        st.caption(voice_info)

    with st.container(border=True):
        st.subheader("📝 Texte")
        text_mode = st.segmented_control("Mode texte", ["Texte brut", "SSML"], default="Texte brut")
        if text_mode == "SSML":
            st.caption(
                "Les balises <break time=\"...\"/> inserent un silence de la duree exacte, et "
                "<prosody rate=\"slow|medium|fast\"> ajuste localement la vitesse de lecture. "
                "Le reste du balisage est ignore. Chaque segment de texte et chaque pause "
                "declenchent un appel reseau separe : un texte avec beaucoup de balises "
                "prend donc plus de temps a generer."
            )

        default_value = DEFAULT_SSML if text_mode == "SSML" else DEFAULT_TEXT
        text = st.text_area("Texte a convertir", value=default_value, height=260)

    # Live preview: regenerated only when the voice or a setting actually changes.
    # The preview text and audio player are intentionally not shown to the user.
    preview_text = PREVIEW_TEXT_SSML if text_mode == "SSML" else PREVIEW_TEXT_PLAIN
    preview_signature = (voice.id, rate, volume, pitch, text_mode)
    just_generated = False
    if preview_signature != st.session_state["preview_signature"]:
        preview_request = TTSRequest(
            text=preview_text,
            text_mode="ssml" if text_mode == "SSML" else "plain",
            voice_id=voice.id,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        try:
            with st.spinner("Apercu en cours..."):
                preview_path = studio.synthesize(preview_request, filename="preview.mp3")
                st.session_state["preview_audio"] = preview_path.read_bytes()
                st.session_state["preview_signature"] = preview_signature
                just_generated = True
        except Exception as exc:
            st.toast(f"Apercu impossible : {exc}", icon="⚠️")

    if (just_generated or replay_clicked) and st.session_state["preview_audio"]:
        _play_hidden_audio(st.session_state["preview_audio"])

    job = st.session_state["gen_job"]
    generating = job is not None

    generate = st.button(
        "Generer l'audio",
        type="primary",
        icon=":material/graphic_eq:",
        width="stretch",
        disabled=generating,
        help="Desactive pendant qu'une generation est en cours." if generating else None,
    )
    if generate and not generating:
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
            job = {
                "progress": 0.0,
                "done": False,
                "cancelled": False,
                "error": None,
                "result_path": None,
                "cancel_event": threading.Event(),
            }
            st.session_state["gen_job"] = job
            thread = threading.Thread(
                target=_run_generation_job, args=(studio, request, job), daemon=True
            )
            thread.start()
            st.rerun()

    if generating:
        if job["progress"] <= 0.0:
            stage_label = "Connexion au service Edge..."
        elif job["progress"] >= 0.97:
            stage_label = "Finalisation..."
        else:
            stage_label = f"Synthese en cours... {int(job['progress'] * 100)} % du texte"
        st.progress(min(job["progress"], 1.0), text=stage_label)

        cancel_clicked = st.button(
            "❌ Annuler la generation",
            icon=":material/cancel:",
            width="stretch",
            disabled=job["cancel_event"].is_set(),
        )
        if cancel_clicked:
            job["cancel_event"].set()
            st.toast("Annulation en cours...", icon="⏹️")

        if job["done"]:
            st.session_state["gen_job"] = None
            if job["cancelled"]:
                st.warning("Generation annulee.")
            elif job["error"]:
                st.error(f"Generation impossible : {job['error']}")
            else:
                st.session_state["last_audio"] = job["result_path"]
                st.success(f"Audio genere : {Path(job['result_path']).name}")
        else:
            time.sleep(0.25)
            st.rerun()

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

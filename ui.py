"""Streamlit UI for the TTS generator."""

from __future__ import annotations

import base64
import logging
import re
import sys
import threading
import time
from pathlib import Path

import streamlit as st

from config import Settings
from services.file_text import UnsupportedFileError, extract_text
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


_SSML_HINT_RE = re.compile(r"<\s*(speak|break|prosody)\b", re.IGNORECASE)


def _looks_like_ssml(text: str) -> bool:
    return bool(_SSML_HINT_RE.search(text))


def _play_hidden_audio(audio_bytes: bytes) -> None:
    """Play an audio clip in the background, with no visible player.

    A per-call token is embedded in the markup so the generated HTML string
    always changes, even when the audio bytes are identical to the previous
    playback (e.g. clicking "replay" without changing any setting). Without
    this, the browser sees byte-identical markup on rerun and never
    recreates the <audio> element, so autoplay only ever fires once.
    """
    st.session_state["_audio_play_token"] = st.session_state.get("_audio_play_token", 0) + 1
    token = st.session_state["_audio_play_token"]
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    st.markdown(
        f"""
        <audio id="tts-preview-{token}" autoplay style="display:none">
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
    st.markdown(
        """
        <style>
        .block-container{padding-top:2.5rem;}
        div[data-testid="stPopoverBody"]{min-width: 420px !important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    settings = Settings.from_env(PROJECT_ROOT)
    configure_logging(settings.output_path)
    st.session_state.setdefault("last_audio", None)
    st.session_state.setdefault("preview_signature", None)
    st.session_state.setdefault("preview_audio", None)
    st.session_state.setdefault("gen_job", None)
    st.session_state.setdefault("text_mode", "Texte brut")
    st.session_state.setdefault("text_area_content", DEFAULT_TEXT)
    st.session_state.setdefault("last_uploaded_file_id", None)
    st.session_state.setdefault("voice_rate", 0)
    st.session_state.setdefault("voice_volume", 0)
    st.session_state.setdefault("voice_pitch", 0)

    st.title("🔊 TTS Generator")
    st.caption("Colle ton texte, choisis une voix Microsoft Edge, ajuste le rendu, puis exporte l'audio.")

    studio = TTSStudio(settings.output_path / "tts")
    voices = cached_voices()

    with st.sidebar:
        st.header("Voix Microsoft Edge")
        st.caption("Voix en ligne gratuites, Connexion Internet requise.")
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
        col_voice, col_buttons = st.columns([8, 2])
        with col_voice:
            voice = st.selectbox("Voix", voices, format_func=voice_label)
        with col_buttons:
            # Spacer to line up with the selectbox's label, then group the
            # settings and replay buttons in their own row so they always
            # stay side by side on the same line, even if this outer column
            # wraps below the selectbox on a narrow screen.
            st.write("")
            col_settings, col_replay = st.columns(2, gap="small")
            with col_settings:
                # The 3-column layout inside the popover (rather than
                # stacking the sliders vertically) forces the panel to lay
                # out wider content, and the CSS min-width rule injected
                # above guarantees it regardless of viewport.
                #
                # Values are round-tripped through plain session_state
                # entries (read as the widget's `value`, written back from
                # its return) rather than relying on a widget `key` -
                # Streamlit drops key-bound widget state for widgets that
                # don't render on a given rerun, which would otherwise
                # reset the sliders whenever the popover is closed.
                with st.popover("", icon=":material/tune:", help="Parametres de la voix", width="stretch"):
                    st.caption("Parametres de la voix")
                    col_rate, col_volume, col_pitch = st.columns(3)
                    with col_rate:
                        st.session_state["voice_rate"] = st.slider(
                            "Vitesse", -50, 50, st.session_state["voice_rate"], help="Pourcentage, -50 a 50"
                        )
                    with col_volume:
                        st.session_state["voice_volume"] = st.slider(
                            "Volume", -50, 50, st.session_state["voice_volume"], help="Pourcentage, -50 a 50"
                        )
                    with col_pitch:
                        st.session_state["voice_pitch"] = st.slider(
                            "Hauteur de voix", -100, 100, st.session_state["voice_pitch"], help="Variation en Hz"
                        )
            with col_replay:
                replay_clicked = st.button(
                    "",
                    icon=":material/replay:",
                    help="Rejouer l'apercu vocal (utile si la lecture automatique a ete bloquee).",
                    width="stretch",
                )

        rate = st.session_state["voice_rate"]
        volume = st.session_state["voice_volume"]
        pitch = st.session_state["voice_pitch"]

        voice_info = f"{voice.name} | {voice.locale}"
        if voice.gender:
            voice_info += f" | {voice.gender}"
        st.caption(voice_info)

    with st.container(border=True):
        st.subheader("📝 Texte")

        uploaded_file = st.file_uploader(
            "Importer un fichier (.txt, .docx, .pdf)",
            type=["txt", "docx", "pdf"],
            help="Le texte extrait remplace le contenu de la zone de texte ci-dessous.",
        )
        if uploaded_file is not None and uploaded_file.file_id != st.session_state["last_uploaded_file_id"]:
            st.session_state["last_uploaded_file_id"] = uploaded_file.file_id
            try:
                extracted = extract_text(uploaded_file.name, uploaded_file.getvalue())
            except UnsupportedFileError as exc:
                st.toast(str(exc), icon="⚠️")
            except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
                st.toast(f"Extraction impossible : {exc}", icon="⚠️")
            else:
                if extracted.strip():
                    st.session_state["text_area_content"] = extracted
                    st.session_state["text_mode"] = "SSML" if _looks_like_ssml(extracted) else "Texte brut"
                    st.toast(f"Texte extrait de {uploaded_file.name}.", icon="📄")
                    st.rerun()
                else:
                    st.toast("Aucun texte trouve dans ce fichier.", icon="⚠️")

        text_mode = st.segmented_control("Mode texte", ["Texte brut", "SSML"], key="text_mode")
        if text_mode == "SSML":
            st.caption(
                "Les balises <break time=\"...\"/> inserent un silence de la duree exacte, et "
                "<prosody rate=\"slow|medium|fast\"> ajuste localement la vitesse de lecture. "
                "Le reste du balisage est ignore. Chaque segment de texte et chaque pause "
                "declenchent un appel reseau separe : un texte avec beaucoup de balises "
                "prend donc plus de temps a generer."
            )

        # Swap in the matching sample text when the mode toggles, but only if
        # the field still holds one of the defaults (never overwrite an
        # upload or something the user typed themselves).
        previous_mode = st.session_state.get("_prev_text_mode", text_mode)
        if text_mode != previous_mode:
            current_content = st.session_state.get("text_area_content", "")
            if current_content in (DEFAULT_TEXT, DEFAULT_SSML) or not current_content.strip():
                st.session_state["text_area_content"] = DEFAULT_SSML if text_mode == "SSML" else DEFAULT_TEXT
        st.session_state["_prev_text_mode"] = text_mode

        text = st.text_area("Texte a convertir", key="text_area_content", height=260)

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

    # The nav bar's native "running" indicator is tied 1:1 to each polling
    # rerun triggered below (every ~250ms): it resets/flickers in lockstep
    # with the generation progress bar instead of just spinning for the real,
    # uninterrupted duration of the job. We hide it and draw our own
    # indicator instead. Crucially, this markup is byte-identical on every
    # rerun while a job is in flight (no per-rerun token), so Streamlit's
    # frontend keeps the same DOM node mounted and its CSS animation keeps
    # spinning without a single cut - fully decoupled from `job["progress"]`
    # - all the way to the job's actual completion (`job["done"]`).
    if generating and not job["done"]:
        st.markdown(
            """
            <style>
            [data-testid="stStatusWidget"] { display: none !important; }
            @keyframes tss-nav-spin { to { transform: rotate(360deg); } }
            .tss-nav-spinner {
                position: fixed;
                top: 0.6rem;
                right: 4.5rem;
                width: 20px;
                height: 20px;
                border: 3px solid rgba(49, 51, 63, 0.15);
                border-top-color: #ff4b4b;
                border-radius: 50%;
                animation: tss-nav-spin 0.8s linear infinite;
                z-index: 999999;
                pointer-events: none;
            }
            </style>
            <div class="tss-nav-spinner"></div>
            """,
            unsafe_allow_html=True,
        )

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

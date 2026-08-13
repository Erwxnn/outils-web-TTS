"""Smoke tests for the Streamlit UI, driven by Streamlit's own AppTest harness.

These never touch the network: the voice list and the synthesis call are both
faked, so what is exercised is the UI wiring (filters, selection, audition
list, validation) rather than the Edge service.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.tts_studio import EdgeTTSEngine, voice_from_row

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

UI_PATH = str(Path(__file__).resolve().parent.parent / "ui.py")


def _row(short_name, locale, friendly_tail, gender):
    return {
        "ShortName": short_name,
        "FriendlyName": f"Microsoft X Online (Natural) - {friendly_tail}",
        "Locale": locale,
        "Gender": gender,
    }


FAKE_VOICES = [
    voice_from_row(_row("en-US-AriaNeural", "en-US", "English (United States)", "Female")),
    voice_from_row(
        _row("en-US-AndrewMultilingualNeural", "en-US", "English (United States)", "Male")
    ),
    voice_from_row(_row("en-GB-RyanNeural", "en-GB", "English (United Kingdom)", "Male")),
    voice_from_row(_row("fr-FR-HenriNeural", "fr-FR", "French (France)", "Male")),
    voice_from_row(_row("fr-FR-DeniseNeural", "fr-FR", "French (France)", "Female")),
    voice_from_row(_row("fr-CA-SylvieNeural", "fr-CA", "French (Canada)", "Female")),
]


@pytest.fixture(autouse=True)
def _offline_engine(monkeypatch):
    """Replace voice discovery and synthesis with local, network-free fakes."""
    monkeypatch.setattr(EdgeTTSEngine, "list_voices", lambda self: list(FAKE_VOICES))

    def fake_synthesize(self, request, output_path, *, on_progress=None, should_cancel=None):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ID3fake-audio")
        if on_progress is not None:
            on_progress(1.0)
        return output_path

    monkeypatch.setattr(EdgeTTSEngine, "synthesize", fake_synthesize)


def _run():
    app = AppTest.from_file(UI_PATH, default_timeout=30)
    app.run()
    return app


def _widget_by_key(app, kind, key):
    return next(w for w in getattr(app, kind) if w.key == key)


def test_app_starts_and_lists_every_voice():
    app = _run()

    assert not app.exception
    voice_select = _select_voice_widget(app)
    assert len(voice_select.options) == len(FAKE_VOICES)


def _select_voice_widget(app):
    """The main voice selectbox is the only one without a key."""
    return next(s for s in app.selectbox if s.key is None)


def _option_ids(app):
    """ShortNames offered by the voice selectbox.

    AppTest exposes ``options`` as the *formatted* labels rather than the Voice
    objects, and ``voice_label`` puts the ShortName last, so it is read back off
    the end of each label.
    """
    return [opt.rsplit(" - ", 1)[-1] for opt in _select_voice_widget(app).options]


def test_language_filter_narrows_the_voice_list():
    app = _run()

    _widget_by_key(app, "selectbox", "filter_language").set_value("French").run()

    assert not app.exception
    assert _option_ids(app) == [
        "fr-FR-HenriNeural",
        "fr-FR-DeniseNeural",
        "fr-CA-SylvieNeural",
    ]


def test_country_filter_narrows_further_and_resets_when_language_changes():
    app = _run()

    _widget_by_key(app, "selectbox", "filter_language").set_value("French").run()
    _widget_by_key(app, "selectbox", "filter_country").set_value("Canada").run()
    assert _option_ids(app) == ["fr-CA-SylvieNeural"]

    # Switching language must clear the now-impossible country choice rather
    # than leaving a stale value that no longer exists in the options.
    _widget_by_key(app, "selectbox", "filter_language").set_value("English").run()
    assert not app.exception
    assert _widget_by_key(app, "selectbox", "filter_country").value == "Tous"
    assert _option_ids(app) == [
        "en-US-AriaNeural",
        "en-US-AndrewMultilingualNeural",
        "en-GB-RyanNeural",
    ]


def test_gender_and_multilingual_filters():
    app = _run()

    _widget_by_key(app, "checkbox", "filter_multilingual").set_value(True).run()

    assert not app.exception
    assert _option_ids(app) == ["en-US-AndrewMultilingualNeural"]


def test_search_box_matches_terms_in_any_order():
    app = _run()

    _widget_by_key(app, "text_input", "filter_query").set_value("andrew multi").run()

    assert not app.exception
    assert _option_ids(app) == ["en-US-AndrewMultilingualNeural"]


def test_impossible_filter_combination_warns_and_keeps_the_app_usable():
    app = _run()

    _widget_by_key(app, "selectbox", "filter_language").set_value("French").run()
    _widget_by_key(app, "checkbox", "filter_multilingual").set_value(True).run()

    assert not app.exception
    assert any("Aucune voix" in w.value for w in app.warning)
    # Filters are ignored rather than leaving an empty, unusable selector.
    assert len(_select_voice_widget(app).options) == len(FAKE_VOICES)


def test_pick_button_selects_a_voice_without_using_the_selectbox():
    app = _run()

    initial = _select_voice_widget(app).value
    assert initial.id == "en-US-AriaNeural"

    _widget_by_key(app, "button", "pick_fr-FR-HenriNeural").click().run()

    assert not app.exception
    assert _select_voice_widget(app).value.id == "fr-FR-HenriNeural"


def test_audition_button_plays_a_sample_without_changing_the_selection():
    app = _run()

    _widget_by_key(app, "button", "audition_fr-FR-HenriNeural").click().run()

    assert not app.exception
    # The committed voice is untouched...
    assert _select_voice_widget(app).value.id == "en-US-AriaNeural"
    # ...and an audition clip was produced and injected for autoplay.
    assert any("<audio" in m.value for m in app.markdown)


def test_generation_rejects_an_unknown_voice_name_in_ssml():
    app = _run()

    app.session_state["text_area_content"] = (
        'Hello.<voice name="fr-FR-HenryNeural">Bonjour.</voice>'
    )
    app.run()
    _widget_by_key(app, "segmented_control", "text_mode").set_value("SSML").run()

    generate = next(b for b in app.button if "Generer" in b.label)
    generate.click().run()

    assert not app.exception
    errors = " ".join(e.value for e in app.error)
    assert "fr-FR-HenryNeural" in errors
    assert "fr-FR-HenriNeural" in errors  # the suggested correction


def test_generation_accepts_a_valid_voice_override():
    app = _run()

    app.session_state["text_area_content"] = (
        'Hello.<voice name="fr-FR-HenriNeural">Bonjour.</voice>'
    )
    app.run()
    _widget_by_key(app, "segmented_control", "text_mode").set_value("SSML").run()

    generate = next(b for b in app.button if "Generer" in b.label)
    generate.click().run()

    assert not app.exception
    assert not app.error

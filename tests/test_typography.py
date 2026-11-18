from PIL import Image, ImageDraw

from trackerbot.presentation.typography import FONT_DIR, SHAPING, font, text


def test_bundled_fonts_load_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert (FONT_DIR / "OFL.txt").is_file()
    assert font(24).getname()[0] == "Inter"
    assert font(24, italic=True).getname()[0] == "Inter"
    assert font(24, italic=True).getname()[1] == "Italic"


def test_weights_produce_distinct_glyphs():
    masks = [bytes(font(40, weight=weight).getmask("Bingecord"))
             for weight in (400, 600, 700, 800)]
    assert len(set(masks)) == 4


def test_shared_text_uses_aligned_numbers():
    draw = ImageDraw.Draw(Image.new("RGB", (300, 80)))
    face = font(24)
    if SHAPING:
        assert draw.textlength("1111", font=face, **SHAPING) == draw.textlength("8888", font=face, **SHAPING)
    text(draw, (0, 0), "1,234 XP", font=face, fill="white")


def test_labels_keep_inter_kerning_and_word_spaces():
    from trackerbot.presentation.typography import label
    actual = Image.new("RGB", (500, 80))
    expected = actual.copy()
    label(ImageDraw.Draw(actual), (5, 5), "Bingecord · Watch XP")
    text(ImageDraw.Draw(expected), (5, 5), "BINGECORD · WATCH XP",
         font=font(14, weight=600), fill=(172, 176, 185))
    assert actual.tobytes() == expected.tobytes()

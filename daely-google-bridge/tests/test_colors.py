"""Tests for colors.py — Daely-hex → Google-colorId nearest-match + helpers."""
import pytest

from daely_google_bridge.colors import (
    EMOJI_FOR_COLOR_ID,
    GOOGLE_EVENT_COLORS,
    VALID_COLOR_IDS,
    emoji_for_color_id,
    is_valid_color_id,
    nearest_color_id,
)


class TestNearestColorId:
    def test_exact_match_for_each_palette_color(self):
        """Each Google palette hex must map to its own colorId."""
        for cid, hex_code in GOOGLE_EVENT_COLORS.items():
            assert nearest_color_id(hex_code) == cid, (
                f"{hex_code} should map to {cid}, got {nearest_color_id(hex_code)}"
            )

    def test_pure_red_maps_to_tomato(self):
        # #ff0000 closest to Tomato (#d50000), not Flamingo (#e67c73)
        assert nearest_color_id("#ff0000") == "11"

    def test_pure_green_maps_to_basil_or_sage(self):
        # #00ff00 closer to Basil (#0b8043) than Sage (#33b679)
        # ((11)^2 + (128)^2 + (67)^2) vs ((51)^2 + (73)^2 + (121)^2)
        result = nearest_color_id("#00ff00")
        assert result in {"2", "10"}  # both green-ish; exact pick depends on metric

    def test_pure_blue_maps_to_blueberry(self):
        # #0000ff closer to Blueberry (#3f51b5) than Peacock (#039be5)
        assert nearest_color_id("#0000ff") == "9"

    def test_yellow_maps_to_banana(self):
        assert nearest_color_id("#ffff00") == "5"

    def test_orange_maps_to_tangerine(self):
        assert nearest_color_id("#ff7f00") == "6"

    def test_grey_maps_to_graphite(self):
        assert nearest_color_id("#808080") == "8"

    def test_daely_sample_rust_maps_to_tangerine_or_flamingo(self):
        # The fixture documentation gives #cd812d as a sample profile color
        # (rust/burnt-orange). Should land on Tangerine (orange).
        assert nearest_color_id("#cd812d") == "6"

    def test_uppercase_and_no_hash_accepted(self):
        assert nearest_color_id("FF0000") == nearest_color_id("#ff0000")
        assert nearest_color_id("#FF0000") == nearest_color_id("#ff0000")

    @pytest.mark.parametrize("bad", [None, "", "#", "#abc", "#ggggggg", "not a color", "12345", "#1234567"])
    def test_garbage_returns_none(self, bad):
        assert nearest_color_id(bad) is None


class TestIsValidColorId:
    @pytest.mark.parametrize("cid", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"])
    def test_all_eleven_are_valid(self, cid):
        assert is_valid_color_id(cid)

    @pytest.mark.parametrize("bad", [None, "", "0", "12", "abc", 1, "1.0"])
    def test_invalid_inputs_rejected(self, bad):
        assert not is_valid_color_id(bad)


class TestEmojiForColorId:
    def test_each_palette_color_has_an_emoji(self):
        for cid in GOOGLE_EVENT_COLORS:
            assert emoji_for_color_id(cid) is not None
            assert emoji_for_color_id(cid) == EMOJI_FOR_COLOR_ID[cid]

    def test_unknown_color_id_returns_none(self):
        assert emoji_for_color_id("99") is None
        assert emoji_for_color_id(None) is None


def test_valid_color_ids_set_matches_palette_keys():
    """Sanity: VALID_COLOR_IDS must stay in sync with GOOGLE_EVENT_COLORS."""
    assert VALID_COLOR_IDS == frozenset(GOOGLE_EVENT_COLORS.keys())
